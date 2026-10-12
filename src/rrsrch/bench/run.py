"""Run the benchmark: write a report for each question and score it.

  faithfulness   report sentences the gate passed / sentences proposed
  coverage       expected points a report makes / expected points (model-judged)
  verdict        the writer's verdict equals the expected one
  rejections     what the reader and the gate refused, by rule

Faithfulness alone can be gamed by saying little, and coverage alone by saying
anything, so they are reported together, never one without the other.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import psycopg

from ..reader.llm import LLM
from ..writer import write as writer


@dataclass
class QuestionResult:
    id: str
    report_id: int
    proposed: int
    passed: int
    points: int
    made: int
    verdict: str | None
    expected: str
    bad_reply: str | None


@dataclass
class Score:
    results: list[QuestionResult] = field(default_factory=list)
    writer_rejections: Counter = field(default_factory=Counter)
    reader: dict = field(default_factory=dict)

    def summary(self) -> dict:
        prop = sum(r.proposed for r in self.results)
        pts = sum(r.points for r in self.results)
        return {
            "questions": len(self.results),
            "faithfulness": sum(r.passed for r in self.results) / prop if prop else None,
            "coverage": sum(r.made for r in self.results) / pts if pts else None,
            "verdicts_matched": sum(r.verdict == r.expected for r in self.results),
            "sentences_proposed": prop,
            "writer_rejections": dict(self.writer_rejections),
            "reader": self.reader,
        }


def reader_stats(conn: psycopg.Connection) -> dict:
    accepted = conn.execute("SELECT count(*) FROM claims").fetchone()[0]
    refused = dict(conn.execute("SELECT reason, count(*) FROM claim_rejections GROUP BY reason").fetchall())
    total = accepted + sum(refused.values())
    return {"claims_accepted": accepted, "claims_refused": refused,
            "rejection_rate": sum(refused.values()) / total if total else None}


def run(conn: psycopg.Connection, llm: LLM, bench: dict, only: list[str] | None = None,
        judge: LLM | None = None, progress=print) -> Score:
    score = Score()
    qs = [q for q in bench["questions"] if not only or q["id"] in only]
    for i, q in enumerate(qs, 1):
        rep = writer.write(conn, llm, q["question"], q.get("scope"), bench_id=q["id"])
        points = q["expected"]["points"]
        made = writer.judge_coverage(conn, judge or llm, rep, points)
        score.writer_rejections.update(s.verdict.rule for s in rep.sentences if not s.verdict.passed)
        score.results.append(QuestionResult(q["id"], rep.id, len(rep.sentences), len(rep.kept), len(points),
                                            sum(made), rep.verdict, q["expected"]["verdict"], rep.bad_reply))
        r = score.results[-1]
        progress(f"[{i}/{len(qs)}] {q['id']}: {r.passed}/{r.proposed} sentences passed, "
                 f"{r.made}/{r.points} points, verdict {r.verdict} (expected {r.expected})")
    score.reader = reader_stats(conn)
    return score
