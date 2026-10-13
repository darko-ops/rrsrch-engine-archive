"""Package a writer report into the shape the site's 4a results UI expects.

Groups:
  benefit / no_difference / harm / safe  — findings from retrieved claims
  contested                              — skeptic contradictions among those claims

Sample data is never invented here: empty groups are omitted.
"""
from __future__ import annotations

from collections import defaultdict

import psycopg

from ..check import fields as fieldmod
from ..reader.llm import LLM
from ..skeptic import run as skeptic
from ..writer import write as writer
from ..writer.retrieve import Candidate

VERDICT_HEADLINE = {
    "benefit": "Benefit: the claims favour MSCs on the outcomes asked.",
    "no_difference": "No clear difference from control on the outcomes asked.",
    "harm": "Harm: the claims favour control or show worse outcomes with MSCs.",
    "safe": "Safety: no serious adverse events in the cited claims.",
    "mixed": "Mixed: pain and function (or comparators) don't all point the same way.",
    "contested": "Contested: studies disagree on the outcomes asked.",
    "uncertain": "Uncertain: too little matching evidence in the ledger yet.",
}

GROUP_ORDER = ("benefit", "no_difference", "contested", "harm", "safe")
GROUP_LABEL = {
    "benefit": "Benefit",
    "no_difference": "No difference",
    "contested": "Contested",
    "harm": "Harm",
    "safe": "Safe",
}
GROUP_COLOR = {
    "benefit": "#00ff88",
    "no_difference": "#aaaaaa",
    "contested": "#fab219",
    "harm": "#e07070",
    "safe": "#00ff88",
}


def _claim_effect(c: Candidate) -> str | None:
    f = fieldmod.extract(c.text)
    if not f.findings:
        return None
    # prefer a clinical effect over "safe" when both appear
    effects = [x.effect for x in f.findings]
    for want in ("benefit", "no_difference", "harm", "safe"):
        if want in effects:
            return want
    return effects[0]


def _groups(conn: psycopg.Connection, shown: list[Candidate]) -> list[dict]:
    by_effect: dict[str, list[Candidate]] = defaultdict(list)
    for c in shown:
        e = _claim_effect(c)
        if e:
            by_effect[e].append(c)

    shown_ids = {c.id for c in shown}
    contested_rows = []
    for row in skeptic.contradictions(conn):
        if row["claim_a_id"] in shown_ids and row["claim_b_id"] in shown_ids:
            contested_rows.append({
                "text": row["claim_a"],
                "b": row["claim_b"],
                "src": f"{row['source_a']} vs. {row['source_b']}",
            })

    out = []
    for key in GROUP_ORDER:
        if key == "contested":
            rows = contested_rows
            if not rows:
                continue
            n_studies = len({r["src"] for r in rows})
            label = GROUP_LABEL[key]
            out.append({
                "key": key,
                "label": label,
                "color": GROUP_COLOR[key],
                "big": str(len(rows)),
                "studiesPlain": f"disagreements · {n_studies} study pairs",
                "countPlain": f"{len(rows)} disagreement{'s' if len(rows) != 1 else ''} across shown claims",
                "blurb": "Claims the skeptic found pointing in opposite directions on a compatible outcome.",
                "rows": rows,
                "morePlain": None,
                "hidden": 0,
            })
            continue
        claims = by_effect.get(key) or []
        if not claims:
            continue
        studies = {c.source_id for c in claims}
        preview, rest = claims[:8], claims[8:]
        out.append({
            "key": key,
            "label": GROUP_LABEL[key],
            "color": GROUP_COLOR[key],
            "big": str(len(claims)),
            "studiesPlain": f"findings · {len(studies)} stud{'y' if len(studies) == 1 else 'ies'}",
            "countPlain": f"{len(claims)} findings from {len(studies)} stud{'y' if len(studies) == 1 else 'ies'}",
            "blurb": f"Ledger claims whose direction is {GROUP_LABEL[key].lower()}.",
            "rows": [{"text": c.text, "src": c.title or c.source_id, "b": None} for c in preview],
            "morePlain": f"Show {len(rest)} more findings" if rest else None,
            "hidden": len(rest),
            "extra": [{"text": c.text, "src": c.title or c.source_id, "b": None} for c in rest],
        })
    # renumber for 4a left column
    for i, g in enumerate(out):
        g["numPad"] = f"{i + 1:02d}"
    return out


def answer(conn: psycopg.Connection, llm: LLM, question: str,
           scope: dict | None = None) -> dict:
    """Run writer + skeptic packaging. Always returns a JSON-serialisable dict."""
    # refresh skeptic rows so contested bands reflect the current ledger
    try:
        skeptic.scan(conn)
    except Exception:  # noqa: BLE001 — empty table / missing migration shouldn't kill answers
        pass

    rep = writer.write(conn, llm, question, scope=scope)
    n_studies = len({c.source_id for c in rep.shown})
    kept = rep.kept
    refused = len(rep.sentences) - len(kept)
    groups = _groups(conn, rep.shown) if rep.shown else []

    if rep.bad_reply and not kept:
        headline = "No matching claims in the ledger yet."
        detail = rep.bad_reply
    else:
        headline = VERDICT_HEADLINE.get(rep.verdict or "", "Answer from the claim ledger.")
        detail = None

    return {
        "ok": True,
        "engine": "rrsrch",
        "question": question,
        "report_id": rep.id,
        "verdict": rep.verdict,
        "kicker": (
            f"Answer from {len(rep.shown)} finding{'s' if len(rep.shown) != 1 else ''} "
            f"in {n_studies} stud{'y' if n_studies == 1 else 'ies'}"
            if rep.shown else "No matching findings in the ledger"
        ),
        "headline": headline,
        "detail": detail,
        "sentences": [
            {"text": s.text, "cites": [str(i) for i in s.cites]}
            for s in kept
        ],
        "refused": refused,
        "groups": groups,
        "faithfulness": rep.faithfulness,
        "claims_shown": len(rep.shown),
        "gaps": None,
        "source": "ledger",
    }
