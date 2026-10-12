-- The reader's bookkeeping.
--
-- claims.self_check: the gate's verdict on the claim against its own passage,
-- kept so anyone can see why the claim was allowed in.
--
-- claim_rejections: every claim the model proposed that a rule refused, and
-- which rule. The rejection rate is a published number, so the rejections
-- are kept, not discarded.
--
-- read_chunks: which stretch of which document was read, by which model and
-- prompt version, so a rerun reads only what's new and a claim can always be
-- traced back to the exact input the model saw.

ALTER TABLE claims ADD COLUMN self_check jsonb;

CREATE TABLE claim_rejections (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents(id),
    doc_sha256  text NOT NULL,
    quote       text,
    claim_text  text,
    reason      text NOT NULL,      -- quote_not_found | gate:<rule> | nothing_checkable | empty_claim | bad_reply
    detail      text,
    extractor   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX claim_rejections_reason_idx ON claim_rejections (reason);

CREATE TABLE read_chunks (
    document_id bigint NOT NULL REFERENCES documents(id),
    chunk_sha   text NOT NULL,
    extractor   text NOT NULL,      -- model | prompt version
    span_start  int NOT NULL,
    span_end    int NOT NULL,
    n_items     int NOT NULL,       -- claims the model proposed
    n_claims    int NOT NULL,       -- claims that passed into the ledger
    read_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, chunk_sha, extractor)
);
