"""The benchmark: questions with expected answers taken from the answer key.

Each question's expected answer rests on findings the meta-analyses report,
and every finding carries a quote that must appear in its meta-analysis
verbatim, so the answer key can be audited against the papers the same way the
ledger can. The meta-analyses are never evidence: rrsrch answers from the
studies they included, and its answer is scored against theirs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..corpus import manifest, snapshot, text
from ..reader import spans

DEFAULT_PATH = Path("benchmark/questions.yaml")

KINDS = {"efficacy", "comparator", "safety", "dose", "frequency", "cell_source", "structure",
         "certainty", "durability"}
EFFECTS = {"benefit", "no_difference", "harm", "safe", "mixed", "inconclusive", "bias", "no_bias"}
VERDICTS = {"benefit", "no_difference", "harm", "safe", "mixed", "contested", "uncertain"}


def load(path: Path = DEFAULT_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def meta_text(pmid: str) -> str | None:
    """Abstract and body of a meta-analysis snapshot, or None when it isn't fetched."""
    d = snapshot.record_dir(f"MED:{pmid}")
    rec_path = d / "record.json"
    if not rec_path.exists():
        return None
    rec = json.loads(rec_path.read_text())
    parts = [rec.get("title", ""), text.abstract_text(rec.get("abstractText"))]
    if (d / "fulltext.xml").exists():
        parts.append(text.fulltext_body((d / "fulltext.xml").read_bytes()))
    return "\n\n".join(parts)


@dataclass
class Problem:
    question: str
    detail: str


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    quotes_checked: int = 0
    quotes_unchecked: int = 0                       # meta-analysis not snapshotted here
    studies: dict[str, list[str]] = field(default_factory=dict)  # question id -> corpus studies


def check(bench: dict, corpus: dict | None = None, read_text=meta_text) -> Report:
    """Validate the benchmark against the manifest and the meta-analyses' own text."""
    corpus = corpus or manifest.load()
    key = {m["pmid"] for m in corpus["answer_key"]}
    rep = Report()
    seen: set[str] = set()
    texts: dict[str, str | None] = {}
    for q in bench["questions"]:
        qid = q.get("id", "?")
        bad = lambda d: rep.problems.append(Problem(qid, d))  # noqa: E731
        if qid in seen:
            bad("duplicate id")
        seen.add(qid)
        if q.get("kind") not in KINDS:
            bad(f"kind {q.get('kind')!r} not in {sorted(KINDS)}")
        exp = q.get("expected") or {}
        if exp.get("verdict") not in VERDICTS:
            bad(f"verdict {exp.get('verdict')!r} not in {sorted(VERDICTS)}")
        if not exp.get("answer") or not exp.get("points"):
            bad("expected answer needs an answer and at least one point")
        support = q.get("support") or []
        if not support:
            bad("no supporting findings from the answer key")
        effects = set()
        for i, s in enumerate(support):
            where = f"support[{i}]"
            if s.get("meta") not in key:
                bad(f"{where}: {s.get('meta')!r} is not in the answer key")
                continue
            if s.get("effect") not in EFFECTS:
                bad(f"{where}: effect {s.get('effect')!r} not in {sorted(EFFECTS)}")
            effects.add(s.get("effect"))
            if s["meta"] not in texts:
                texts[s["meta"]] = read_text(s["meta"])
            body = texts[s["meta"]]
            if body is None:
                rep.quotes_unchecked += 1
            elif spans.locate(s.get("quote", ""), body) is None:
                bad(f"{where}: quote not found verbatim in {s['meta']}: {s.get('quote', '')[:60]!r}")
            else:
                rep.quotes_checked += 1
        if exp.get("verdict") == "contested" and len(effects) < 2:
            bad("verdict is contested but every supporting finding agrees")
        metas = {s.get("meta") for s in support}
        rep.studies[qid] = sorted(st["id"] for st in corpus["studies"]
                                  if metas & set(st.get("included_by", [])))
    return rep
