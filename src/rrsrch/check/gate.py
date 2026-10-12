"""The citation gate: may this sentence stand on these claims?

Rules run in a fixed order, and the first one that fails decides. The verdict
names that rule and says why, so every rejection can be explained without
trusting a model:

  cites      the sentence cites at least one claim
  scope      every cited claim is about the report's population (by default
             human knees with osteoarthritis): no rat study, no hip study
  direction  each finding in the sentence (a direction about an outcome:
             benefit / no difference / harm / safe) is a finding of a cited
             claim about a compatible outcome, and no cited claim found the
             opposite about that outcome
  entities   every cell type, comparator and outcome the sentence names is
             named by a claim. A sentence may be more general than its claim
             ("pain" from "VAS pain"), never more specific
  numbers    every number in the sentence is in a claim, to the precision the
             sentence states; p-value inequalities are checked as inequalities

What rules can't catch (a subtle paraphrase that changes meaning) is measured
separately by the faithfulness score. The gate is the floor, not the ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import vocab
from .fields import Fields, Number, extract

REPORT_SCOPE = {"species": "human", "joint": "knee", "condition": "osteoarthritis"}


# What a claim inherits from its source when its own text is silent: the
# population, and the intervention ("the cell-recipient group improved" is a
# claim about MSCs because the study is). Outcomes are never inherited: a claim
# must name what it measured.
INHERITED = ("species", "joint", "condition", "cell_type")


@dataclass(frozen=True)
class CitedClaim:
    id: str
    text: str
    # what the claim's source establishes; see context_for
    context: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass
class Verdict:
    passed: bool
    rule: str                       # the rule that failed, or "pass"
    detail: str = ""
    log: list[tuple[str, bool, str]] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"passed": self.passed, "rule": self.rule, "detail": self.detail,
                "log": [{"rule": r, "ok": ok, "detail": d} for r, ok, d in self.log]}


def source_context(title: str, abstract: str) -> dict[str, frozenset[str]]:
    """What a source establishes for all its claims: population and intervention.

    Condition, joint and cell type come from the title and abstract
    together: a study of "cartilage defects" in "osteoarthritic knees" is
    about both. Species is stricter, because abstracts often mention animal
    work in their background: the title's species wins, and the abstract's is
    used only when it names exactly one.
    """
    from .vocab import find
    ctx = {}
    for g in INHERITED:
        t, a = find(title, g), find(abstract, g)
        if g == "species":
            ctx[g] = t or (a if len(a) == 1 else frozenset())
        else:
            ctx[g] = t | a
    return ctx


def _effective(group: str, own: frozenset[str], c: CitedClaim) -> frozenset[str]:
    """The claim's own entities, filled in or sharpened by its source's.

    Silent claim: inherits. Claim that says "MSCs" in an adipose-derived MSC
    trial: means the adipose-derived ones, so the more specific term wins.
    """
    if group not in INHERITED:
        return own
    ctx = c.context.get(group, frozenset())
    if not own:
        return ctx
    sharper = {x for x in ctx if any(o in vocab.ancestors(group, x) for o in own)}
    return frozenset(sharper | {o for o in own if not any(o in vocab.ancestors(group, x) for x in sharper)})


def _close(c: float, s: float, decimals: int, rel: float = 0.0) -> bool:
    """c, written to s's precision, is s. 7.65 supports "7.7" and "7.65", not "7.6"."""
    return abs(c - s) <= 0.5 * 10 ** -decimals + 1e-9 or (rel and abs(c - s) <= rel * abs(s))


def _number_supported(s: Number, claims: list[Number]) -> bool:
    for c in claims:
        if s.kind == "p":
            if c.kind != "p":
                continue
            if s.op == "<" and (c.op == "<" and c.value <= s.value or c.op == "=" and c.value < s.value):
                return True
            if s.op == ">" and c.op in (">", "=") and c.value >= s.value:
                return True
            if s.op == "=" and c.op == "=" and _close(c.value, s.value, s.decimals):
                return True
        elif s.kind == "time":
            if c.kind == "time" and _close(c.value, s.value, s.decimals, rel=0.05):
                return True
        elif s.kind == "percent":
            if c.kind == "percent" and _close(c.value, s.value, s.decimals):
                return True
        elif c.kind in ("count", "plain") and _close(c.value, s.value, s.decimals):
            return True
    return False


def _entity_supported(group: str, entity: str, claim_entities) -> bool:
    return any(entity in vocab.ancestors(group, c) for c in claim_entities)


def _outcomes_fit(sentence: frozenset[str], claim: frozenset[str]) -> bool:
    """The claim finding speaks to every outcome the sentence finding names."""
    return all(_entity_supported("outcome", o, claim) for o in sentence)


def _outcomes_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    """Same outcome family in either direction (VAS pain vs pain, WOMAC vs WOMAC pain)."""
    if not a or not b:
        return True
    return _outcomes_fit(a, b) or _outcomes_fit(b, a)


def check(sentence: str, claims: list[CitedClaim], report_scope: dict[str, str] | None = None,
          sentence_fields: Fields | None = None, claim_fields: dict[str, Fields] | None = None) -> Verdict:
    scope = report_scope if report_scope is not None else REPORT_SCOPE
    sf = sentence_fields or extract(sentence)
    cf = claim_fields or {c.id: extract(c.text) for c in claims}
    log: list[tuple[str, bool, str]] = []

    def fail(rule: str, detail: str) -> Verdict:
        log.append((rule, False, detail))
        return Verdict(False, rule, detail, log)

    if not claims:
        return fail("cites", "the sentence cites no claim")
    log.append(("cites", True, f"{len(claims)} claim(s)"))

    for c in claims:
        for group, wanted in scope.items():
            known = _effective(group, cf[c.id].scope.get(group, frozenset()), c)
            if known and wanted not in known:
                return fail("scope", f"claim {c.id} is about {group} {'/'.join(sorted(known))}, "
                                     f"not {wanted}")
    log.append(("scope", True, ", ".join(f"{g}={v}" for g, v in scope.items()) or "no scope set"))

    def describe(f) -> str:
        return f"{f.effect} for {', '.join(sorted(f.outcomes)) or 'unnamed outcomes'}"

    for f in sf.findings:
        support = [c.id for c in claims for g in cf[c.id].findings
                   if g.effect == f.effect and _outcomes_fit(f.outcomes, g.outcomes)]
        if not support:
            found = "; ".join(describe(g) for c in claims for g in cf[c.id].findings) or "no direction"
            return fail("direction", f"sentence says {describe(f)}; claims say {found}")
        if f.effect == "safe":
            continue
        for c in claims:
            for g in cf[c.id].findings:
                if g.effect in (f.effect, "safe") or not _outcomes_overlap(f.outcomes, g.outcomes):
                    continue
                # a sentence that reports the other side too isn't hiding it
                if any(h.effect == g.effect and _outcomes_overlap(h.outcomes, g.outcomes) for h in sf.findings):
                    continue
                return fail("direction", f"sentence says {describe(f)}; claim {c.id} says {describe(g)}")
    log.append(("direction", True, "; ".join(describe(f) for f in sf.findings) or "no direction stated"))

    for group in vocab.ENTITY_GROUPS:
        have = set().union(*(_effective(group, cf[c.id].entities.get(group, frozenset()), c)
                             for c in claims))
        for e in sorted(sf.entities.get(group, frozenset())):
            if not _entity_supported(group, e, have):
                return fail("entities", f"sentence names {group} '{e}'; claims name "
                                        f"{', '.join(sorted(have)) or 'none'}")
    log.append(("entities", True, "all named entities supported"))

    claim_numbers = [n for c in claims for n in cf[c.id].numbers]
    for n in sf.numbers:
        if not _number_supported(n, claim_numbers):
            return fail("numbers", f"'{n.text.strip()}' ({n.kind}) is not in any cited claim")
    log.append(("numbers", True, f"{len(sf.numbers)} number(s) supported"))

    return Verdict(True, "pass", "", log)
