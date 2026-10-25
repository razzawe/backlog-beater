import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from redis import Redis
from sqlmodel import Session, SQLModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'enrichment-worker'))
from enrichment_worker import hltb_pacing, jobs
from app.database import Game, EnrichmentJob, JobStatus, engine


class HLTBSpacingTests(unittest.TestCase):
    def setUp(self):
        self.client = Redis.from_url(os.environ['HLTB_REVIEW_CACHE_URL'])
        for attr in ('SPACING', 'COOLDOWN'):
            patcher = patch.object(hltb_pacing, attr, 'test:spacing:' + attr)
            patcher.start()
            self.addCleanup(patcher.stop)
            self.client.delete('test:spacing:' + attr)
        patcher = patch.object(hltb_pacing, 'client', self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_spacing_waits_locally_and_ignores_old_active_lease(self):
        import time
        self.client.set('hltb:global:active:v1', 'obsolete', px=45000)
        self.addCleanup(self.client.delete, 'hltb:global:active:v1')
        self.assertEqual(hltb_pacing.wait_for_slot(), 0)
        # Shorten spacing for a fast real Redis test.
        self.client.pexpire(hltb_pacing.SPACING, 100)
        started = time.monotonic()
        self.assertEqual(hltb_pacing.wait_for_slot(), 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.08)
        self.assertGreater(self.client.pttl(hltb_pacing.SPACING), 2500)

    def test_long_provider_cooldown_defers_and_never_shortens(self):
        hltb_pacing.cooldown(60)
        hltb_pacing.cooldown(1)
        with patch.object(hltb_pacing.time, 'sleep') as sleep:
            self.assertGreater(hltb_pacing.wait_for_slot(), 59)
            sleep.assert_not_called()

    def test_cooldown_deferral_does_not_claim_processing_attempt(self):
        from celery.exceptions import Retry
        from enrichment_worker.tasks import enrich_game, refresh_review
        with patch.object(hltb_pacing, 'wait_for_slot', return_value=60), patch(
                'enrichment_worker.tasks.jobs.claim_running') as claim:
            for task in (enrich_game, refresh_review):
                with patch.object(task, 'retry', side_effect=Retry()) as retry:
                    with self.assertRaises(Retry):
                        task.run(1, 'token')
                    self.assertGreaterEqual(retry.call_args.kwargs['countdown'], 60)
            claim.assert_not_called()

    def test_redis_failure_defers_without_provider_access(self):
        from redis.exceptions import ConnectionError
        with patch.object(self.client, 'eval', side_effect=ConnectionError('offline')):
            self.assertEqual(hltb_pacing.wait_for_slot(), 10)


class HLTBRetryTests(unittest.TestCase):
    def test_three_attempts_backoff_and_manual_reset(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            game = Game(steam_app_id=620)
            session.add(game)
            session.flush()
            game_id = game.id
            jobs.create_ready(session, [game_id])
            session.commit()
        from unittest.mock import MagicMock
        for attempt in range(1, 4):
            publisher = MagicMock()
            jobs.dispatch(publisher)
            token = publisher.send_task.call_args.kwargs['args'][1]
            self.assertTrue(jobs.claim_running(game_id, token))
            with Session(engine) as session:
                job = jobs.lock_attempt(session, game_id, token)
                self.assertEqual(job.attempt_count, attempt)
                jobs.finish(job, failed=True, error='lookup_timeout')
                retried = jobs.schedule_retry(job)
                self.assertEqual(retried, attempt < 3)
                session.add(job)
                session.commit()
            if retried:
                self.assertEqual(jobs.dispatch(MagicMock()), 0)  # Honor retry delay.
                with Session(engine) as session:
                    job = session.get(EnrichmentJob, game_id)
                    job.next_attempt_at = None
                    session.add(job)
                    session.commit()
        self.assertEqual(jobs.dispatch(MagicMock()), 0)
        self.assertTrue(jobs.retry_failed(game_id))
        with Session(engine) as session:
            self.assertEqual(session.get(EnrichmentJob, game_id).attempt_count, 0)

    def test_retry_migration_rerunnable_preserves_existing_job(self):
        from uuid import uuid4
        schema = 'retry_migration_' + uuid4().hex
        root = Path(__file__).resolve().parents[1] / 'migrations'
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
            try:
                connection.exec_driver_sql(f'SET search_path TO {schema}')
                connection.exec_driver_sql('CREATE TABLE game(id INTEGER PRIMARY KEY)')
                connection.exec_driver_sql('INSERT INTO game VALUES (1)')
                connection.exec_driver_sql((root / '006_add_enrichment_jobs.sql').read_text())
                connection.exec_driver_sql("INSERT INTO enrichment_jobs (game_id,status,updated_at) VALUES (1,'failed',NOW())")
                for _ in range(2):
                    connection.exec_driver_sql((root / '008_add_hltb_retries.sql').read_text())
                row = connection.exec_driver_sql('SELECT status,attempt_count,next_attempt_at FROM enrichment_jobs').one()
                self.assertEqual(tuple(row), ('failed', 0, None))
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
