-- Kanban platform schema (PostgreSQL).
--
-- Hierarchy: project_epic -> feature -> task. Features and tasks may be
-- standalone (parent_id IS NULL); a project_epic never has a parent. The
-- composite foreign key over (parent_id, required_parent_type) makes an
-- invalid parent combination unrepresentable at the storage layer.
--
-- Dependency edges live in card_dependencies and are unrelated to the
-- hierarchy parent link: they model "card X depends on card Y" between any
-- two distinct cards.
--
-- Persisted V1 card fields beyond the original five live on the cards table:
-- owner and priority are optional opaque non-empty strings; requirements and
-- blockers are ordered lists of unique non-empty strings. The blockers
-- column is a free-form card field and is unrelated to dependency-derived
-- blocking (open_dependencies / blocked), which stays computed from
-- card_dependencies.

BEGIN;

CREATE TABLE IF NOT EXISTS cards (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'TODO',
    parent_id BIGINT,
    owner TEXT,
    priority TEXT,
    requirements TEXT[] NOT NULL DEFAULT '{}',
    blockers TEXT[] NOT NULL DEFAULT '{}',
    required_parent_type TEXT GENERATED ALWAYS AS (
        CASE type
            WHEN 'feature' THEN 'project_epic'
            WHEN 'task' THEN 'feature'
        END
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cards_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT cards_title_length CHECK (char_length(title) <= 200),
    CONSTRAINT cards_type_valid CHECK (type IN ('project_epic', 'feature', 'task')),
    CONSTRAINT cards_status_valid CHECK (status IN (
        'TODO',
        'READY',
        'IN_PROGRESS',
        'REVIEW',
        'CHANGES_REQUESTED',
        'APPROVED',
        'MERGED',
        'CLOSED',
        'BLOCKED'
    )),
    CONSTRAINT cards_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT cards_epic_has_no_parent CHECK (type <> 'project_epic' OR parent_id IS NULL),
    CONSTRAINT cards_owner_not_blank CHECK (owner IS NULL OR btrim(owner) <> ''),
    CONSTRAINT cards_priority_not_blank CHECK (priority IS NULL OR btrim(priority) <> ''),
    CONSTRAINT cards_requirements_valid CHECK (
        array_position(requirements, NULL::text) IS NULL
        AND '' <> ALL (requirements)
    ),
    CONSTRAINT cards_blockers_valid CHECK (
        array_position(blockers, NULL::text) IS NULL
        AND '' <> ALL (blockers)
    ),
    CONSTRAINT cards_id_type_key UNIQUE (id, type),
    CONSTRAINT cards_parent_type_fkey FOREIGN KEY (parent_id, required_parent_type)
        REFERENCES cards (id, type)
);

CREATE TABLE IF NOT EXISTS card_dependencies (
    card_id BIGINT NOT NULL REFERENCES cards (id) ON DELETE CASCADE,
    depends_on_card_id BIGINT NOT NULL REFERENCES cards (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, depends_on_card_id),
    CONSTRAINT card_dependencies_no_self CHECK (card_id <> depends_on_card_id)
);

-- Migration for databases created before the persisted card-field extension.
-- Idempotent: on a fresh database the columns already exist (CREATE TABLE
-- above) and these statements are skipped; on a legacy database they add the
-- columns with the same constraints and defaults.
ALTER TABLE cards ADD COLUMN IF NOT EXISTS owner TEXT
    CONSTRAINT cards_owner_not_blank CHECK (owner IS NULL OR btrim(owner) <> '');
ALTER TABLE cards ADD COLUMN IF NOT EXISTS priority TEXT
    CONSTRAINT cards_priority_not_blank CHECK (priority IS NULL OR btrim(priority) <> '');
ALTER TABLE cards ADD COLUMN IF NOT EXISTS requirements TEXT[] NOT NULL DEFAULT '{}'
    CONSTRAINT cards_requirements_valid CHECK (
        array_position(requirements, NULL::text) IS NULL
        AND '' <> ALL (requirements)
    );
ALTER TABLE cards ADD COLUMN IF NOT EXISTS blockers TEXT[] NOT NULL DEFAULT '{}'
    CONSTRAINT cards_blockers_valid CHECK (
        array_position(blockers, NULL::text) IS NULL
        AND '' <> ALL (blockers)
    );

CREATE INDEX IF NOT EXISTS cards_parent_id_idx ON cards (parent_id);
CREATE INDEX IF NOT EXISTS card_dependencies_depends_on_idx
    ON card_dependencies (depends_on_card_id);

COMMIT;
