"""PostgreSQL integration tests for attempt ownership and dispatch recovery."""
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from sqlmodel import SQLModel, Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'enrichment-worker'))
from app.database import EnrichmentJob, Game, JobStatus, engine
from enrichment_worker import jobs


class EnrichmentJobTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            game = Game(steam_app_id=620, name='Portal 2')
            session.add(game)
            session.flush()
            self.game_id = game.id
            jobs.create_ready(session, [game.id])
            session.commit()

    def dispatch(self):
        publisher = MagicMock()
        jobs.dispatch(publisher)
        return publisher

    def test_concurrent_schedulers_and_duplicate_delivery(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            publishers = list(pool.map(lambda _: self.dispatch(), range(2)))
        calls = [p.send_task.call_args for p in publishers if p.send_task.called]
        self.assertEqual(len(calls), 1)
        game_id, token = calls[0].kwargs['args']
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: jobs.claim_running(game_id, token), range(2)))
        self.assertEqual(sorted(claims), [False, True])

    def test_broker_failure_and_old_pending_republication(self):
        publisher = MagicMock()
        publisher.connection_for_write.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            jobs.dispatch(publisher)
        with Session(engine) as session:
            job = session.get(EnrichmentJob, self.game_id)
            self.assertEqual(job.status, JobStatus.PENDING)
            token = job.attempt_token
            job.last_published_at = datetime.now(UTC) - timedelta(seconds=301)
            session.add(job)
            session.commit()
        publisher = self.dispatch()
        self.assertEqual(publisher.send_task.call_args.kwargs['args'], [self.game_id, token])
        self.assertTrue(jobs.claim_running(self.game_id, token))

    def test_timeout_retry_fences_old_attempt_and_import_does_not_reset(self):
        token = self.dispatch().send_task.call_args.kwargs['args'][1]
        self.assertTrue(jobs.claim_running(self.game_id, token))
        with Session(engine) as session:
            job = session.get(EnrichmentJob, self.game_id)
            job.updated_at = datetime.now(UTC) - timedelta(seconds=121)
            session.add(job)
            session.commit()
        self.dispatch()
        with Session(engine) as session:
            jobs.create_ready(session, [self.game_id])
            session.commit()
            self.assertEqual(session.get(EnrichmentJob, self.game_id).status, JobStatus.FAILED)
            self.assertIsNone(jobs.lock_attempt(session, self.game_id, token))
        self.assertTrue(jobs.retry_failed(self.game_id))
        new_token = self.dispatch().send_task.call_args.kwargs['args'][1]
        self.assertNotEqual(token, new_token)
        self.assertFalse(jobs.claim_running(self.game_id, token))
        self.assertTrue(jobs.claim_running(self.game_id, new_token))
        jobs.fail_attempt(self.game_id, token, 'old_failure')
        with Session(engine) as session:
            job = jobs.lock_attempt(session, self.game_id, new_token)
            self.assertIsNotNone(job)
            jobs.finish(job)
            session.add(job)
            session.commit()
        self.assertFalse(self.dispatch().send_task.called)


class EnrichmentJobMigrationTests(unittest.TestCase):
    def test_additive_rerunnable_migration_preserves_existing_data(self):
        from uuid import uuid4
        from sqlalchemy import text
        self.assertEqual(engine.url.database, 'steam_test')
        schema = 'job_migration_' + uuid4().hex
        sql = (Path(__file__).resolve().parents[1] /
               'migrations/006_add_enrichment_jobs.sql').read_text()
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
            try:
                connection.execute(text(f'SET search_path TO {schema}'))
                connection.exec_driver_sql('CREATE TABLE game(id INTEGER PRIMARY KEY, name TEXT)')
                connection.exec_driver_sql("INSERT INTO game VALUES (1, 'Existing game')")
                connection.exec_driver_sql(sql)
                self.assertEqual(connection.exec_driver_sql(
                    'SELECT count(*) FROM enrichment_jobs').scalar_one(), 0)
                connection.exec_driver_sql("INSERT INTO enrichment_jobs "
                    "(game_id, status, updated_at) VALUES (1, 'ready', NOW())")
                connection.exec_driver_sql(sql)
                self.assertEqual(connection.exec_driver_sql(
                    'SELECT name FROM game').scalar_one(), 'Existing game')
                self.assertEqual(connection.exec_driver_sql(
                    'SELECT status FROM enrichment_jobs').scalar_one(), 'ready')
                from sqlalchemy.exc import IntegrityError
                for statement in (
                    "INSERT INTO enrichment_jobs VALUES (1, 'ready', NULL, NOW(), NULL, NULL)",
                    "INSERT INTO enrichment_jobs VALUES (999, 'ready', NULL, NOW(), NULL, NULL)",
                    "UPDATE enrichment_jobs SET status = 'invalid' WHERE game_id = 1",
                ):
                    with self.assertRaises(IntegrityError):
                        connection.exec_driver_sql(statement)
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
