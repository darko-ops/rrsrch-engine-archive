"""Scan the ledger for claim pairs that agree or contradict.

Compares every pair of claims that share enough vocabulary to be worth
judging (an overlapping outcome, after scope filtering), so the work is
O(comparable pairs), not O(n²) over the whole ledger. Results land in
`claim_agreements`; only `agrees` and `contradicts` are stored.
`couldnt_compare` is the default silence, not a row.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from ..check import fields as fieldmod
from ..check import gate, vocab
from ..writer import retrieve
from . import agree


@dataclass
class ScanReport:
    claims: int = 0
    pairs_compared: int = 0
    stored: dict[str, int] = field(default_factory=dict)   # agrees / contradicts
    skipped_same_text: int = 0


def _load_claims(conn: psycopg.Connection) -> list[agree.ClaimView]:
    rows = conn.execute(
        "SELECT c.id, c.text, c.fields, c.scope, s.id, s.title, "
        "  (SELECT text FROM documents a WHERE a.source_id = s.id AND a.part = 'abstract') "
        "FROM claims c JOIN documents d ON d.id = c.document_id JOIN sources s ON s.id = d.source_id "
        "ORDER BY c.id").fetchall()
    contexts: dict[str, dict] = {}
    out: list[agree.ClaimView] = []
    for cid, text, flds, cscope, sid, title, abstract in rows:
        if sid not in contexts:
            contexts[sid] = gate.source_context(title, abstract or "")
        # prefer stored fields when present; fall back to re-extract
        extracted = None
        if flds:
            try:
                findings = tuple(fieldmod.Finding(f["effect"], frozenset(f.get("outcomes") or ()))
                                 for f in flds.get("findings") or ())
                numbers = tuple(fieldmod.Number(n["kind"], n["value"], n.get("op", "="), n.get("text", ""))
                                for n in flds.get("numbers") or ())
                entities = {g: frozenset(v) for g, v in (flds.get("entities") or {}).items()}
                scope = {g: frozenset(v) for g, v in (flds.get("scope") or cscope or {}).items()}
                extracted = fieldmod.Fields(numbers=numbers, findings=findings,
                                            entities=entities, scope=scope)
            except (KeyError, TypeError, ValueError):
                extracted = None
        out.append(agree.ClaimView(str(cid), text, sid, contexts[sid], extracted))
    return out


def _outcome_keys(c: agree.ClaimView) -> set[str]:
    """Outcome ids and their ancestors — the buckets this claim can be paired in."""
    f = c.extracted()
    keys: set[str] = set()
    for finding in f.findings:
        for o in finding.outcomes:
            keys.update(vocab.ancestors("outcome", o))
    # also bare entities, for claims whose findings didn't pair an outcome
    for o in f.entities.get("outcome", ()):
        keys.update(vocab.ancestors("outcome", o))
    return keys


def scan(conn: psycopg.Connection) -> ScanReport:
    """Compare comparable claim pairs; replace previous skeptic rows."""
    claims = _load_claims(conn)
    rep = ScanReport(claims=len(claims))
    if len(claims) < 2:
        conn.execute("DELETE FROM claim_agreements")
        return rep

    # bucket by outcome ancestor so we only compare claims that could overlap
    buckets: dict[str, list[agree.ClaimView]] = {}
    for c in claims:
        keys = _outcome_keys(c)
        if not keys:
            continue
        # drop claims outside the report population early
        f = c.extracted()
        ok, _ = agree._in_report_scope(f, c.context, gate.REPORT_SCOPE)
        if not ok:
            continue
        for k in keys:
            buckets.setdefault(k, []).append(c)

    seen_pairs: set[tuple[int, int]] = set()
    results: list[tuple[int, int, agree.Relation]] = []
    for bucket in buckets.values():
        # unique by id inside the bucket
        uniq = list({c.id: c for c in bucket}.values())
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                ai, bi = int(a.id), int(b.id)
                key = (min(ai, bi), max(ai, bi))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                # near-duplicate text from the same paper isn't a finding
                if a.source_id == b.source_id and retrieve.words(a.text) == retrieve.words(b.text):
                    rep.skipped_same_text += 1
                    continue
                rel = agree.relate(a, b)
                rep.pairs_compared += 1
                if rel.verdict in ("agrees", "contradicts"):
                    results.append((key[0], key[1], rel))

    with conn.transaction():
        conn.execute("DELETE FROM claim_agreements")
        for a_id, b_id, rel in results:
            conn.execute(
                "INSERT INTO claim_agreements "
                "(claim_a_id, claim_b_id, verdict, rule, detail, evidence, rules) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
                (a_id, b_id, rel.verdict, rel.rule, rel.detail,
                 json.dumps(rel.evidence), agree.RULES_VERSION))
    rep.stored = dict(Counter(r.verdict for _, _, r in results))
    return rep


def contradictions(conn: psycopg.Connection) -> list[dict]:
    """Stored contradictions with both claim texts, for display."""
    rows = conn.execute(
        "SELECT r.id, r.claim_a_id, r.claim_b_id, r.verdict, r.rule, r.detail, "
        "  a.text, b.text, sa.id, sb.id "
        "FROM claim_agreements r "
        "JOIN claims a ON a.id = r.claim_a_id "
        "JOIN claims b ON b.id = r.claim_b_id "
        "JOIN documents da ON da.id = a.document_id JOIN sources sa ON sa.id = da.source_id "
        "JOIN documents db ON db.id = b.document_id JOIN sources sb ON sb.id = db.source_id "
        "WHERE r.verdict = 'contradicts' "
        "ORDER BY r.id").fetchall()
    return [{"id": rid, "claim_a_id": aid, "claim_b_id": bid, "verdict": v, "rule": rule,
             "detail": detail, "claim_a": ta, "claim_b": tb, "source_a": sa, "source_b": sb}
            for rid, aid, bid, v, rule, detail, ta, tb, sa, sb in rows]
