-- Add HLTB metadata without changing existing catalog, ownership or progress.
-- Apply with psql -v ON_ERROR_STOP=1. Safe to re-run on the current schema.
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE game
    ADD COLUMN IF NOT EXISTS hltb_id INTEGER,
    ADD COLUMN IF NOT EXISTS main_story_hours DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS main_extra_hours DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS completionist_hours DOUBLE PRECISION;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'game'::regclass AND conname = 'game_positive_hltb_id') THEN
        ALTER TABLE game ADD CONSTRAINT game_positive_hltb_id CHECK (hltb_id IS NULL OR hltb_id > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'game'::regclass AND conname = 'game_hours_require_hltb_id') THEN
        ALTER TABLE game ADD CONSTRAINT game_hours_require_hltb_id CHECK (
            hltb_id IS NOT NULL OR (main_story_hours IS NULL AND main_extra_hours IS NULL AND completionist_hours IS NULL)
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'game'::regclass AND conname = 'game_positive_hours') THEN
        ALTER TABLE game ADD CONSTRAINT game_positive_hours CHECK (
            (main_story_hours IS NULL OR main_story_hours > 0) AND
            (main_extra_hours IS NULL OR main_extra_hours > 0) AND
            (completionist_hours IS NULL OR completionist_hours > 0)
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS gameenrichment (
    game_id INTEGER PRIMARY KEY REFERENCES game(id),
    status VARCHAR(13) NOT NULL,
    reason VARCHAR NOT NULL,
    candidates JSON NOT NULL,
    last_lookup_at TIMESTAMPTZ NOT NULL,
    last_success_at TIMESTAMPTZ,
    last_error VARCHAR,
    CONSTRAINT enrichment_status CHECK (status IN ('matched', 'ambiguous', 'no_match', 'lookup_failed'))
);

CREATE TABLE IF NOT EXISTS personalhltboverride (
    user_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    hltb_id INTEGER NOT NULL,
    main_story_hours DOUBLE PRECISION,
    main_extra_hours DOUBLE PRECISION,
    completionist_hours DOUBLE PRECISION,
    candidate JSON NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, game_id),
    FOREIGN KEY (user_id, game_id) REFERENCES ownedgame(user_id, game_id),
    CONSTRAINT override_positive_hltb_id CHECK (hltb_id > 0),
    CONSTRAINT override_positive_hours CHECK (
        (main_story_hours IS NULL OR main_story_hours > 0) AND
        (main_extra_hours IS NULL OR main_extra_hours > 0) AND
        (completionist_hours IS NULL OR completionist_hours > 0)
    )
);

COMMIT;
