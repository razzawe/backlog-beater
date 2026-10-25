-- Additive; does not enqueue or backfill existing games.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    game_id INTEGER PRIMARY KEY REFERENCES game(id),
    status VARCHAR NOT NULL CHECK (status IN ('ready','pending','running','completed','failed')),
    attempt_token VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL,
    last_published_at TIMESTAMPTZ,
    last_error VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_enrichment_jobs_status_updated
    ON enrichment_jobs(status, updated_at);
COMMIT;
