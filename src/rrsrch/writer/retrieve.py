"""Which ledger claims a question is about.

No embeddings yet: claims are ranked by the clinical vocabulary they share
with the question (outcomes, including their parents, comparators, cell
types) and by follow-up time. That keeps retrieval explainable, and the
vocabulary is the same one the gate checks with. Claims about another
population (animals, another joint) are dropped here, since the gate would
refuse any sentence that cites them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg

from ..check import fields as fieldmod
from ..check import gate, vocab


@dataclass
class Candidate:
    id: int
    text: str
    source_id: str
    title: str
    year: int | None
    full_text: bool
    context: dict
    score: float = 0.0


STOP = frozenset("""a an and are as at be between by compared control did do does for from has have how in into is
it its knee mscs msc of on or osteoarthritis than that the their there this to treatment was were what which
with work works better more less after injection injections patients""".split())


def words(text: str) -> set[str]:
    """Content words, lightly stemmed, for a tie-breaker the vocabulary doesn't cover (dose, repeated)."""
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP}


def _widen(group: str, ids) -> set[str]:
    out = set(ids)
    for e in ids:
        out.update(vocab.ancestors(group, e))
    return out


def question_terms(question: str, scope: dict | None = None) -> dict[str, set[str]]:
    """Vocabulary the question names, plus the benchmark's scope for it."""
    f = fieldmod.extract(question)
    terms = {g: set(f.entities.get(g, ())) for g in vocab.ENTITY_GROUPS}
    for g in vocab.ENTITY_GROUPS:
        terms[g] |= set((scope or {}).get(g, ()))
    months = (scope or {}).get("months")
    terms["months"] = {float(months)} if months else {n.value for n in f.numbers if n.kind == "time"}
    terms["words"] = words(question)
    return terms


def _in_scope(c: Candidate, claim_scope: dict) -> bool:
    for group, wanted in gate.REPORT_SCOPE.items():
        known = set(claim_scope.get(group) or ()) or set(c.context.get(group, ()))
        if known and wanted not in known:
            return False
    return True


def candidates(conn: psycopg.Connection, question: str, scope: dict | None = None,
               limit: int = 60, per_source: int = 6) -> list[Candidate]:
    terms = question_terms(question, scope)
    rows = conn.execute(
        "SELECT c.id, c.text, c.fields, c.scope, s.id, s.title, s.pub_year, s.has_full_text, "
        "  (SELECT text FROM documents a WHERE a.source_id = s.id AND a.part = 'abstract') "
        "FROM claims c JOIN documents d ON d.id = c.document_id JOIN sources s ON s.id = d.source_id "
        "ORDER BY c.id").fetchall()
    contexts: dict[str, dict] = {}
    scored: list[Candidate] = []
    for cid, text, flds, cscope, sid, title, year, full, abstract in rows:
        if sid not in contexts:
            contexts[sid] = gate.source_context(title, abstract or "")
        c = Candidate(cid, text, sid, title, year, full, contexts[sid])
        if not _in_scope(c, cscope or {}):
            continue
        ents = (flds or {}).get("entities", {})
        score, topical = 0.0, set()
        for g, w in (("outcome", 3.0), ("comparator", 2.0), ("cell_type", 2.0)):
            want = terms[g]
            if not want:
                continue
            have = set(ents.get(g, ()))
            if g == "cell_type":
                have |= set(c.context.get("cell_type", ()))   # "MSCs" in a study of adipose MSCs
            if want & have:
                score += w
            elif _widen(g, have) & want or _widen(g, want) & have:
                score += w / 2                                  # parent or child of what was asked
            else:
                continue
            topical.add(g)
        times = {n["value"] for n in (flds or {}).get("numbers", []) if n["kind"] == "time"}
        if terms["months"] and any(abs(t - m) < 0.5 for t in times for m in terms["months"]):
            score += 1.5
            topical.add("months")
        shared = len(terms["words"] & words(text))
        if shared:
            score += min(shared, 3) * 0.5
            topical.add("words")
        if (flds or {}).get("findings"):
            score += 1.0                     # a result, not just a design fact
        # a claim must share what the question is about (outcome, comparator, follow-up);
        # cell type alone matches nearly every claim, since claims inherit their study's
        asked = {g for g in ("outcome", "comparator", "months") if terms[g]} or {"words", "cell_type"}
        c.score = score
        if topical & asked:
            scored.append(c)
    scored.sort(key=lambda c: (-c.score, c.id))
    out, per = [], {}
    for c in scored:
        if per.get(c.source_id, 0) >= per_source:
            continue
        per[c.source_id] = per.get(c.source_id, 0) + 1
        out.append(c)
        if len(out) >= limit:
            break
    return out
