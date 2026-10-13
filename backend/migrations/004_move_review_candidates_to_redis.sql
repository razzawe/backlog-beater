-- Apply after stopping old enrichment API/workers, before starting new images.
-- Only transient review suggestions are discarded; personal overrides remain.
-- An owner opening an old ambiguous result will trigger a fresh cached search.
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE gameenrichment DROP COLUMN IF EXISTS candidates;
COMMIT;
