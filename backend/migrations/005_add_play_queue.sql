-- Additive migration; safe to rerun. Existing progress and ownership are unchanged.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE TABLE IF NOT EXISTS playqueueentry (
    user_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    added_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, game_id),
    FOREIGN KEY (user_id, game_id) REFERENCES ownedgame(user_id, game_id) ON DELETE CASCADE,
    CONSTRAINT queue_nonnegative_position CHECK (position >= 0)
);
COMMIT;
