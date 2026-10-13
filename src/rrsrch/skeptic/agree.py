"""Compare two ledger claims: do they agree, contradict, or can't we tell?

No model. Verdicts come from the same field extraction the citation gate uses
(direction per outcome, entities, numbers as context). Two claims are only
judged against each other when they speak to a compatible question:

  - same report population (human knees with osteoarthritis by default),
  - at least one overlapping outcome (VAS pain vs pain counts; WOMAC vs MOCART
    does not),
  - compatible cell type and comparator when both name one (siblings under
    MSC count as compatible; HA vs placebo does not),
  - compatible follow-up when both state a time (12 months vs 52 weeks counts;
    3 months vs 24 months does not).

Then:

  agrees       same direction on an overlapping outcome
  contradicts  different directions on an overlapping outcome
               (benefit vs no_difference is the classic failed reproduction)
  couldnt_compare  not enough overlap, or no direction to compare

Every verdict names the rule that decided it and the findings compared, so a
person can check it by hand. Same-source pairs are allowed: a paper that
says both "pain improved" and "pain was not different" is a contradiction
the skeptic should surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..check import fields as fieldmod
from ..check import gate, vocab
from ..check.fields import Fields, Finding

RULES_VERSION = "skeptic-v1"
VERDICTS = ("agrees", "contradicts", "couldnt_compare")

# Directions that conflict when about the same outcome. "safe" only conflicts
# with "harm": a claim of safety does not speak to whether pain improved.
_CONFLICTS = frozenset({
    frozenset({"benefit", "no_difference"}),
    frozenset({"benefit", "harm"}),
    frozenset({"no_difference", "harm"}),
    frozenset({"safe", "harm"}),
})


@dataclass(frozen=True)
class ClaimView:
    """One claim, with the fields and source context the skeptic needs."""
    id: str
    text: str
    source_id: str = ""
    context: dict[str, frozenset[str]] = field(default_factory=dict)
    fields: Fields | None = None

    def extracted(self) -> Fields:
        return self.fields if self.fields is not None else fieldmod.extract(self.text)


@dataclass
class Relation:
    verdict: str                        # agrees | contradicts | couldnt_compare
    rule: str                           # the rule that decided
    detail: str
    evidence: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"verdict": self.verdict, "rule": self.rule, "detail": self.detail,
                "evidence": self.evidence}


def _effective(group: str, own: frozenset[str], ctx: dict[str, frozenset[str]]) -> frozenset[str]:
    """Claim entities sharpened by source context (same rule as the gate)."""
    if group not in gate.INHERITED:
        return own
    background = ctx.get(group, frozenset())
    if not own:
        return background
    sharper = {x for x in background if any(o in vocab.ancestors(group, x) for o in own)}
    return frozenset(sharper | {o for o in own if not any(o in vocab.ancestors(group, x) for x in sharper)})


def _widen(group: str, ids: frozenset[str]) -> set[str]:
    out = set(ids)
    for e in ids:
        out.update(vocab.ancestors(group, e))
    return out


def _overlap(group: str, a: frozenset[str], b: frozenset[str]) -> bool:
    """True when the sets share an id, or one is an ancestor of the other."""
    if not a or not b:
        return True                     # silence isn't a conflict
    return bool(_widen(group, a) & _widen(group, b))


def _outcomes_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False                    # a finding with no outcome can't be matched
    return bool(_widen("outcome", a) & _widen("outcome", b))


def _times_compatible(a: Fields, b: Fields) -> bool:
    ta = {n.value for n in a.numbers if n.kind == "time"}
    tb = {n.value for n in b.numbers if n.kind == "time"}
    if not ta or not tb:
        return True
    return any(abs(x - y) <= max(0.75, 0.1 * max(x, y)) for x in ta for y in tb)


def _effects_conflict(a: str, b: str) -> bool:
    return frozenset({a, b}) in _CONFLICTS


def _in_report_scope(f: Fields, ctx: dict[str, frozenset[str]],
                     report_scope: dict[str, str]) -> tuple[bool, str]:
    for group, wanted in report_scope.items():
        known = _effective(group, f.scope.get(group, frozenset()), ctx)
        if known and wanted not in known:
            return False, f"{group}={'/'.join(sorted(known))}, not {wanted}"
    return True, ""


def relate(a: ClaimView, b: ClaimView,
           report_scope: dict[str, str] | None = None) -> Relation:
    """How claim a stands next to claim b."""
    scope = report_scope if report_scope is not None else gate.REPORT_SCOPE
    fa, fb = a.extracted(), b.extracted()

    ok_a, why_a = _in_report_scope(fa, a.context, scope)
    ok_b, why_b = _in_report_scope(fb, b.context, scope)
    if not ok_a or not ok_b:
        return Relation("couldnt_compare", "scope",
                        f"claim {a.id if not ok_a else b.id} is about {why_a or why_b}",
                        {"a_scope": why_a or "ok", "b_scope": why_b or "ok"})

    if not fa.findings or not fb.findings:
        return Relation("couldnt_compare", "direction",
                        "one or both claims state no direction",
                        {"a_findings": len(fa.findings), "b_findings": len(fb.findings)})

    cells_a = _effective("cell_type", fa.entities.get("cell_type", frozenset()), a.context)
    cells_b = _effective("cell_type", fb.entities.get("cell_type", frozenset()), b.context)
    if not _overlap("cell_type", cells_a, cells_b):
        return Relation("couldnt_compare", "entities",
                        f"cell types differ: {', '.join(sorted(cells_a)) or 'none'} vs "
                        f"{', '.join(sorted(cells_b)) or 'none'}",
                        {"a_cell_type": sorted(cells_a), "b_cell_type": sorted(cells_b)})

    comps_a = fa.entities.get("comparator", frozenset())
    comps_b = fb.entities.get("comparator", frozenset())
    if comps_a and comps_b and not _overlap("comparator", comps_a, comps_b):
        return Relation("couldnt_compare", "entities",
                        f"comparators differ: {', '.join(sorted(comps_a))} vs "
                        f"{', '.join(sorted(comps_b))}",
                        {"a_comparator": sorted(comps_a), "b_comparator": sorted(comps_b)})

    if not _times_compatible(fa, fb):
        ta = sorted(n.value for n in fa.numbers if n.kind == "time")
        tb = sorted(n.value for n in fb.numbers if n.kind == "time")
        return Relation("couldnt_compare", "time",
                        f"follow-up times differ: {ta} vs {tb} months",
                        {"a_months": ta, "b_months": tb})

    conflicts: list[tuple[Finding, Finding]] = []
    agreements: list[tuple[Finding, Finding]] = []
    for fa_f in fa.findings:
        for fb_f in fb.findings:
            if not _outcomes_overlap(fa_f.outcomes, fb_f.outcomes):
                continue
            if _effects_conflict(fa_f.effect, fb_f.effect):
                conflicts.append((fa_f, fb_f))
            elif fa_f.effect == fb_f.effect:
                agreements.append((fa_f, fb_f))

    def describe(f: Finding) -> str:
        return f"{f.effect} for {', '.join(sorted(f.outcomes)) or 'unnamed'}"

    if conflicts:
        fa_f, fb_f = conflicts[0]
        return Relation(
            "contradicts", "direction",
            f"claim {a.id} says {describe(fa_f)}; claim {b.id} says {describe(fb_f)}",
            {"conflicts": [{"a": describe(x), "b": describe(y)} for x, y in conflicts],
             "agreements": [{"a": describe(x), "b": describe(y)} for x, y in agreements]},
        )
    if agreements:
        fa_f, fb_f = agreements[0]
        return Relation(
            "agrees", "direction",
            f"both say {describe(fa_f)}",
            {"agreements": [{"a": describe(x), "b": describe(y)} for x, y in agreements]},
        )
    return Relation(
        "couldnt_compare", "outcomes",
        "no overlapping outcome between the claims' findings",
        {"a_findings": [describe(f) for f in fa.findings],
         "b_findings": [describe(f) for f in fb.findings]},
    )
