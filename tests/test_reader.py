import json
import uuid

import pytest

from rrsrch.reader import extract, spans
from rrsrch.reader.llm import parse_json_object


def test_quotes_are_found_through_typography_only():
    text = "Results\nVAS pain fell from 6.1 to 3.2 – a “marked” change   at 12 months."
    s = spans.locate('VAS pain fell from 6.1 to 3.2 - a "marked" change at 12 months.', text)
    assert s is not None and text[s[0]:s[1]].startswith("VAS pain") and text[s[0]:s[1]].endswith("months.")
    assert spans.locate("VAS pain fell from 6.1 to 3.3 - a", text) is None          # a changed digit
    assert spans.locate("vas pain fell from 6.1 to 3.2", text) is None               # changed case
    assert spans.locate("VAS pain", text) is None                                    # too short to pin


def test_quotes_must_be_inside_the_chunk_that_was_read():
    text = "Introduction: MSCs repaired cartilage in rats. Results: WOMAC improved at 12 months."
    start = text.index("Results")
    assert spans.locate("MSCs repaired cartilage in rats.", text, start) is None
    assert spans.locate("WOMAC improved at 12 months.", text, start) is not None


def test_replies_parse_with_or_without_a_code_fence():
    assert parse_json_object('```json\n{"claims": []}\n```') == {"claims": []}
    assert parse_json_object('Here you go: {"claims": [1]} trailing') == {"claims": [1]}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_fulltext_chunks_skip_introduction_and_discussion():
    text = "\n\n".join(["Introduction", "Rats did well in prior work.", "Methods", "We enrolled 40 patients.",
                      "Results", "WOMAC improved at 12 months.", "Discussion", "Others found the same."])
    got = [c.text for c in extract.chunks("fulltext", text)]
    assert got == ["We enrolled 40 patients.\n\nResults\n\nWOMAC improved at 12 months."]
    c = extract.chunks("fulltext", text)[0]
    assert text[c.start:c.end] == c.text                      # chunks are exact slices


# ── the whole reader against Postgres, with a scripted model ──

psycopg = pytest.importorskip("psycopg")
from rrsrch.ledger import db  # noqa: E402

ABSTRACT = ("Methods\nForty patients with knee osteoarthritis received bone marrow MSCs or hyaluronic acid.\n"
            "Results\nAt 12 months, WOMAC scores improved more with MSCs than with hyaluronic acid (p = 0.01), "
            "while VAS pain did not differ (p = 0.41).")


class ScriptedLLM:
    model = "scripted"

    def __init__(self, claims):
        self.claims = claims

    def json_chat(self, system, user):
        assert "Forty patients" in user
        return {"claims": self.claims}


@pytest.fixture
def conn():
    import os
    url = os.environ.get("RRSRCH_DATABASE_URL", db.DEFAULT_URL)
    try:
        admin = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    name = f"rrsrch_test_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {name}")
    c = psycopg.connect(url.rsplit("/", 1)[0] + f"/{name}")
    db.migrate(c)
    c.execute("INSERT INTO sources (id, title, has_full_text, snapshot_sha256) VALUES "
              "('MED:1', 'Bone marrow MSCs versus hyaluronic acid for knee osteoarthritis', false, 'x')")
    c.execute("INSERT INTO documents (source_id, part, text, sha256) VALUES ('MED:1', 'abstract', %s, 'h')",
              (ABSTRACT,))
    c.commit()
    try:
        yield c
    finally:
        c.close()
        admin.execute(f"DROP DATABASE {name}")
        admin.close()


@pytest.mark.db
def test_reader_keeps_checked_claims_and_records_every_rejection(conn):
    quote = ("At 12 months, WOMAC scores improved more with MSCs than with hyaluronic acid (p = 0.01), "
             "while VAS pain did not differ (p = 0.41).")
    llm = ScriptedLLM([
        {"quote": quote, "claim": "At 12 months, WOMAC improved more with MSCs than hyaluronic acid (p = 0.01)."},
        {"quote": quote, "claim": "At 12 months, VAS pain did not differ between MSCs and hyaluronic acid (p = 0.41)."},
        {"quote": quote, "claim": "MSCs reduced VAS pain more than hyaluronic acid."},                  # direction flip
        {"quote": quote, "claim": "At 24 months, WOMAC improved more with MSCs (p = 0.01)."},            # invented number
        {"quote": "At 12 months, KOOS improved with MSCs (p = 0.01).", "claim": "KOOS improved (p = 0.01)."},  # not in text
        {"quote": quote, "claim": "The study was interesting."},                                          # nothing to check
    ])
    rep = extract.read_corpus(conn, llm, progress=lambda *_: None)
    assert rep.claims == 2
    assert rep.rejected == {"gate:direction": 1, "gate:numbers": 1, "quote_not_found": 1,
                            "nothing_checkable": 1}
    rows = conn.execute("SELECT text, span_start, span_end, scope, self_check FROM claims ORDER BY id").fetchall()
    for text, a, b, scope, check in rows:
        assert ABSTRACT[a:b] == quote
        assert scope == {"species": ["human"], "joint": ["knee"], "condition": ["osteoarthritis"]}
        assert check["passed"] is True
    # rerunning reads nothing new
    rep2 = extract.read_corpus(conn, llm, progress=lambda *_: None)
    assert (rep2.chunks_read, rep2.chunks_skipped, rep2.claims) == (0, 1, 0)
