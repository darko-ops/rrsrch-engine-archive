-- The claim ledger: everything a report is allowed to say comes from here.
--
-- Step 1 fills sources, documents, trials, trial_links and the corpus tables
-- from the fixed corpus. `claims` is defined now so its shape is settled, and
-- is filled in step 2 by the reader.
--
-- Two rules shape the schema:
--   1. A claim points at an exact character span of a document, and a
--      document's text is stored with its hash. If the text ever changes the
--      hash changes, so a span can never silently point at different words.
--   2. Meta-analyses are the benchmark's answer key, not evidence. They live in
--      answer_key and never become a source a claim can cite.

CREATE EXTENSION IF NOT EXISTS vector;

-- One paper (or abstract-only record) in the corpus.
CREATE TABLE sources (
    id              text PRIMARY KEY,           -- Europe PMC "SRC:ID", e.g. MED:30762487
    pmid            text,
    pmcid           text,
    doi             text,
    title           text NOT NULL,
    pub_year        int,
    journal         text,
    pub_types       text[] NOT NULL DEFAULT '{}',
    has_full_text   boolean NOT NULL,           -- false = abstract only (stated in every report)
    license         text,                       -- as reported by Europe PMC, when open access
    snapshot_sha256 text NOT NULL,              -- of the metadata snapshot this row was loaded from
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

-- The exact text a claim's span refers to. One row per part of a source.
CREATE TABLE documents (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id  text NOT NULL REFERENCES sources(id),
    part       text NOT NULL CHECK (part IN ('title', 'abstract', 'fulltext')),
    text       text NOT NULL,
    sha256     text NOT NULL,                   -- of `text`; spans are only valid against this hash
    UNIQUE (source_id, part)
);

-- A single claim pulled from a document (step 2).
CREATE TABLE claims (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES documents(id),
    doc_sha256   text NOT NULL,                 -- the document hash the span was taken against
    span_start   int NOT NULL CHECK (span_start >= 0),
    span_end     int NOT NULL CHECK (span_end > span_start),
    text         text NOT NULL,                 -- the claim as stated; must be checkable against the span
    fields       jsonb NOT NULL DEFAULT '{}',   -- comparands for the agreement rules: numbers, direction, entities
    scope        jsonb NOT NULL DEFAULT '{}',   -- population: species, joint, condition
    extractor    text NOT NULL,                 -- model + prompt version that proposed it
    embedding    vector,                        -- dimension fixed once the embedding model is chosen
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX claims_document_idx ON claims (document_id);

-- A ClinicalTrials.gov registration: what a team said it would do.
CREATE TABLE trials (
    nct_id            text PRIMARY KEY,
    title             text,
    overall_status    text,
    completion_date   text,                     -- as registered; partial dates ("2019-06") are common
    has_results       boolean NOT NULL,
    primary_outcomes  jsonb NOT NULL DEFAULT '[]',
    snapshot_sha256   text NOT NULL,
    loaded_at         timestamptz NOT NULL DEFAULT now()
);

-- How a registration was tied to a paper. Reconciliation verdicts (matched,
-- endpoint drift, no publication found, couldn't match) are step 3.
CREATE TABLE trial_links (
    nct_id     text NOT NULL REFERENCES trials(nct_id),
    source_id  text NOT NULL REFERENCES sources(id),
    method     text NOT NULL CHECK (method IN ('nct_in_abstract', 'nct_in_fulltext')),
    PRIMARY KEY (nct_id, source_id, method)
);

-- The benchmark's answer key: meta-analyses whose conclusions are the
-- expected answers. Deliberately not a source.
CREATE TABLE answer_key (
    pmid     text PRIMARY KEY,
    pmcid    text,
    title    text NOT NULL,
    pub_year int
);

-- Which meta-analysis included which study. Overlap between meta-analyses is
-- how contested answers are found: same trials, different conclusions.
CREATE TABLE inclusions (
    meta_analysis_pmid text NOT NULL REFERENCES answer_key(pmid),
    source_id          text NOT NULL REFERENCES sources(id),
    PRIMARY KEY (meta_analysis_pmid, source_id)
);
