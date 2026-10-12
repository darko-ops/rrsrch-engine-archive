from pathlib import Path

import pytest

from rrsrch.bench import questions

MA = "Results\nCompared to placebo, MSCs lowered VAS pain by 1.2 points at 12 months (95% CI -2.0 to -0.4)."
CORPUS = {"answer_key": [{"pmid": "1"}, {"pmid": "2"}],
          "studies": [{"id": "MED:10", "included_by": ["1"]}, {"id": "MED:11", "included_by": ["2"]}]}


def bench(**over):
    q = {"id": "q1", "kind": "efficacy", "question": "Do MSCs reduce pain at 12 months?",
         "expected": {"verdict": "benefit", "answer": "Yes, modestly.", "points": ["VAS pain lower at 12 months"]},
         "support": [{"meta": "1", "effect": "benefit",
                      "quote": "MSCs lowered VAS pain by 1.2 points at 12 months (95% CI -2.0 to -0.4)."}]}
    q.update(over)
    return {"questions": [q]}


def check(b):
    return questions.check(b, CORPUS, read_text=lambda pmid: MA if pmid == "1" else None)


def test_a_well_formed_question_passes_and_lists_its_corpus_studies():
    rep = check(bench())
    assert rep.problems == []
    assert (rep.quotes_checked, rep.studies) == (1, {"q1": ["MED:10"]})


def test_an_altered_quote_is_a_problem():
    b = bench(support=[{"meta": "1", "effect": "benefit",
                        "quote": "MSCs lowered VAS pain by 1.5 points at 12 months (95% CI -2.0 to -0.4)."}])
    assert [p.detail[:28] for p in check(b).problems] == ["support[0]: quote not found "]


def test_contested_needs_disagreeing_findings():
    b = bench(expected={"verdict": "contested", "answer": "a", "points": ["b"]})
    assert [p.detail for p in check(b).problems] == ["verdict is contested but every supporting finding agrees"]


def test_a_finding_must_come_from_the_answer_key():
    b = bench(support=[{"meta": "9", "effect": "benefit", "quote": "x" * 20}])
    assert "not in the answer key" in check(b).problems[0].detail


def test_unfetched_meta_analyses_are_counted_not_failed():
    b = bench(support=[{"meta": "2", "effect": "benefit", "quote": "anything long enough"}])
    rep = check(b)
    assert (rep.problems, rep.quotes_unchecked) == ([], 1)


@pytest.mark.skipif(not Path("data/snapshots/europepmc").exists(), reason="snapshots not fetched")
def test_the_benchmark_checks_clean_against_the_real_answer_key():
    rep = questions.check(questions.load())
    assert [(p.question, p.detail) for p in rep.problems] == []
    assert rep.quotes_unchecked == 0
