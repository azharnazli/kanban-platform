-- Evidence journal extension (additive; applied by kanban_pg_adapter.py).
--
-- The base schema in schema.sql has no place for acceptance criteria, reviews,
-- or integration evidence. The HTTP workflow gates persist those through the
-- optional two-method EvidenceStore extension (kanban_http/domain.py), mapped
-- by the adapter onto this append-only table:
--
--   kind 'acceptance_criteria' -> {"criteria": [...], "created_at": ...}
--   kind 'review'              -> {id, criterion_id, verdict, tested_revision,
--                                  actor, created_at}
--   kind 'integration'         -> {id, tested_revision, integration_ref,
--                                  checks, actor, created_at}
--
-- Rows are immutable and ordered by id (insertion order). The record column
-- stores the caller record verbatim as JSONB; the row id is merged into the
-- record on read. schema.sql and kanban_pg.py are intentionally untouched.

BEGIN;

CREATE TABLE IF NOT EXISTS card_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id BIGINT NOT NULL REFERENCES cards (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS card_evidence_card_kind_idx
    ON card_evidence (card_id, kind, id);

COMMIT;
