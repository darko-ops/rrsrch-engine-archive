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
2. **Reader → ledger → citation gate → writer**, with the clinical vocabulary
   and the benchmark. ← *built; waiting on the first run on the DGX Spark*
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

## The citation gate

A report sentence may stand on its cited claims only if rules agree, in order:

| Rule | Rejects |
|---|---|
| `cites` | a sentence with no claim behind it |
| `scope` | a claim about another population: animals, another joint, another condition |
| `direction` | benefit where the claim found no difference (including benefit words over a non-significant p), or the reverse; a finding about one outcome used for another |
| `entities` | a cell type, comparator or outcome the claims don't name. Going more general is allowed ("pain" from "VAS pain"); going more specific is not |
| `numbers` | any number not in the claims, to the precision written; p-value inequalities are checked as inequalities |

Every verdict names the rule that decided it. The vocabulary behind the rules
(cell types, comparators, outcome measures, populations) lives in
[`src/rrsrch/check/vocab.yaml`](src/rrsrch/check/vocab.yaml), written to be
corrected by a domain researcher rather than a programmer.

`rrsrch gate-eval` scores the gate on
[`gate_cases.yaml`](src/rrsrch/check/gate_cases.yaml): real claims from the
corpus with faithful and unfaithful sentences. Today it catches 23/23
unfaithful sentences and refuses 0/14 faithful ones. **That is a development
set**: it was written alongside the rules, so it shows the rules do what they
were built to do, not how they do on new text. Cases the gate is not designed
to catch are kept in the set as `known_gap` and reported, not dropped:

- which trial arm a number belongs to (a high-dose p-value attached to the
  low-dose arm still finds support), and
- generalizing one trial to a class ("MSCs reduce pain") from one study.

The writer's sentences face the same gate as the reader's claims.

## The benchmark

[`benchmark/questions.yaml`](benchmark/questions.yaml) has 25 questions. Each
expected answer comes from the meta-analyses, as a verdict (including
`contested` where they disagree), the points a complete answer must make, and
the findings behind it, each quoted verbatim. `rrsrch bench check` proves the
quotes. Reading the meta-analyses closely turned up contradictions in most of
them; see [`benchmark/README.md`](benchmark/README.md). The questions are a
draft until a domain researcher reviews them.

## The reader

The reader proposes claims with a local model, and rules decide what enters
the ledger. Each claim's quote must be in the document **verbatim** (only
typography is normalized), and the claim must pass the citation gate against
its own quote. Everything refused is kept in `claim_rejections` with the rule
that refused it, because the rejection rate is a published number.

Only the abstract and the full text's methods, results and conclusions are
read. Introductions and discussions are mostly about other studies, so they
are skipped rather than misattributed.

The model is any OpenAI-compatible server (vLLM or Ollama on the DGX Spark):

```bash
export RRSRCH_LLM_URL=http://spark-31f4.local:8000/v1     # vLLM; Ollama is :11434/v1
export RRSRCH_READER_MODEL=<model name the server uses>
uv run rrsrch read plan             # what will be sent: ~180 chunks, ~440k characters
uv run rrsrch read run --limit 2    # try two sources first
uv run rrsrch read run              # everything; reruns skip what's already read
uv run rrsrch read stats            # proposed vs accepted, rejections by rule
uv run rrsrch gate "SENTENCE" 12 15 # check a sentence against ledger claims 12 and 15
```

## The writer

The writer answers a question from the ledger, never from the papers directly.
It picks claims that share the question's outcome, comparator or follow-up
time (by the same vocabulary the gate uses), and gives the model only those
claims, numbered. The model returns a verdict and sentences, each citing the
claims it rests on. **Every sentence then faces the citation gate against
exactly the claims it cites.** A sentence that fails is left out of the
report and kept in `report_sentences` with the rule that refused it.

One rule is added for writing across studies: "two trials found…" passes only
when the sentence cites exactly two different studies.

```bash
uv run rrsrch write "Do MSCs reduce knee pain at 12 months compared with hyaluronic acid?"
uv run rrsrch bench run                   # all 25 questions: faithfulness, coverage, verdicts, rejections
uv run rrsrch bench run --only pain-12m   # one question
```

`bench run` reports the numbers together: **faithfulness** (sentences the
gate passed / sentences proposed), **coverage** (expected points made,
judged by the model, so it's labeled as such), **verdict agreement** with the
answer key, and the reader's and writer's **rejections by rule**. A report
can score high on faithfulness by saying little, which is why coverage is
never reported without it.

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
src/rrsrch/check/              clinical vocabulary and the citation gate
src/rrsrch/reader/             claim extraction with a local model
src/rrsrch/writer/             claim retrieval and gated report writing
src/rrsrch/bench/              benchmark checks and the benchmark run
benchmark/questions.yaml       the benchmark questions and expected answers
```

## License

[Apache 2.0](LICENSE).
