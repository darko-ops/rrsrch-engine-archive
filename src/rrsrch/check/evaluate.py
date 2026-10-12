"""Score the citation gate on its evaluation set (gate_cases.yaml).

Reports, separately:
  - catch rate:     unfaithful sentences rejected, and by an expected rule
  - false rejects:  faithful sentences the gate refused
  - known gaps:     what the gate is not designed to catch, and whether it did
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import gate

CASES_PATH = Path(__file__).with_name("gate_cases.yaml")


@dataclass
class Result:
    case: dict
    verdict: gate.Verdict

    @property
    def correct(self) -> bool:
        if self.case["expect"] == "pass":
            return self.verdict.passed
        return not self.verdict.passed and (
            "rules" not in self.case or self.verdict.rule in self.case["rules"])


@dataclass
class Score:
    results: list[Result] = field(default_factory=list)

    def _pick(self, expect: str, gap: bool) -> list[Result]:
        return [r for r in self.results
                if r.case["expect"] == expect and bool(r.case.get("known_gap")) == gap]

    def summary(self) -> dict:
        unfaithful, faithful, gaps = self._pick("reject", False), self._pick("pass", False), \
            [r for r in self.results if r.case.get("known_gap")]
        return {
            "caught": f"{sum(r.correct for r in unfaithful)}/{len(unfaithful)}",
            "false_rejects": f"{sum(not r.correct for r in faithful)}/{len(faithful)}",
            "known_gaps_caught": f"{sum(r.correct for r in gaps)}/{len(gaps)}",
        }


def load_cases(path: Path = CASES_PATH) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text())
    return data["claims"], data["cases"]


def run(path: Path = CASES_PATH) -> Score:
    claims, cases = load_cases(path)
    score = Score()
    for case in cases:
        cited = [gate.CitedClaim(name, claims[name]["text"],
                                 {g: frozenset(v) for g, v in claims[name]["context"].items()})
                 for name in case["claims"]]
        score.results.append(Result(case, gate.check(case["sentence"], cited)))
    return score
