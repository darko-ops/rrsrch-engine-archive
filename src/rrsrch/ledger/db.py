"""Postgres for the claim ledger: migrations, and loading the fixed corpus.

Loading is repeatable. Rerunning it after a refresh updates what changed,
with one exception: a document whose text changed is refused if any claim
already points into it, because that claim's span would now point at
different words. That case has to be handled on purpose, never by overwrite.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from ..corpus import snapshot, text
from ..sources.ctgov import find_nct_ids

DEFAULT_URL = "postgresql://rrsrch:rrsrch@localhost:5433/rrsrch"
MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or os.environ.get("RRSRCH_DATABASE_URL", DEFAULT_URL))


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply db/migrations/*.sql in order, once each. Returns the names applied now."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.name in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
        applied.append(f.name)
    return applied


class ChangedUnderClaims(Exception):
    """A document's text changed but claims still point into the old text."""


@dataclass
class LoadReport:
    sources: int = 0
    documents: dict[str, int] = field(default_factory=dict)
    trials: int = 0
    links: int = 0
    missing_snapshots: list[str] = field(default_factory=list)
    unregistered_ncts: list[str] = field(default_factory=list)


def _put_document(conn, source_id: str, part: str, body: str) -> None:
    digest = snapshot.sha256(body.encode())
    row = conn.execute("SELECT id, sha256 FROM documents WHERE source_id = %s AND part = %s",
                       (source_id, part)).fetchone()
    if row is None:
        conn.execute("INSERT INTO documents (source_id, part, text, sha256) VALUES (%s, %s, %s, %s)",
                     (source_id, part, body, digest))
    elif row[1] != digest:
        n = conn.execute("SELECT count(*) FROM claims WHERE document_id = %s", (row[0],)).fetchone()[0]
        if n:
            raise ChangedUnderClaims(f"{source_id} {part}: text changed and {n} claim(s) point into it")
        conn.execute("UPDATE documents SET text = %s, sha256 = %s WHERE id = %s", (body, digest, row[0]))


def _trial_fields(raw: bytes) -> dict:
    d = json.loads(raw)
    p = d.get("protocolSection", {})
    return {
        "title": p.get("identificationModule", {}).get("briefTitle"),
        "overall_status": p.get("statusModule", {}).get("overallStatus"),
        "completion_date": p.get("statusModule", {}).get("completionDateStruct", {}).get("date"),
        "has_results": bool(d.get("hasResults")),
        "primary_outcomes": p.get("outcomesModule", {}).get("primaryOutcomes", []),
    }


def load_corpus(conn: psycopg.Connection, manifest: dict) -> LoadReport:
    rep = LoadReport()
    with conn.transaction():
        for m in manifest["answer_key"]:
            conn.execute(
                "INSERT INTO answer_key (pmid, pmcid, title, pub_year) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (pmid) DO UPDATE SET pmcid = EXCLUDED.pmcid, title = EXCLUDED.title, "
                "pub_year = EXCLUDED.pub_year",
                (m["pmid"], m.get("pmcid"), m["title"], m.get("year")))

        texts: dict[str, dict[str, str]] = {}
        for s in manifest["studies"]:
            d = snapshot.record_dir(s["id"])
            rec_path = d / "record.json"
            if not rec_path.exists():
                rep.missing_snapshots.append(s["id"])
                continue
            raw = rec_path.read_bytes()
            r = json.loads(raw)
            ft_path = d / "fulltext.xml"
            parts = {"title": r.get("title") or s["title"],
                     "abstract": text.abstract_text(r.get("abstractText")),
                     "fulltext": text.fulltext_body(ft_path.read_bytes()) if ft_path.exists() else ""}
            conn.execute(
                "INSERT INTO sources (id, pmid, pmcid, doi, title, pub_year, journal, pub_types, "
                "has_full_text, license, snapshot_sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET pmid = EXCLUDED.pmid, pmcid = EXCLUDED.pmcid, "
                "doi = EXCLUDED.doi, title = EXCLUDED.title, pub_year = EXCLUDED.pub_year, "
                "journal = EXCLUDED.journal, pub_types = EXCLUDED.pub_types, "
                "has_full_text = EXCLUDED.has_full_text, license = EXCLUDED.license, "
                "snapshot_sha256 = EXCLUDED.snapshot_sha256, loaded_at = now()",
                (s["id"], r.get("pmid"), r.get("pmcid"), r.get("doi"), parts["title"],
                 int(r["pubYear"]) if r.get("pubYear") else None,
                 (r.get("journalInfo") or {}).get("journal", {}).get("title"),
                 (r.get("pubTypeList") or {}).get("pubType", []),
                 bool(parts["fulltext"]), r.get("license"), snapshot.sha256(raw)))
            rep.sources += 1
            for part, body in parts.items():
                if body:
                    _put_document(conn, s["id"], part, body)
                    rep.documents[part] = rep.documents.get(part, 0) + 1
            texts[s["id"]] = parts
            for pmid in s["included_by"]:
                conn.execute("INSERT INTO inclusions VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (pmid, s["id"]))

        for sid, parts in texts.items():
            for part, method in (("abstract", "nct_in_abstract"), ("fulltext", "nct_in_fulltext")):
                for nct in find_nct_ids(parts[part]):
                    tp = snapshot.trial_path(nct)
                    if not tp.exists():
                        rep.unregistered_ncts.append(f"{nct} (cited by {sid})")
                        continue
                    raw = tp.read_bytes()
                    f = _trial_fields(raw)
                    conn.execute(
                        "INSERT INTO trials (nct_id, title, overall_status, completion_date, "
                        "has_results, primary_outcomes, snapshot_sha256) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (nct_id) DO UPDATE SET "
                        "title = EXCLUDED.title, overall_status = EXCLUDED.overall_status, "
                        "completion_date = EXCLUDED.completion_date, has_results = EXCLUDED.has_results, "
                        "primary_outcomes = EXCLUDED.primary_outcomes, "
                        "snapshot_sha256 = EXCLUDED.snapshot_sha256, loaded_at = now()",
                        (nct, f["title"], f["overall_status"], f["completion_date"],
                         f["has_results"], json.dumps(f["primary_outcomes"]), snapshot.sha256(raw)))
                    conn.execute("INSERT INTO trial_links VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                 (nct, sid, method))
                    rep.links += 1
        rep.trials = conn.execute("SELECT count(*) FROM trials").fetchone()[0]
    return rep
