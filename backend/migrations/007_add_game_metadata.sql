-- Additive and rerunnable. No metadata backfill or job creation.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS game_metadata (
    game_id INTEGER PRIMARY KEY REFERENCES game(id),
    single_player BOOLEAN,
    genres JSON NOT NULL,
    last_lookup_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error VARCHAR,
    CONSTRAINT game_metadata_genres_array CHECK (json_typeof(genres) = 'array')
);
CREATE TABLE IF NOT EXISTS game_metadata_jobs (
    game_id INTEGER PRIMARY KEY REFERENCES game(id),
    status VARCHAR(9) NOT NULL,
    attempt_token VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL,
    last_published_at TIMESTAMPTZ,
    last_error VARCHAR,
    CONSTRAINT game_metadata_job_status CHECK (status IN ('ready','pending','running','completed','failed'))
);
CREATE INDEX IF NOT EXISTS ix_game_metadata_jobs_status_updated
    ON game_metadata_jobs(status, updated_at);
COMMIT;
