BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE game_metadata ADD COLUMN IF NOT EXISTS multiplayer BOOLEAN;
COMMIT;
