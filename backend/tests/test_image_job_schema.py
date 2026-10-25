"""Isolated PostgreSQL checks for image schema, migration, and constraints."""
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from app.database import ImageBatch, ImageJob, engine


class ImageJobSchemaTests(unittest.TestCase):
    def test_model_defaults(self):
        batch = ImageBatch(user_id=1)
        self.assertEqual((batch.status, batch.max_games), ('ready', 115))
        job = ImageJob(batch_id=batch.id, game_id=1)
        self.assertEqual((job.status, job.attempt_count, job.source_url), ('ready', 0, None))

    def test_rerun_preservation_and_constraints(self):
        self.assertEqual(engine.url.database, 'steam_test')
        schema = 'image_schema_' + uuid4().hex
        root = Path(__file__).resolve().parents[1] / 'migrations'
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
            try:
                connection.exec_driver_sql(f'SET search_path TO {schema}')
                connection.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
                connection.exec_driver_sql('CREATE TABLE game (id INTEGER PRIMARY KEY, cover_url VARCHAR)')
                connection.exec_driver_sql('INSERT INTO "user" VALUES (1)')
                connection.exec_driver_sql("INSERT INTO game VALUES (1,'https://example.test/steam.jpg')")
                for _ in range(2):
                    for file in ('012_add_game_cover_s3_key.sql', '013_add_image_batches_and_jobs.sql'):
                        connection.exec_driver_sql((root / file).read_text())
                self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM image_jobs').scalar_one(), 0)
                connection.exec_driver_sql("INSERT INTO image_batches VALUES ('batch',1,'ready',115,now(),NULL)")
                connection.exec_driver_sql("INSERT INTO image_jobs (batch_id,game_id,status,attempt_count,updated_at) "
                                           "VALUES ('batch',1,'ready',0,now())")
                connection.exec_driver_sql((root / '013_add_image_batches_and_jobs.sql').read_text())
                self.assertEqual(tuple(connection.exec_driver_sql('SELECT cover_url,cover_s3_key FROM game').one()),
                                 ('https://example.test/steam.jpg', None))
                self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM image_jobs').scalar_one(), 1)
                invalid = [
                    "INSERT INTO image_jobs (batch_id,game_id,status,attempt_count,updated_at) VALUES ('batch',1,'ready',0,now())",
                    "UPDATE image_jobs SET status='bogus'",
                    "UPDATE image_jobs SET attempt_count=-1",
                    "UPDATE image_jobs SET game_id=999",
                    "UPDATE image_jobs SET batch_id='missing'",
                    "UPDATE image_batches SET user_id=999",
                    "UPDATE image_batches SET max_games=116",
                    "UPDATE image_batches SET status='bogus'",
                ]
                for sql in invalid:
                    with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                        connection.exec_driver_sql(sql)
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
