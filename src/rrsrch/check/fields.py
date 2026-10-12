"""What the agreement rules compare: the checkable content of a piece of text.

From a sentence or a claim this pulls out, by rule:
  - numbers, each with a kind (time, percent, p-value, count, plain),
  - the direction of each clause (benefit, no difference, harm, safe),
  - the entities named (cell types, comparators, outcome measures),
  - the population (species, joint, condition).

Direction comes from the statistics first and the wording second. "Improved
(p = 0.06)" is read as no difference: a non-significant result described with
a benefit word is exactly the spin the gate exists to stop.

No model is involved anywhere in this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import vocab

# ── numbers ──────────────────────────────────────────────────────────────────

_WORD_NUMS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_WORD_NUMS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                   "eighty": 80, "ninety": 90, "hundred": 100})
_TENS = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
_WORD_NUM_RE = re.compile(rf"\b({_TENS}[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
                          + "|".join(sorted(_WORD_NUMS, key=len, reverse=True)) + r")\b", re.I)

_P_RE = re.compile(r"\b[pP]\s*(<|>|=|≤|≥|<=|>=)\s*(0?\.\d+|1(?:\.0+)?)")
_CI_LABEL_RE = re.compile(r"\b9[05]\s*%\s*(?:CI|confidence intervals?)\b", re.I)
_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|\s)?(days?|weeks?|wks?|months?|mos?|years?|yrs?)\b", re.I)
_TIME_AFTER_RE = re.compile(r"\b(day|week|month|year)\s+(\d+(?:\.\d+)?)\b", re.I)     # "at month 12"
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_COUNT_RE = re.compile(r"\b[nN]\s*=\s*(\d+)\b|(\d+)\s+(?:patients|knees|participants|subjects|"
                       r"people|individuals|trials|studies|RCTs)\b")
_NUM_RE = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.]*\d)")
_YEAR_RE = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")
_REF_RE = re.compile(r"\[\d+(?:[,–-]\s*\d+)*\]")
_MONTHS_PER = {"d": 1 / 30.44, "w": 12 / 52.18, "m": 1.0, "y": 12.0}


@dataclass(frozen=True)
class Number:
    kind: str           # time | percent | p | count | plain
    value: float        # time is in months
    op: str = "="       # for p-values: < > =
    text: str = ""
    decimals: int = 0   # precision as written, for rounding comparisons


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def _words_to_digits(text: str) -> str:
    def conv(m: re.Match) -> str:
        w = m.group(1).lower().replace("-", " ").split()
        return str(sum(_WORD_NUMS[x] for x in w))
    return _WORD_NUM_RE.sub(conv, text)


def numbers(text: str) -> tuple[Number, ...]:
    out: list[Number] = []
    t = _THOUSANDS_RE.sub("", text)                       # "1,066" is one number
    t = _CI_LABEL_RE.sub(" ", _REF_RE.sub(" ", _words_to_digits(t)))

    def take(regex: re.Pattern, make) -> None:
        nonlocal t
        def sub(m: re.Match) -> str:
            n = make(m)
            if n is not None:
                out.append(n)
            return " " * len(m.group(0))
        t = regex.sub(sub, t)

    take(_P_RE, lambda m: Number("p", float(m.group(2)), {"≤": "<", "<=": "<", "≥": ">", ">=": ">"}
                                 .get(m.group(1), m.group(1)), m.group(0), _decimals(m.group(2))))
    take(_TIME_RE, lambda m: Number("time", float(m.group(1)) * _MONTHS_PER[m.group(2)[0].lower()],
                                    text=m.group(0), decimals=_decimals(m.group(1))))
    take(_TIME_AFTER_RE, lambda m: Number("time", float(m.group(2)) * _MONTHS_PER[m.group(1)[0].lower()],
                                          text=m.group(0), decimals=_decimals(m.group(2))))
    take(_PCT_RE, lambda m: Number("percent", float(m.group(1)), text=m.group(0),
                                   decimals=_decimals(m.group(1))))
    take(_COUNT_RE, lambda m: Number("count", float(m.group(1) or m.group(2)), text=m.group(0)))
    # names with digits in them ("T2 mapping", "SF-36") are vocabulary, not numbers
    t = _YEAR_RE.sub(" ", vocab.mask_all(t))
    take(_NUM_RE, lambda m: Number("plain", float(m.group(1)), text=m.group(1),
                                   decimals=_decimals(m.group(1))))
    return tuple(out)


# ── direction ────────────────────────────────────────────────────────────────

_NO_DIFF = re.compile(
    r"\bno (?:statistically |clinically )?(?:significant|meaningful|relevant|notable) "
    r"(?:\w+ )?(?:differences?|changes?|improvements?|effects?|benefits?|reductions?)"
    r"|\bnot (?:statistically |clinically )?significant(?:ly)?\b|\bnon-?significant(?:ly)?\b"
    r"|\bdid not (?:differ|improve|reach|show|result|lead)|\bfailed to\b|\bno differences?\b"
    r"|\bwere (?:similar|comparable)\b|\bwas (?:similar|comparable)\b|\bcomparable (?:to|with|between)\b"
    r"|\bnon-?inferior|\bequivalent\b|\bno better than\b|\bsimilar (?:to|between)\b"
    r"|\bn\.s\.|\btrend(?:ed)? (?:toward|towards)\b|\bno different from\b"
    r"|\bneither (?:\w+ )?(?:improvement|change|benefit)|\bno (?:further )?(?:improvement|change|progression)\b", re.I)
_HARM = re.compile(
    r"\bworse(?:ned)?\b|\bdeteriorat\w*|\binferior\b|\bincreased? (?:the )?pain\b"
    r"|\b(?:greater|higher|more) pain\b"
    r"|\b(?:caused|causing|associated with|led to|resulted in|leading to) (?:\w+ ){0,3}"
    r"(?:adverse|complications?|pain|swelling|effusions?|infections?)\b"
    r"|\b(?:incidences?|rates?|frequency|frequencies) of (?:[\w-]+ ){0,8}(?:was|were) "
    r"(?:significantly |markedly )?(?:higher|greater|increased|more frequent)", re.I)
_SAFE = re.compile(
    r"\bno (?:serious |severe |major |treatment-related )?(?:adverse (?:events?|effects?|reactions?)"
    r"|complications?)\b|\bwell[- ]tolerated\b|\b(?:was|were|is|are) safe\b|\bsafe and\b", re.I)
_BENEFIT = re.compile(
    r"\bimprov\w*|\bbetter\b|\bsuperior\b|\bgreater (?:improvement|reduction|increase|decrease)"
    r"|\b(?:reduc|decreas|alleviat|reliev)\w* (?:[\w-]+ ){0,2}(?:pain|symptoms|stiffness|defect|areas)"
    r"|\bpain relief\b|\bregenerat\w*|\beffective\b|\befficacious\b|\bfavou?r(?:ed|able)\b"
    r"|\bincreas\w* (?:\w+ ){0,2}cartilage\b|\brepair(?:ed)?\b|\bbenefi(?:t|cial)\b|\brestor\w*", re.I)

_CLAUSE_SPLIT = re.compile(r"[.;]\s+|\s+(?:but|whereas|however|while)\s+|,\s*(?:but|whereas|however)\s+", re.I)
_SUBORDINATE = re.compile(r"^(?:although|though|while|whereas|despite)\b", re.I)


def clauses(text: str) -> list[str]:
    """Split into clauses that each carry one finding. "Although A was not
    different, B was better" becomes [A ..., B ...]."""
    out = []
    for c in _CLAUSE_SPLIT.split(text):
        c = c.strip()
        if _SUBORDINATE.match(c) and "," in c:
            head, tail = c.split(",", 1)
            out += [head.strip(), tail.strip()]
        elif c:
            out.append(c)
    return out


def clause_effect(clause: str) -> str | None:
    """benefit | no_difference | harm | safe | None, for one clause."""
    if _SAFE.search(clause):
        return "safe"
    if _NO_DIFF.search(clause):
        return "no_difference"
    if _HARM.search(clause):
        return "harm"
    if _BENEFIT.search(clause):
        ps = [n for n in numbers(clause) if n.kind == "p"]
        significant = any(n.op == "<" and n.value <= 0.05 or n.op == "=" and n.value < 0.05 for n in ps)
        if ps and not significant:
            return "no_difference"      # benefit words over a non-significant p: spin
        return "benefit"
    return None


# ── all together ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """One clause's direction, and the outcome measures that clause is about.
    Pairing them is what stops "histology improved" from supporting "clinical
    outcomes improved" when the same claim says clinical outcomes didn't."""
    effect: str
    outcomes: frozenset[str]


@dataclass(frozen=True)
class Fields:
    numbers: tuple[Number, ...] = ()
    findings: tuple[Finding, ...] = ()
    entities: dict[str, frozenset[str]] = field(default_factory=dict)
    scope: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def effects(self) -> frozenset[str]:
        return frozenset(f.effect for f in self.findings)

    def to_json(self) -> dict:
        return {"numbers": [{"kind": n.kind, "value": n.value, "op": n.op, "text": n.text}
                            for n in self.numbers],
                "findings": [{"effect": f.effect, "outcomes": sorted(f.outcomes)} for f in self.findings],
                "entities": {g: sorted(v) for g, v in self.entities.items() if v},
                "scope": {g: sorted(v) for g, v in self.scope.items() if v}}


def extract(text: str) -> Fields:
    outcomes = vocab.find(text, "outcome")
    findings = []
    for c in clauses(text):
        if e := clause_effect(c):
            # a clause that names no outcome is about the text's outcomes as a whole
            findings.append(Finding(e, vocab.find(c, "outcome") or outcomes))
    return Fields(
        numbers=numbers(text),
        findings=tuple(dict.fromkeys(findings)),
        entities={g: vocab.find(text, g) for g in vocab.ENTITY_GROUPS},
        scope={g: vocab.find(text, g) for g in vocab.SCOPE_GROUPS},
    )
