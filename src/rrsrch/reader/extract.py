"""The reader: turn documents into claims, each pinned to its exact passage.

The model proposes; rules decide. For every claim the model returns:

  1. its quote must be found verbatim in the document (spans.locate), and
  2. the claim must pass the citation gate against its own quote: no number,
     direction, outcome or cell type the quote doesn't contain.

A claim that fails either check is kept in claim_rejections with the rule that
rejected it, never in claims. The rejection rate is reported, not hidden.

Only parts of a paper that report the study's own work are read: the abstract,
and the full text's methods, results and conclusions. Introductions and
discussions are skipped, because their sentences are mostly about other
studies and would be misattributed to this one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import psycopg

from ..check import fields as fieldmod
from ..check import gate
from . import spans
from .llm import LLM

PROMPT_VERSION = "reader-v1"

SYSTEM = """You extract findings from one clinical study into an evidence ledger.

Return a JSON object: {"claims": [{"quote": "...", "claim": "..."}]}

Rules:
- Only what THIS study did and found: results, and design facts such as sample size, groups, dose and follow-up. Never background, and never results of other studies.
- "quote": one or two consecutive sentences copied exactly from the text, character for character.
- "claim": one finding, restated plainly as a single sentence. Use only the numbers, outcome measures, groups and direction that appear in the quote. Name the outcome measure and what was compared. If a result was not statistically significant, the claim must say so.
- Split compound results into separate claims, one finding each.
- If the text has no findings of this study, return {"claims": []}.

Example text: "At 12 months, WOMAC scores improved more in the MSC group than with hyaluronic acid (p = 0.01), while VAS pain did not differ (p = 0.41)."
Example output: {"claims": [
 {"quote": "At 12 months, WOMAC scores improved more in the MSC group than with hyaluronic acid (p = 0.01), while VAS pain did not differ (p = 0.41).", "claim": "At 12 months, WOMAC scores improved more with MSCs than with hyaluronic acid (p = 0.01)."},
 {"quote": "At 12 months, WOMAC scores improved more in the MSC group than with hyaluronic acid (p = 0.01), while VAS pain did not differ (p = 0.41).", "claim": "At 12 months, VAS pain did not differ between MSCs and hyaluronic acid (p = 0.41)."}
]}"""

USER = "Study: {title}\n\nText:\n{text}"

READ_SECTION = re.compile(r"method|material|patient|participant|design|result|outcome|finding|"
                          r"conclusion|efficacy|safety|follow", re.I)
SKIP_SECTION = re.compile(r"introduction|background|discussion|limitation|reference|acknowledg|"
                          r"funding|conflict|abbreviation|supplementary|author", re.I)
MAX_CHUNK = 3500


@dataclass
class Chunk:
    start: int
    end: int
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


def chunks(part: str, text: str) -> list[Chunk]:
    """The readable stretches of a document, as offsets into its exact text."""
    if part == "abstract":
        return [Chunk(0, len(text), text)] if text else []
    if part != "fulltext":
        return []
    out: list[Chunk] = []
    reading, cur_start, cur_end, pos = False, None, None, 0
    for block in text.split("\n\n"):
        b_start, b_end = pos, pos + len(block)
        pos = b_end + 2
        is_title = len(block) < 90 and not block.rstrip().endswith((".", ":", ")")) and "\n" not in block
        if is_title:
            if SKIP_SECTION.search(block):
                reading = False
            elif READ_SECTION.search(block):
                reading = True
            continue
        if not reading:
            continue
        if cur_start is not None and b_end - cur_start > MAX_CHUNK:
            out.append(Chunk(cur_start, cur_end, text[cur_start:cur_end]))
            cur_start = None
        if cur_start is None:
            cur_start = b_start
        cur_end = b_end
    if cur_start is not None:
        out.append(Chunk(cur_start, cur_end, text[cur_start:cur_end]))
    return out


@dataclass
class ReadReport:
    chunks_read: int = 0
    chunks_skipped: int = 0
    claims: int = 0
    rejected: dict[str, int] = field(default_factory=dict)


def _reject(conn, doc_id, doc_sha, quote, claim, reason, detail, extractor, rep: ReadReport) -> None:
    conn.execute("INSERT INTO claim_rejections (document_id, doc_sha256, quote, claim_text, reason, "
                 "detail, extractor) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (doc_id, doc_sha, quote, claim, reason, detail, extractor))
    rep.rejected[reason] = rep.rejected.get(reason, 0) + 1


def read_document(conn: psycopg.Connection, llm: LLM, doc: dict, context: dict, rep: ReadReport) -> None:
    extractor = f"{llm.model}|{PROMPT_VERSION}"
    for ch in chunks(doc["part"], doc["text"]):
        done = conn.execute("SELECT 1 FROM read_chunks WHERE document_id = %s AND chunk_sha = %s "
                            "AND extractor = %s", (doc["id"], ch.hash, extractor)).fetchone()
        if done:
            rep.chunks_skipped += 1
            continue
        try:
            reply = llm.json_chat(SYSTEM, USER.format(title=doc["title"], text=ch.text))
            items = reply.get("claims", [])
            if not isinstance(items, list):
                raise ValueError("'claims' is not a list")
        except (ValueError, KeyError) as e:
            with conn.transaction():
                _reject(conn, doc["id"], doc["sha256"], None, None, "bad_reply", str(e)[:300], extractor, rep)
            continue
        with conn.transaction():
            n = 0
            for it in items:
                quote = str((it or {}).get("quote") or "")
                claim = " ".join(str((it or {}).get("claim") or "").split())
                if not claim:
                    _reject(conn, doc["id"], doc["sha256"], quote, claim, "empty_claim", "", extractor, rep)
                    continue
                span = spans.locate(quote, doc["text"], ch.start, ch.end)
                if span is None:
                    _reject(conn, doc["id"], doc["sha256"], quote, claim, "quote_not_found",
                            "quote is not in the text verbatim", extractor, rep)
                    continue
                passage = doc["text"][span[0]:span[1]]
                cf = fieldmod.extract(claim)
                if not (cf.numbers or cf.findings or any(cf.entities.values())):
                    _reject(conn, doc["id"], doc["sha256"], quote, claim, "nothing_checkable",
                            "claim has no number, direction or named entity", extractor, rep)
                    continue
                # the claim may not say more than its own passage: same gate the writer faces,
                # minus the report's population (a claim can be about anyone; scope is checked at use)
                v = gate.check(claim, [gate.CitedClaim("passage", passage, context)], report_scope={},
                               sentence_fields=cf)
                if not v.passed:
                    _reject(conn, doc["id"], doc["sha256"], quote, claim, f"gate:{v.rule}", v.detail,
                            extractor, rep)
                    continue
                conn.execute(
                    "INSERT INTO claims (document_id, doc_sha256, span_start, span_end, text, fields, "
                    "scope, extractor, self_check) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (doc["id"], doc["sha256"], span[0], span[1], claim, json.dumps(cf.to_json()),
                     json.dumps({g: sorted(cf.scope.get(g) or context.get(g, ())) for g in
                                 ("species", "joint", "condition")}),
                     extractor, json.dumps(v.to_json())))
                n += 1
            conn.execute("INSERT INTO read_chunks (document_id, chunk_sha, extractor, span_start, span_end, "
                         "n_claims, n_items) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (doc["id"], ch.hash, extractor, ch.start, ch.end, n, len(items)))
        rep.chunks_read += 1
        rep.claims += n


def read_corpus(conn: psycopg.Connection, llm: LLM, source_ids: list[str] | None = None,
                limit: int | None = None, progress=print) -> ReadReport:
    rep = ReadReport()
    rows = conn.execute(
        "SELECT d.id, d.source_id, d.part, d.text, d.sha256, s.title, "
        "  (SELECT text FROM documents a WHERE a.source_id = d.source_id AND a.part = 'abstract') "
        "FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE d.part IN ('abstract', 'fulltext') AND (%s::text[] IS NULL OR d.source_id = ANY(%s)) "
        "ORDER BY d.source_id, d.part", (source_ids, source_ids)).fetchall()
    sources = list(dict.fromkeys(r[1] for r in rows))[:limit]
    for i, sid in enumerate(sources, 1):
        for r in (r for r in rows if r[1] == sid):
            doc = {"id": r[0], "source_id": r[1], "part": r[2], "text": r[3], "sha256": r[4], "title": r[5]}
            context = gate.source_context(r[5], r[6] or "")
            read_document(conn, llm, doc, context, rep)
        progress(f"[{i}/{len(sources)}] {sid}: {rep.claims} claims so far, rejected {rep.rejected}")
    return rep
