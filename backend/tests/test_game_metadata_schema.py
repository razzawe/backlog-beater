"""Metadata model round trips and additive migration contract."""
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from app.database import Game, GameMetadata, GameMetadataJob, JobStatus, engine


class GameMetadataSchemaTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            game = Game(steam_app_id=620, name='Portal 2', hltb_id=123, main_story_hours=8)
            session.add(game)
            session.flush()
            from app.database import (User, OwnedGame, CompletionStatus, PlayQueueEntry,
                                      PersonalHLTBOverride, GameEnrichment, EnrichmentStatus,
                                      EnrichmentJob)
            from datetime import UTC, datetime
            user = User(username='metadata-owner', hashed_password='unused')
            session.add(user)
            session.flush()
            session.add(OwnedGame(user_id=user.id, game_id=game.id,
                                  completion_status=CompletionStatus.FINISHED))
            session.flush()
            session.add(PlayQueueEntry(user_id=user.id, game_id=game.id, position=0))
            session.add(PersonalHLTBOverride(user_id=user.id, game_id=game.id,
                hltb_id=456, main_story_hours=7, candidate={'hltb_id': 456},
                selected_at=datetime.now(UTC)))
            session.add(GameEnrichment(game_id=game.id, status=EnrichmentStatus.MATCHED,
                reason='exact', last_lookup_at=datetime.now(UTC)))
            session.add(EnrichmentJob(game_id=game.id, status=JobStatus.COMPLETED))
            session.commit()
            self.game_id = game.id

    def test_unknown_mode_genres_and_independent_jobs_round_trip(self):
        from app.database import EnrichmentJob
        with Session(engine) as session:
            session.add(GameMetadata(game_id=self.game_id))
            session.add(GameMetadataJob(game_id=self.game_id))
            session.commit()
            record = session.get(GameMetadata, self.game_id)
            self.assertIsNone(record.single_player)
            self.assertEqual(record.genres, [])
            self.assertIsNone(record.last_success_at)
            self.assertEqual(session.get(GameMetadataJob, self.game_id).status, JobStatus.READY)
            self.assertEqual(session.get(EnrichmentJob, self.game_id).status, JobStatus.COMPLETED)
            for support in (True, False, None):
                record.single_player = support
                record.genres = [{'id': '1', 'name': 'Action'}]
                session.add(record)
                session.commit()
                session.expire_all()
                self.assertIs(session.get(GameMetadata, self.game_id).single_player, support)
                self.assertEqual(session.get(GameMetadata, self.game_id).genres,
                                 [{'id': '1', 'name': 'Action'}])
            self.assertEqual(session.get(Game, self.game_id).main_story_hours, 8)

    def test_migration_rerun_preserves_existing_tables_and_enforces_constraints(self):
        schema = 'metadata_migration_' + uuid4().hex
        migration = (Path(__file__).resolve().parents[1] /
                     'migrations/007_add_game_metadata.sql').read_text()
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
            try:
                connection.exec_driver_sql(f'SET search_path TO {schema}')
                # Clone the existing populated schema, including ownership/progress,
                # overrides, queues, HLTB outcomes, and enrichment jobs.
                tables = list(SQLModel.metadata.sorted_tables)
                for table in tables:
                    if table.name in {'game_metadata', 'game_metadata_jobs'}:
                        continue
                    connection.exec_driver_sql(f'CREATE TABLE "{table.name}" (LIKE public."{table.name}" INCLUDING ALL)')
                    connection.exec_driver_sql(f'INSERT INTO "{table.name}" SELECT * FROM public."{table.name}"')
                snapshots = {t.name: connection.exec_driver_sql(
                    f'SELECT row_to_json(t)::text FROM "{t.name}" t ORDER BY row_to_json(t)::text').all()
                    for t in tables if t.name not in {'game_metadata', 'game_metadata_jobs'}}
                connection.exec_driver_sql(migration)
                upgrade = (Path(__file__).resolve().parents[1] / 'migrations/009_add_multiplayer_metadata.sql').read_text()
                connection.exec_driver_sql(upgrade)
                connection.exec_driver_sql(upgrade)
                for name in ('game_metadata', 'game_metadata_jobs'):
                    self.assertEqual(connection.exec_driver_sql(f'SELECT count(*) FROM {name}').scalar_one(), 0)
                connection.execute(text("INSERT INTO game_metadata (game_id, genres) VALUES (:id, '[]')"), {'id': self.game_id})
                connection.execute(text("INSERT INTO game_metadata_jobs (game_id,status,updated_at) VALUES (:id,'ready',NOW())"), {'id': self.game_id})
                connection.exec_driver_sql(migration)
                for name, snapshot in snapshots.items():
                    self.assertEqual(connection.exec_driver_sql(
                        f'SELECT row_to_json(t)::text FROM "{name}" t ORDER BY row_to_json(t)::text').all(), snapshot)
                self.assertEqual(connection.exec_driver_sql('SELECT status FROM game_metadata_jobs').scalar_one(), 'ready')
                for statement in (
                    "INSERT INTO game_metadata (game_id, genres) VALUES (999999, '[]')",
                    f"INSERT INTO game_metadata (game_id, genres) VALUES ({self.game_id}, '[]')",
                    f"UPDATE game_metadata SET genres = '{{}}' WHERE game_id = {self.game_id}",
                    f"UPDATE game_metadata SET genres = NULL WHERE game_id = {self.game_id}",
                    "UPDATE game_metadata_jobs SET status = 'invalid'",
                    "INSERT INTO game_metadata_jobs (game_id,status,updated_at) VALUES (999999,'ready',NOW())",
                ):
                    with self.assertRaises(IntegrityError):
                        connection.exec_driver_sql(statement)
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
