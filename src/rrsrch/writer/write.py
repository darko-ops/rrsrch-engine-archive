"""The writer: answer a question from ledger claims, one checked sentence at a time.

The model drafts; the gate decides. The model sees only numbered claims (never
the papers), and returns sentences that each cite the claims they rest on.
Every sentence then faces the citation gate against exactly the claims it
cites. Sentences that fail are kept with the rule that failed them and left
out of the report. Faithfulness is passed / proposed.

The verdict is returned as a field, not prose, so the report has no uncited
summary sentence: everything a reader sees is backed by claims.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field

import psycopg

from ..check import fields as fieldmod
from ..check import gate
from ..reader.llm import LLM
from .retrieve import Candidate, candidates

PROMPT_VERSION = "writer-v1"
VERDICTS = ("benefit", "no_difference", "harm", "safe", "mixed", "contested", "uncertain")

SYSTEM = """You write evidence summaries for a clinical question, using ONLY the numbered claims given.

Return a JSON object:
{"verdict": "...", "sentences": [{"text": "...", "cites": [claim ids]}]}

verdict: one of benefit, no_difference, harm, safe, mixed (differs by outcome or comparator), contested (studies disagree), uncertain (too little evidence).

Rules for sentences:
- Every sentence cites the claim ids it rests on, and says nothing those claims don't say.
- Use only numbers, outcome measures, comparators, cell types and time points that appear in the cited claims. Never compute or average numbers.
- Keep each finding's direction exactly. If a claim says a difference was not significant, say there was no significant difference.
- Report disagreement: when claims conflict, write a sentence for each side.
- Say when evidence comes from a single study. You may count studies ("two trials") only when the sentence cites exactly that many different studies.
- Do not name authors and do not put citation markers in the text; the cites field carries them.
- 3 to 10 sentences. Most important findings first."""

USER = "Question: {question}\n\nClaims:\n{claims}"

TRIAL_COUNT = re.compile(r"\b(?:trials?|studies|study|RCTs?|papers?)\b", re.I)


def format_claims(cands: list[Candidate]) -> str:
    lines = []
    for c in cands:
        tag = f"{c.source_id}, {c.year or 'n.d.'}{'' if c.full_text else ', abstract only'}"
        lines.append(f"[{c.id}] ({tag}) {c.text}")
    return "\n".join(lines)


def check_sentence(text: str, cited: list[Candidate]) -> gate.Verdict:
    """The gate, plus one writer rule: "N trials" is supported when N different studies are cited."""
    sf = fieldmod.extract(text)
    n_sources = len({c.source_id for c in cited})
    kept = tuple(n for n in sf.numbers
                 if not (n.kind == "count" and n.value == n_sources and TRIAL_COUNT.search(n.text)))
    sf = dataclasses.replace(sf, numbers=kept)
    return gate.check(text, [gate.CitedClaim(str(c.id), c.text, c.context) for c in cited],
                      sentence_fields=sf)


@dataclass
class Sentence:
    text: str
    cites: list[int]
    verdict: gate.Verdict


@dataclass
class Report:
    id: int | None
    question: str
    verdict: str | None
    sentences: list[Sentence] = field(default_factory=list)
    shown: list[Candidate] = field(default_factory=list)
    bad_reply: str | None = None

    @property
    def kept(self) -> list[Sentence]:
        return [s for s in self.sentences if s.verdict.passed]

    @property
    def faithfulness(self) -> float | None:
        return len(self.kept) / len(self.sentences) if self.sentences else None

    def render(self) -> str:
        by_id = {c.id: c for c in self.shown}
        out = []
        for s in self.kept:
            srcs = sorted({by_id[i].source_id for i in s.cites})
            out.append(f"{s.text} [{'; '.join(srcs)}]")
        return " ".join(out)


def write(conn: psycopg.Connection, llm: LLM, question: str, scope: dict | None = None,
          bench_id: str | None = None, limit: int = 60) -> Report:
    cands = candidates(conn, question, scope, limit=limit)
    rep = Report(None, question, None, shown=cands)
    by_id = {c.id: c for c in cands}
    if not cands:
        rep.bad_reply = "no claims in the ledger match the question"
    else:
        try:
            reply = llm.json_chat(SYSTEM, USER.format(question=question, claims=format_claims(cands)))
            items = reply.get("sentences")
            if not isinstance(items, list):
                raise ValueError("'sentences' is not a list")
            v = reply.get("verdict")
            rep.verdict = v if v in VERDICTS else None
        except (ValueError, KeyError) as e:
            rep.bad_reply, items = str(e)[:300], []
        for it in items:
            text = " ".join(str((it or {}).get("text") or "").split())
            if not text:
                continue
            try:
                cites = list(dict.fromkeys(int(x) for x in (it.get("cites") or [])))
            except (TypeError, ValueError):
                cites = []
            unknown = [i for i in cites if i not in by_id]
            if unknown:
                v = gate.Verdict(False, "unknown_claim", f"cites claims it was not shown: {unknown}")
            else:
                v = check_sentence(text, [by_id[i] for i in cites])
            rep.sentences.append(Sentence(text, cites, v))
    with conn.transaction():
        rep.id = conn.execute(
            "INSERT INTO reports (question, bench_id, verdict, writer, claims_shown, bad_reply) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (question, bench_id, rep.verdict, f"{llm.model}|{PROMPT_VERSION}", [c.id for c in cands],
             rep.bad_reply)).fetchone()[0]
        for pos, s in enumerate(rep.sentences):
            conn.execute(
                "INSERT INTO report_sentences (report_id, position, text, claim_ids, passed, rule, detail, "
                "check_log) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (rep.id, pos, s.text, s.cites, s.verdict.passed, s.verdict.rule, s.verdict.detail,
                 json.dumps(s.verdict.to_json())))
    return rep


# ── coverage: which expected points a report makes ──

JUDGE_VERSION = "coverage-v1"

JUDGE_SYSTEM = """You check whether a report makes each expected point.

Return a JSON object: {"made": [true or false for each point, in order]}

A point is made only if the report states it or something equivalent. A report that states the opposite, or is silent, does not make it."""


def judge_coverage(conn: psycopg.Connection, llm: LLM, rep: Report, points: list[str]) -> list[bool]:
    text = " ".join(s.text for s in rep.kept)
    if not text:
        made = [False] * len(points)
    else:
        listed = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(points))
        try:
            reply = llm.json_chat(JUDGE_SYSTEM, f"Report:\n{text}\n\nPoints:\n{listed}")
            made = [bool(x) for x in reply.get("made", [])]
        except (ValueError, KeyError):
            made = []
        if len(made) != len(points):
            made = [False] * len(points)         # an unusable judgement counts as nothing covered
    with conn.transaction():
        for p, m in zip(points, made):
            conn.execute("INSERT INTO report_coverage (report_id, point, made, judge) VALUES (%s, %s, %s, %s) "
                         "ON CONFLICT (report_id, point) DO UPDATE SET made = EXCLUDED.made",
                         (rep.id, p, m, f"{llm.model}|{JUDGE_VERSION}"))
    return made
