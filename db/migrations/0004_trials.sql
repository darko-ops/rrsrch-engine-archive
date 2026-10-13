-- Trial reconciliation verdicts (step 3): one row per (trial, linked paper),
-- or per trial with no linked paper. States are neutral by design: matched,
-- endpoint_drift, no_publication_found, couldnt_match. Reasons and evidence
-- (registry field paths, document offsets) say why, so every state can be
-- checked by hand.

CREATE TABLE trial_reconciliations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nct_id     text NOT NULL REFERENCES trials(nct_id),
    source_id  text REFERENCES sources(id),
    state      text NOT NULL CHECK (state IN ('matched', 'endpoint_drift', 'no_publication_found', 'couldnt_match')),
    reasons    jsonb NOT NULL,
    flags      text[] NOT NULL DEFAULT '{}',    -- registered_after_start, registry_edited_after_publication
    evidence   jsonb NOT NULL,
    rules      text NOT NULL,                   -- rules version
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX trial_reconciliations_nct_idx ON trial_reconciliations (nct_id);
