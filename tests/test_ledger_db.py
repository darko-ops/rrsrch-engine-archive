"""Ledger load against a real Postgres (the docker-compose one), in a throwaway database."""
import json
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from rrsrch.corpus import snapshot  # noqa: E402
from rrsrch.ledger import db  # noqa: E402

ADMIN_URL = os.environ.get("RRSRCH_DATABASE_URL", db.DEFAULT_URL)

pytestmark = pytest.mark.db


@pytest.fixture
def conn():
    name = f"rrsrch_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    admin.execute(f"CREATE DATABASE {name}")
    c = psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}")
    try:
        yield c
    finally:
        c.close()
        admin.execute(f"DROP DATABASE {name}")
        admin.close()


@pytest.fixture
def corpus_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = {"id": "1", "source": "MED", "pmid": "1", "title": "BM-MSC for knee OA",
           "pubYear": "2019", "abstractText": "<h4>Methods</h4>RCT, NCT01234567. n=40.",
           "pubTypeList": {"pubType": ["Randomized Controlled Trial"]}}
    d = snapshot.record_dir("MED:1")
    d.mkdir(parents=True)
    (d / "record.json").write_bytes(snapshot.stable_record_bytes(rec))
    t = snapshot.trial_path("NCT01234567")
    t.parent.mkdir(parents=True)
    t.write_text(json.dumps({"hasResults": False, "protocolSection": {
        "identificationModule": {"briefTitle": "MSC knee trial"},
        "statusModule": {"overallStatus": "COMPLETED", "completionDateStruct": {"date": "2018-06"}},
        "outcomesModule": {"primaryOutcomes": [{"measure": "WOMAC", "timeFrame": "12 months"}]}}}))
    return {"topic": "t",
            "answer_key": [{"pmid": "99", "pmcid": "PMC99", "title": "A meta-analysis", "year": 2025}],
            "studies": [{"id": "MED:1", "title": "BM-MSC for knee OA", "included_by": ["99"]}],
            "unresolved": []}


def test_load_is_repeatable_and_links_trials(conn, corpus_dir):
    assert db.migrate(conn) == ["0001_ledger.sql", "0002_reader.sql", "0003_writer.sql",
                                "0004_trials.sql", "0005_skeptic.sql"]
    assert db.migrate(conn) == []
    rep = db.load_corpus(conn, corpus_dir)
    assert (rep.sources, rep.trials, rep.links) == (1, 1, 1)
    db.load_corpus(conn, corpus_dir)
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 2   # title + abstract
    assert conn.execute("SELECT method FROM trial_links").fetchone()[0] == "nct_in_abstract"
    assert conn.execute("SELECT has_full_text FROM sources").fetchone()[0] is False


def test_text_changed_under_a_claim_is_refused(conn, corpus_dir):
    db.migrate(conn)
    db.load_corpus(conn, corpus_dir)
    doc_id, sha = conn.execute("SELECT id, sha256 FROM documents WHERE part = 'abstract'").fetchone()
    conn.execute("INSERT INTO claims (document_id, doc_sha256, span_start, span_end, text, extractor) "
                 "VALUES (%s, %s, 0, 7, 'Methods', 'test')", (doc_id, sha))
    conn.commit()
    rec_path = snapshot.record_dir("MED:1") / "record.json"
    rec = json.loads(rec_path.read_text())
    rec["abstractText"] = "<h4>Methods</h4>Corrected: n=38."
    rec_path.write_bytes(snapshot.stable_record_bytes(rec))
    with pytest.raises(db.ChangedUnderClaims):
        db.load_corpus(conn, corpus_dir)
