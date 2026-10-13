"""Validate migration 003 in a disposable schema, leaving public data untouched."""

import subprocess
from pathlib import Path
from uuid import uuid4


def psql(sql):
    return subprocess.run(
        ["docker-compose", "exec", "-T", "db", "psql", "-U", "backlog",
         "-d", "backlog_beater", "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True, check=True,
    )


def main():
    schema = "hltb_migration_check_" + uuid4().hex
    migration = (Path(__file__).resolve().parents[1] / "backend/migrations/003_add_hltb_enrichment.sql").read_text()
    try:
        psql(f"""
            CREATE SCHEMA {schema};
            SET search_path TO {schema};
            CREATE TABLE game (id INTEGER PRIMARY KEY, steam_app_id INTEGER UNIQUE,
                               name VARCHAR, cover_url VARCHAR);
            CREATE TABLE ownedgame (user_id INTEGER, game_id INTEGER REFERENCES game(id),
                                    completion_status VARCHAR, PRIMARY KEY(user_id, game_id));
            INSERT INTO game VALUES (1, 123, 'Existing title', 'https://example.com/cover');
            INSERT INTO ownedgame VALUES (42, 1, 'finished');
            {migration}
            UPDATE game SET hltb_id=10, main_story_hours=1.5 WHERE id=1;
            INSERT INTO gameenrichment VALUES (1, 'matched', 'unique_exact_title', '[]', now(), now(), NULL);
            INSERT INTO personalhltboverride VALUES (42, 1, 20, 2.5, NULL, NULL, '{{"hltb_id":20}}', now());
            {migration}
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM game WHERE id=1 AND steam_app_id=123
                               AND name='Existing title' AND cover_url='https://example.com/cover'
                               AND hltb_id=10 AND main_story_hours=1.5) THEN
                    RAISE EXCEPTION 'Catalog or HLTB metadata changed';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM ownedgame WHERE user_id=42 AND game_id=1
                               AND completion_status='finished') THEN
                    RAISE EXCEPTION 'Ownership or progress changed';
                END IF;
                IF (SELECT count(*) FROM gameenrichment) <> 1 OR
                   (SELECT hltb_id FROM personalhltboverride) <> 20 THEN
                    RAISE EXCEPTION 'Existing enrichment records changed';
                END IF;
                BEGIN
                    INSERT INTO game (id, steam_app_id, main_story_hours) VALUES (2, 456, 2);
                    RAISE EXCEPTION 'Hours without HLTB ID accepted';
                EXCEPTION WHEN check_violation THEN NULL; END;
                BEGIN
                    UPDATE game SET main_story_hours=-1 WHERE id=1;
                    RAISE EXCEPTION 'Negative hours accepted';
                EXCEPTION WHEN check_violation THEN NULL; END;
                BEGIN
                    INSERT INTO personalhltboverride VALUES (99, 1, 20, NULL, NULL, NULL, '{{}}', now());
                    RAISE EXCEPTION 'Nonowner override accepted';
                EXCEPTION WHEN foreign_key_violation THEN NULL; END;
            END $$;
        """)
        print("Migration passed: legacy data, repeat application, enrichment records and constraints preserved.")
    finally:
        psql(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


if __name__ == "__main__":
    main()
