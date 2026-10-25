import unittest
from pathlib import Path
from uuid import uuid4
from app.database import engine


class TrailerMigrationTests(unittest.TestCase):
    def test_additive_and_rerunnable(self):
        self.assertEqual(engine.url.database,'steam_test')
        schema='trailers_'+uuid4().hex
        migration=(Path(__file__).resolve().parents[1]/'migrations/011_add_game_trailers.sql').read_text()
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
            try:
                connection.exec_driver_sql(f'SET search_path TO {schema}')
                connection.exec_driver_sql('CREATE TABLE game_metadata(game_id INTEGER PRIMARY KEY, is_free BOOLEAN, genres JSON)')
                connection.exec_driver_sql("INSERT INTO game_metadata VALUES (1,FALSE,'[]')")
                for _ in range(2): connection.exec_driver_sql(migration)
                self.assertEqual(tuple(connection.exec_driver_sql('SELECT game_id,is_free,trailer_url,trailer_format FROM game_metadata').one()),(1,False,None,None))
                connection.exec_driver_sql("UPDATE game_metadata SET trailer_url='https://example.test/video', trailer_format='hls'")
                connection.exec_driver_sql(migration)
                self.assertEqual(connection.exec_driver_sql('SELECT trailer_format FROM game_metadata').scalar_one(),'hls')
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
