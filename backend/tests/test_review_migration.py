import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from app.database import engine


class ReviewMigrationTests(unittest.TestCase):
    def test_removes_legacy_shortlists_without_touching_personal_data(self):
        self.assertEqual(engine.url.database, 'steam_test')
        schema = 'review_migration_' + uuid4().hex
        sql = (Path(__file__).resolve().parents[1] / 'migrations/004_move_review_candidates_to_redis.sql').read_text()
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
            try:
                connection.execute(text(f'SET search_path TO {schema}'))
                connection.execute(text("CREATE TABLE gameenrichment(game_id integer PRIMARY KEY, status text, candidates json);"))
                connection.execute(text("CREATE TABLE personalhltboverride(user_id integer, game_id integer, hltb_id integer, candidate json);"))
                connection.execute(text("INSERT INTO gameenrichment VALUES (1, 'ambiguous', '[1,2]');"))
                connection.exec_driver_sql("INSERT INTO personalhltboverride VALUES (1,1,20,'{\"hltb_id\":20}');")
                for _ in range(2):
                    connection.exec_driver_sql(sql)
                self.assertEqual(connection.execute(text('SELECT status FROM gameenrichment')).scalar_one(), 'ambiguous')
                self.assertEqual(connection.execute(text('SELECT hltb_id FROM personalhltboverride')).scalar_one(), 20)
                self.assertEqual(connection.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=:schema AND table_name='gameenrichment' AND column_name='candidates'"), {'schema': schema}).scalar_one(), 0)
            finally:
                connection.execute(text('SET search_path TO public'))
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
