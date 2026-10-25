"""Verify optional S3 keys and preservation of existing covers on migration."""
import unittest
from pathlib import Path
from uuid import uuid4

from app.database import Game, engine


class CoverS3MigrationTests(unittest.TestCase):
    def test_model_defaults_to_no_s3_cover(self):
        game = Game(steam_app_id=620, cover_url="https://example.test/steam.jpg")
        self.assertIsNone(game.cover_s3_key)
        self.assertTrue(Game.__table__.c.cover_s3_key.nullable)

    def test_migration_preserves_legacy_data_and_saved_key_on_rerun(self):
        self.assertEqual(engine.url.database, "steam_test")
        schema = "cover_s3_" + uuid4().hex
        migration = (Path(__file__).resolve().parents[1] /
                     "migrations/012_add_game_cover_s3_key.sql").read_text()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            try:
                connection.exec_driver_sql(f"SET search_path TO {schema}")
                connection.exec_driver_sql("CREATE TABLE game (id INTEGER PRIMARY KEY, "
                    "steam_app_id INTEGER, name VARCHAR, cover_url VARCHAR, hltb_id INTEGER)")
                connection.exec_driver_sql("INSERT INTO game VALUES "
                    "(1,620,'Portal 2','https://example.test/steam.jpg',123)")
                for _ in range(2):
                    connection.exec_driver_sql(migration)
                self.assertEqual(tuple(connection.exec_driver_sql("SELECT * FROM game").one()),
                    (1, 620, "Portal 2", "https://example.test/steam.jpg", 123, None))
                connection.exec_driver_sql("UPDATE game SET cover_s3_key='covers/steam/620.jpg'")
                connection.exec_driver_sql(migration)
                self.assertEqual(connection.exec_driver_sql("SELECT cover_s3_key FROM game").scalar_one(),
                                 "covers/steam/620.jpg")
            finally:
                connection.exec_driver_sql("SET search_path TO public")
                connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
