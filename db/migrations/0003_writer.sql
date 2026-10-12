-- The writer's output, sentence by sentence.
--
-- Every sentence the model proposed is kept, with the claims it cited and the
-- gate's verdict. A report shows only the sentences that passed; faithfulness
-- is passed / proposed, and the refused ones stay here with the rule that
-- refused them.

CREATE TABLE reports (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question    text NOT NULL,
    bench_id    text,                       -- benchmark question id, when run from the benchmark
    verdict     text,                       -- the writer's verdict on the evidence
    writer      text NOT NULL,              -- model | prompt version
    claims_shown bigint[] NOT NULL,         -- the claims the model was given
    bad_reply   text,                       -- why the model's reply couldn't be used, if it couldn't
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE report_sentences (
    report_id   bigint NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    position    int NOT NULL,
    text        text NOT NULL,
    claim_ids   bigint[] NOT NULL,
    passed      boolean NOT NULL,
    rule        text NOT NULL,              -- pass | cites | unknown_claim | scope | direction | entities | numbers
    detail      text,
    check_log   jsonb,
    PRIMARY KEY (report_id, position)
);

CREATE TABLE report_coverage (
    report_id   bigint NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    point       text NOT NULL,
    made        boolean NOT NULL,
    judge       text NOT NULL,              -- model | prompt version: coverage is model-judged
    PRIMARY KEY (report_id, point)
);
