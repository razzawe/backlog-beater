BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE game_metadata ADD COLUMN IF NOT EXISTS trailer_url VARCHAR;
ALTER TABLE game_metadata ADD COLUMN IF NOT EXISTS trailer_thumbnail VARCHAR;
ALTER TABLE game_metadata ADD COLUMN IF NOT EXISTS trailer_title VARCHAR;
ALTER TABLE game_metadata ADD COLUMN IF NOT EXISTS trailer_format VARCHAR;
COMMIT;
