-- Run once against an existing database; create_all does not alter existing tables.
BEGIN;

ALTER TABLE "user"
    ADD COLUMN steam_id VARCHAR(20),
    ADD CONSTRAINT user_steam_id_key UNIQUE (steam_id);

COMMIT;