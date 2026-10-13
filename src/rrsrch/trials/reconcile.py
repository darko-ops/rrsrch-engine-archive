"""Trial reconciliation: what a trial registered against what its paper reported.

A ClinicalTrials.gov registration is what a team said it would measure; the
publication is what it reported. Each (trial, paper) pair gets one of four
neutral states:

  matched               the paper's declared primary outcome is the registered one,
                        at a registered time point
  endpoint_drift        the paper's primary outcome was not registered as primary,
                        a registered primary outcome is missing from the full text,
                        or the primary outcome is reported at an unregistered time
  no_publication_found  only for a registration with no linked paper, completed more
                        than 24 months before the snapshot (the strict rule)
  couldnt_match         the rules can't decide: the paper doesn't say which outcome
                        was primary, only its abstract is open, or the registered
                        outcome names nothing in the vocabulary

Every state carries its reasons and the evidence behind them: registry fields
by path, and passages by document offset. Two facts are recorded alongside the
state rather than folded into it: registration after enrolment began, and a
registry record edited after the paper's publication year (the current record
is compared, and an edit can hide drift; ClinicalTrials.gov's version history
isn't available to outside callers).

Only ClinicalTrials.gov is searched. Trials registered elsewhere (EU CTR,
ChiCTR, IRCT, ...) are not reconciled, and every report says so.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import psycopg

from ..check import vocab
from ..corpus import snapshot
from ..reader.extract import chunks

RULES_VERSION = "reconcile-v1"
STATES = ("matched", "endpoint_drift", "no_publication_found", "couldnt_match")
NO_PUBLICATION_MONTHS = 24

PRIMARY = re.compile(r"\bprimary (?:efficacy |safety )?(?:end[- ]?points?|outcomes?(?: measures?)?|"
                     r"objectives?|aims?)\b", re.I)
# a sentence that talks about analysing or powering the primary outcome doesn't say what it was
ANALYSIS = re.compile(r"\b(?:sample size|power(?:ed)?|analy[sz]\w*|fisher|t-test|regression|imputation)\b", re.I)
SECONDARY = re.compile(r"\bsecondary\b", re.I)
FAMILIES = frozenset({"clinical_score", "structural"})   # "PROMs": neither confirms nor contradicts
# words a registered measure's name shares with any outcome text; they say nothing about which outcome
GENERIC_WORDS = frozenset("""change changes baseline score scores value values index scale measure measured
number participants patients subjects follow assessment assessed evaluation evaluated degree improvement total
visit visits treatment period month months week weeks year years using level levels""".split())
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n\n")

_UNIT = {"d": 1 / 30.4375, "w": 7 / 30.4375, "m": 1.0, "y": 12.0}
_N = r"\d+(?:\.\d+)?"
_LIST = rf"{_N}(?:\s*(?:,|and|or|to|-|–)\s*{_N})*"
_UNIT_FIRST = re.compile(rf"\b(days?|weeks?|months?|years?|wk|mo)\s+({_LIST})", re.I)
_UNIT_AFTER = re.compile(rf"({_LIST})\s*-?\s*(days?|weeks?|months?|years?|wks?|mos?)\b", re.I)
_UP_TO = re.compile(rf"\b(?:up to|through|until|within)\s+(?:\w+\s+)?({_N})\s*-?\s*(days?|weeks?|months?|years?)\b",
                    re.I)


@dataclass
class Times:
    points: set[float] = field(default_factory=set)        # months
    spans: list[tuple[float, float]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.points or self.spans)

    def covers(self, t: float) -> bool:
        tol = lambda x: max(0.75, 0.1 * x)  # noqa: E731  24 weeks is 6 months
        return (any(abs(t - p) <= tol(p) for p in self.points)
                or any(a - tol(a) <= t <= b + tol(b) for a, b in self.spans))

    def describe(self) -> str:
        parts = [f"{p:g}" for p in sorted(round(p, 1) for p in self.points)]
        parts += [f"{a:g}-{b:g}" for a, b in self.spans]
        return ", ".join(parts) + " months" if parts else "no time stated"


def parse_times(text: str) -> Times:
    """Time points in months from a registry time frame or a sentence: "Weeks 0, 24", "1 to 5 years"."""
    out = Times()
    t = text
    for m in _UP_TO.finditer(t):
        out.spans.append((0.0, float(m.group(1)) * _UNIT[m.group(2)[0].lower()]))
    t = _UP_TO.sub(" ", t)

    def take(nums: str, unit: str) -> None:
        k = _UNIT[unit[0].lower()]
        vals = [float(x) * k for x in re.findall(_N, nums)]
        if re.search(r"\bto\b|[-–]", nums) and len(vals) == 2:
            out.spans.append((vals[0], vals[1]))
        else:
            out.points.update(v for v in vals if v > 0)       # week 0 is the baseline, not a time point

    for m in _UNIT_FIRST.finditer(t):
        take(m.group(2), m.group(1))
    t = _UNIT_FIRST.sub(" ", t)
    for m in _UNIT_AFTER.finditer(t):
        take(m.group(1), m.group(2))
    return out


def outcomes(text: str) -> frozenset[str]:
    return vocab.find(text, "outcome")


def related(a: str, b: str) -> bool:
    """Same outcome, or one is a more general name for the other (pain ~ VAS pain)."""
    return a == b or a in vocab.ancestors("outcome", b) or b in vocab.ancestors("outcome", a)


def _covered(o: str, pool) -> bool:
    return any(related(o, p) for p in pool)


@dataclass
class Registered:
    path: str
    measure: str
    time_frame: str
    outcomes: frozenset[str]
    times: Times


def registered(trial: dict, which: str = "primaryOutcomes") -> list[Registered]:
    out = []
    for i, o in enumerate(trial["protocolSection"].get("outcomesModule", {}).get(which, [])):
        text = f"{o.get('measure', '')}. {o.get('description', '')}"
        out.append(Registered(f"protocolSection.outcomesModule.{which}[{i}]", o.get("measure", ""),
                              o.get("timeFrame", ""), outcomes(text), parse_times(o.get("timeFrame", ""))))
    return out


@dataclass
class Declared:
    document_id: int
    start: int
    end: int
    text: str
    outcomes: frozenset[str]
    times: Times


def declared_primary(docs: list[dict]) -> list[Declared]:
    """Statements in the paper's own sections (abstract, methods, results) of what was primary.

    Either a sentence that says so ("The primary outcome was WOMAC at 12 months"), or the first
    sentence under a heading that says so ("Primary Outcome" / "Safety was measured by ...").
    """
    out, seen = [], set()

    def add(d: dict, start: int, end: int) -> None:
        s = SECONDARY.split(d["text"][start:end].strip())[0]   # "...; secondary outcomes were ..." is not primary
        o = outcomes(s)
        if o and s not in seen:
            seen.add(s)
            out.append(Declared(d["id"], start, end, s, o, parse_times(s)))

    for d in docs:
        read = chunks(d["part"], d["text"])
        for ch in read:
            pos = ch.start
            for piece in SENTENCE.split(ch.text):
                start = d["text"].find(piece, pos)
                pos = start + len(piece) if start >= 0 else pos
                if PRIMARY.search(SECONDARY.split(piece)[0]) and not ANALYSIS.search(piece):
                    add(d, start, start + len(piece))
        if d["part"] != "fulltext":
            continue
        pos = 0
        blocks = d["text"].split("\n\n")
        for i, block in enumerate(blocks):
            start, pos = pos, pos + len(block) + 2
            heading = len(block) < 60 and PRIMARY.search(block) and not SECONDARY.search(block)
            if not heading or i + 1 >= len(blocks):
                continue
            first = SENTENCE.split(blocks[i + 1])[0]
            b_start = pos
            if any(c.start <= b_start < c.end for c in read):    # only under a section the reader reads
                add(d, b_start, b_start + len(first))
    return out


@dataclass
class Result:
    nct_id: str
    source_id: str | None
    state: str
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


def _month(s: str | None) -> tuple[int, int] | None:
    m = re.match(r"(\d{4})-(\d{2})", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


OWN = re.compile(r"\b(?:we|our|this (?:study|trial)|the present (?:study|trial)|the current (?:study|trial))\b",
                 re.I)


def own_report(nct_id: str, docs: list[dict]) -> bool:
    """The abstract names the trial, or a sentence names it as "our"/"this" trial."""
    for d in docs:
        if nct_id not in d["text"]:
            continue
        if d["part"] == "abstract":
            return True
        for piece in SENTENCE.split(d["text"]):
            if nct_id in piece and OWN.search(piece):
                return True
    return False


def _distinctive(measure: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", measure.lower()) if w not in GENERIC_WORDS}


def registry_flags(trial: dict, pub_year: int | None) -> tuple[list[str], list[dict]]:
    sm = trial["protocolSection"]["statusModule"]
    flags, ev = [], []
    start, first = _month(sm.get("startDateStruct", {}).get("date")), _month(sm.get("studyFirstSubmitDate"))
    if start and first and first > start:
        flags.append("registered_after_start")
        ev.append({"registry": "protocolSection.statusModule.studyFirstSubmitDate",
                   "value": f"submitted {sm.get('studyFirstSubmitDate')}, enrolment started "
                            f"{sm['startDateStruct']['date']}"})
    updated = _month(sm.get("lastUpdatePostDateStruct", {}).get("date"))
    if updated and pub_year and updated[0] > pub_year:
        flags.append("registry_edited_after_publication")
        ev.append({"registry": "protocolSection.statusModule.lastUpdatePostDateStruct",
                   "value": f"last updated {sm['lastUpdatePostDateStruct']['date']}, paper published {pub_year}"})
    return flags, ev


def reconcile_pair(nct_id: str, trial: dict, source: dict, docs: list[dict]) -> Result:
    res = Result(nct_id, source["id"], "couldnt_match")
    res.flags, flag_ev = registry_flags(trial, source.get("pub_year"))
    reg = registered(trial)
    secondary = registered(trial, "secondaryOutcomes")
    reg_outcomes = set().union(*(r.outcomes for r in reg)) if reg else set()
    res.evidence += [{"registry": r.path, "value": f"{r.measure} | {r.time_frame}"} for r in reg]

    # is this paper the trial's own report, or does it only cite the trial?
    listed = [r.get("type") for r in trial["protocolSection"].get("referencesModule", {}).get("references", [])
              if source.get("pmid") and r.get("pmid") == source["pmid"]]
    if listed:
        res.evidence.append({"registry": "protocolSection.referencesModule.references",
                             "value": f"lists this paper ({listed[0]})"})
    elif not own_report(nct_id, docs):
        res.reasons.append(f"neither the registry nor the paper's abstract ties the paper to {nct_id}, and the "
                           f"paper never names it as its own trial; it may only cite the trial")
        res.evidence += flag_ev
        return res
    if len(source.get("trials", ())) > 1:
        res.reasons.append(f"the paper reports {len(source['trials'])} registered trials "
                           f"({', '.join(source['trials'])}), so its primary outcome can't be attributed to one")
        res.evidence += flag_ev
        return res
    if not reg_outcomes:
        res.reasons.append("the registered primary outcome names no measure in the vocabulary")
        res.evidence += flag_ev
        return res

    full = [d for d in docs if d["part"] == "fulltext" and d["text"]]
    decl = declared_primary(docs)
    res.evidence += [{"document_id": d.document_id, "start": d.start, "end": d.end, "text": d.text}
                     for d in decl]
    paper_primary = set().union(*(d.outcomes for d in decl)) if decl else set()

    drift = []
    for o in sorted(paper_primary):
        if _covered(o, reg_outcomes):
            continue
        if o in FAMILIES or any(x != o and o in vocab.ancestors("outcome", x) for x in paper_primary):
            continue                          # report "VAS pain", not also "pain"
        sec = [s.measure for s in secondary if _covered(o, s.outcomes)]
        drift.append(f"the paper's primary outcome {o} was registered "
                     + (f"as secondary ({sec[0]})" if sec else "nowhere in the registration"))
    if full:
        body = " ".join(d["text"] for d in full)
        reported, low = outcomes(body), body.lower()
        for r in reg:
            gone = sorted(o for o in r.outcomes if not _covered(o, reported))
            words = _distinctive(r.measure)
            if gone and not any(w in low for w in words):
                drift.append(f"registered primary outcome '{r.measure}' ({', '.join(gone)}) is not reported "
                             f"anywhere in the full text")
    for d in decl:
        if not d.times.points:
            continue
        regs = [r for r in reg if any(_covered(o, r.outcomes) for o in d.outcomes) and r.times]
        if regs and not any(r.times.covers(t) for r in regs for t in d.times.points):
            drift.append(f"the paper's primary outcome is reported at {d.times.describe()}; registered at "
                         f"{'; '.join(r.times.describe() for r in regs)}")

    res.evidence += flag_ev
    if drift:
        res.state, res.reasons = "endpoint_drift", drift
    elif decl and any(_covered(o, reg_outcomes) for o in paper_primary - FAMILIES):
        res.state = "matched"
        res.reasons.append(f"the paper's declared primary outcome ({', '.join(sorted(paper_primary))}) is "
                           f"registered as primary")
    elif not decl:
        res.reasons.append("the paper does not say which outcome was primary"
                           + ("" if full else ", and only its abstract is open"))
    else:
        res.reasons.append("the paper names its primary outcome only generally")
    return res


def no_publication(nct_id: str, trial: dict, today: date) -> Result | None:
    """The strict rule: a completed registration, no linked paper, more than 24 months on."""
    sm = trial["protocolSection"]["statusModule"]
    done = _month(sm.get("completionDateStruct", {}).get("date"))
    if sm.get("overallStatus") != "COMPLETED" or not done:
        return Result(nct_id, None, "couldnt_match", ["no linked paper, and the trial is not marked completed "
                                                      "with a completion date"])
    months = (today.year - done[0]) * 12 + today.month - done[1]
    if months <= NO_PUBLICATION_MONTHS:
        return Result(nct_id, None, "couldnt_match", [f"no linked paper, but completed only {months} months ago"])
    return Result(nct_id, None, "no_publication_found",
                  [f"completed {sm['completionDateStruct']['date']}, {months} months before the snapshot, and no "
                   f"paper in the corpus reports it"],
                  evidence=[{"registry": "protocolSection.statusModule.completionDateStruct",
                             "value": sm["completionDateStruct"]["date"]}])


def reconcile_all(conn: psycopg.Connection, today: date | None = None) -> list[Result]:
    today = today or date.today()
    trials = conn.execute("SELECT nct_id FROM trials ORDER BY nct_id").fetchall()
    results = []
    for (nct_id,) in trials:
        trial = json.loads(snapshot.trial_path(nct_id).read_text())
        links = conn.execute(
            "SELECT DISTINCT l.source_id, s.pub_year, s.pmid, (SELECT array_agg(DISTINCT x.nct_id ORDER BY x.nct_id) "
            "FROM trial_links x WHERE x.source_id = l.source_id) FROM trial_links l JOIN sources s "
            "ON s.id = l.source_id WHERE l.nct_id = %s ORDER BY 1", (nct_id,)).fetchall()
        if not links:
            results.append(no_publication(nct_id, trial, today))
            continue
        for sid, year, pmid, reported in links:
            docs = [{"id": i, "part": p, "text": t} for i, p, t in conn.execute(
                "SELECT id, part, text FROM documents WHERE source_id = %s AND part IN ('abstract', 'fulltext') "
                "ORDER BY part", (sid,)).fetchall()]
            results.append(reconcile_pair(nct_id, trial, {"id": sid, "pub_year": year, "pmid": pmid,
                                                          "trials": reported}, docs))
    with conn.transaction():
        conn.execute("DELETE FROM trial_reconciliations WHERE rules = %s", (RULES_VERSION,))
        for r in results:
            conn.execute("INSERT INTO trial_reconciliations (nct_id, source_id, state, reasons, flags, evidence, "
                         "rules) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (r.nct_id, r.source_id, r.state, json.dumps(r.reasons), r.flags,
                          json.dumps(r.evidence), RULES_VERSION))
    return results
