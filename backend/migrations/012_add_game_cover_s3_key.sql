-- Additive and rerunnable. Existing Steam cover URLs remain unchanged.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE game ADD COLUMN IF NOT EXISTS cover_s3_key VARCHAR;
COMMIT;
