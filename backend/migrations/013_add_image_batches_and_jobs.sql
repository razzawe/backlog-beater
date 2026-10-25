-- Schema only: creates no batches/jobs and triggers no AWS operations.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS image_batches (
    id VARCHAR PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id),
    status VARCHAR NOT NULL,
    max_games INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    CONSTRAINT image_batch_status CHECK (status IN ('ready','running','completed','cancelled')),
    CONSTRAINT image_batch_max_games CHECK (max_games BETWEEN 1 AND 115)
);
CREATE INDEX IF NOT EXISTS ix_image_batches_user_created ON image_batches(user_id, created_at);
CREATE TABLE IF NOT EXISTS image_jobs (
    batch_id VARCHAR NOT NULL REFERENCES image_batches(id),
    game_id INTEGER NOT NULL REFERENCES game(id),
    source_url VARCHAR,
    status VARCHAR NOT NULL,
    attempt_token VARCHAR,
    attempt_count INTEGER NOT NULL,
    next_attempt_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    last_published_at TIMESTAMPTZ,
    last_error VARCHAR,
    PRIMARY KEY (batch_id, game_id),
    CONSTRAINT image_job_status CHECK (status IN ('ready','pending','running','completed','skipped','failed','cancelled')),
    CONSTRAINT image_job_attempt_count CHECK (attempt_count >= 0)
);
CREATE INDEX IF NOT EXISTS ix_image_jobs_status_due ON image_jobs(status, next_attempt_at, updated_at);
CREATE INDEX IF NOT EXISTS ix_image_jobs_game_status ON image_jobs(game_id, status);
COMMIT;
