-- Skeptic findings (step 3): where two ledger claims agree or contradict.
-- Only those two verdicts are stored; couldnt_compare is silence, not a row.
-- claim_a_id < claim_b_id always, so each pair appears once.

CREATE TABLE claim_agreements (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_a_id  bigint NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    claim_b_id  bigint NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    verdict     text NOT NULL CHECK (verdict IN ('agrees', 'contradicts')),
    rule        text NOT NULL,                   -- direction, entities, ...
    detail      text NOT NULL,
    evidence    jsonb NOT NULL,
    rules       text NOT NULL,                   -- rules version
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (claim_a_id < claim_b_id),
    UNIQUE (claim_a_id, claim_b_id)
);
CREATE INDEX claim_agreements_verdict_idx ON claim_agreements (verdict);
