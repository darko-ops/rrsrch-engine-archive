# rrsrch

**The evidence engine for regenerative medicine.** A local-first research agent
that answers hard questions with a map of what the literature actually shows,
where it conflicts, and every sentence traced to its source passage.

rrsrch is a research tool, not medical advice.

## Status

Early. v1 answers questions about one topic, MSC therapy for knee
osteoarthritis, over a fixed corpus. The build order:

1. **Fixed corpus**: the studies included in recent meta-analyses, their
   trial registrations, and the claim-ledger schema. ← *done*
2. Reader → ledger → citation gate, with the clinical vocabulary.
3. Skeptic and trial reconciliation.
4. Live search, editable plan, and the reading interface.

## How the corpus is built

The meta-analyses are the **answer key**, not evidence: their conclusions are
the benchmark's expected answers, so they never enter the ledger. The corpus is
the studies they included, read from each meta-analysis's own Table 1 and
matched to PubMed by rule (`rrsrch corpus discover`). Anything matched by
search instead of by reference list is marked for a person to review, and
anything that can't be matched is listed as unresolved, never guessed. See
[`corpus/README.md`](corpus/README.md).

Everything rrsrch reads is snapshotted locally and pinned by hash in
`corpus/snapshots.lock.json`. Snapshots aren't committed, because article
text carries per-paper licenses, but `rrsrch corpus fetch` rebuilds them and
`rrsrch corpus verify` proves they're the same bytes.

## Running it

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
uv run rrsrch corpus fetch       # snapshot papers + ClinicalTrials.gov records (~1 min)
uv run rrsrch corpus verify      # check them against the lockfile
docker compose up -d             # Postgres 16 + pgvector on :5433
uv run rrsrch ledger load        # migrate and load the ledger
uv run pytest                    # tests; the db ones skip if Postgres is down
```

Searches go to Europe PMC and ClinicalTrials.gov, so queries leave the
machine. Nothing else does.

On macOS, keep the checkout out of an iCloud-synced folder (Desktop,
Documents). iCloud marks the virtualenv's `.pth` files hidden, and Python
3.13 then can't import the package.

## Layout

```
corpus/manifest.yaml           the reviewed corpus: answer key, studies, unresolved
corpus/snapshots.lock.json     sha256 of every snapshot
db/migrations/                 the ledger schema
src/rrsrch/sources/            Europe PMC and ClinicalTrials.gov clients
src/rrsrch/corpus/             discover (Table 1 → studies), snapshots, text extraction
src/rrsrch/ledger/             Postgres: migrations and corpus load
```
