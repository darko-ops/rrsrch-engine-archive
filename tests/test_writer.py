import json
import os
import uuid

import pytest

from rrsrch.check import fields as fieldmod
from rrsrch.writer import retrieve, write

psycopg = pytest.importorskip("psycopg")
from rrsrch.ledger import db  # noqa: E402

SOURCES = {
    "MED:1": ("Bone marrow MSCs versus hyaluronic acid for knee osteoarthritis: a randomized trial",
              "Forty patients with knee osteoarthritis were randomized."),
    "MED:2": ("Adipose-derived MSCs for knee osteoarthritis: a randomized placebo-controlled trial",
              "Twenty-four patients with knee osteoarthritis were enrolled."),
    "MED:3": ("Bone marrow MSCs in a rabbit model of knee osteoarthritis", "Rabbits were treated."),
}
CLAIMS = [
    ("MED:1", "At 12 months, VAS pain was lower with MSCs than with hyaluronic acid (p = 0.01)."),
    ("MED:2", "At 12 months, VAS pain was lower with MSCs than with placebo (p = 0.03)."),
    ("MED:2", "At 12 months, WOMAC function did not differ between MSCs and placebo (p = 0.40)."),
    ("MED:3", "At 12 weeks, cartilage repair was better with MSCs in rabbits (p = 0.02)."),
    ("MED:1", "The trial enrolled 40 patients."),
]


@pytest.fixture
def conn():
    url = os.environ.get("RRSRCH_DATABASE_URL", db.DEFAULT_URL)
    try:
        admin = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    name = f"rrsrch_test_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {name}")
    c = psycopg.connect(url.rsplit("/", 1)[0] + f"/{name}")
    db.migrate(c)
    ids = {}
    for sid, (title, abstract) in SOURCES.items():
        c.execute("INSERT INTO sources (id, title, pub_year, has_full_text, snapshot_sha256) "
                  "VALUES (%s, %s, 2020, false, 'x')", (sid, title))
        ids[sid] = c.execute("INSERT INTO documents (source_id, part, text, sha256) "
                             "VALUES (%s, 'abstract', %s, 'h') RETURNING id", (sid, abstract)).fetchone()[0]
    claim_ids = []
    for sid, text in CLAIMS:
        f = fieldmod.extract(text)
        claim_ids.append(c.execute(
            "INSERT INTO claims (document_id, doc_sha256, span_start, span_end, text, fields, scope, extractor) "
            "VALUES (%s, 'h', 0, 1, %s, %s, %s, 'test') RETURNING id",
            (ids[sid], text, json.dumps(f.to_json()), json.dumps(f.to_json()["scope"]))).fetchone()[0])
    c.commit()
    c.claim_ids = claim_ids
    try:
        yield c
    finally:
        c.close()
        admin.execute(f"DROP DATABASE {name}")
        admin.close()


class ScriptedLLM:
    model = "scripted"

    def __init__(self, report, made=None):
        self.report, self.made, self.seen = report, made, []

    def json_chat(self, system, user):
        self.seen.append(user)
        if system == write.JUDGE_SYSTEM:
            return {"made": self.made}
        return self.report


pytestmark = pytest.mark.db


def test_retrieval_ranks_by_shared_vocabulary_and_drops_other_populations(conn):
    got = retrieve.candidates(conn, "Do MSCs reduce VAS pain at 12 months?")
    texts = [c.text for c in got]
    assert not any("rabbits" in t for t in texts)            # another species: the gate would refuse it
    assert texts[0].startswith("At 12 months, VAS pain")
    assert "The trial enrolled 40 patients." not in texts    # nothing about the question


def test_every_sentence_is_gated_and_kept_with_its_verdict(conn):
    hyal, plac, func, rabbit, _ = conn.claim_ids
    llm = ScriptedLLM({"verdict": "benefit", "sentences": [
        {"text": "At 12 months, VAS pain was lower with MSCs than with hyaluronic acid or placebo.",
         "cites": [hyal, plac]},
        {"text": "Two trials found lower VAS pain with MSCs at 12 months.", "cites": [hyal, plac]},
        {"text": "WOMAC function did not differ between MSCs and placebo at 12 months.", "cites": [func]},
        {"text": "WOMAC function improved with MSCs at 12 months.", "cites": [func]},          # direction
        {"text": "At 24 months, VAS pain was lower with MSCs (p = 0.01).", "cites": [hyal]},    # number
        {"text": "Three trials found lower VAS pain with MSCs.", "cites": [hyal, plac]},       # count
        {"text": "MSCs repaired cartilage.", "cites": [rabbit]},                               # not shown
        {"text": "MSCs reduce pain.", "cites": []},                                            # uncited
    ]}, made=[True, False])
    rep = write.write(conn, llm, "Do MSCs reduce VAS pain at 12 months?")
    assert [s.verdict.rule for s in rep.sentences] == [
        "pass", "pass", "pass", "direction", "numbers", "numbers", "unknown_claim", "cites"]
    assert rep.faithfulness == 3 / 8
    assert "MED:1; MED:2" in rep.render() and "24 months" not in rep.render()
    assert "rabbits" not in llm.seen[0]                      # the model never saw the out-of-scope claim
    rows = conn.execute("SELECT passed, rule FROM report_sentences WHERE report_id = %s ORDER BY position",
                        (rep.id,)).fetchall()
    assert sum(p for p, _ in rows) == 3 and len(rows) == 8
    assert write.judge_coverage(conn, llm, rep, ["pain is lower", "function is better"]) == [True, False]
    assert conn.execute("SELECT count(*) FROM report_coverage WHERE made").fetchone()[0] == 1


def test_an_unusable_reply_is_recorded_not_raised(conn):
    rep = write.write(conn, ScriptedLLM({"sentences": "nope"}), "Do MSCs reduce VAS pain at 12 months?")
    assert rep.sentences == [] and rep.bad_reply == "'sentences' is not a list"
    assert conn.execute("SELECT bad_reply FROM reports WHERE id = %s", (rep.id,)).fetchone()[0]


def test_the_benchmark_runner_reports_all_three_numbers(conn):
    from rrsrch.bench import run
    plac = conn.claim_ids[1]
    llm = ScriptedLLM({"verdict": "benefit", "sentences": [
        {"text": "VAS pain was lower with MSCs than with placebo at 12 months.", "cites": [plac]},
        {"text": "VAS pain was 50% lower.", "cites": [plac]}]}, made=[True, True, False])
    bench = {"questions": [{"id": "q", "question": "Do MSCs reduce VAS pain at 12 months?",
                            "scope": {"outcome": ["vas_pain"], "months": 12},
                            "expected": {"verdict": "benefit", "points": ["a", "b", "c"]}}]}
    s = run.run(conn, llm, bench, progress=lambda *_: None).summary()
    assert (s["faithfulness"], round(s["coverage"], 3), s["verdicts_matched"]) == (0.5, 0.667, 1)
    assert s["writer_rejections"] == {"numbers": 1}
    assert s["reader"]["claims_accepted"] == 5
