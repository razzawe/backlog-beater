"""Metadata dispatch, persistence, pacing, and stale-attempt integration."""
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import SQLModel, Session
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'enrichment-worker'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'metadata-worker'))
from app.database import Game, GameMetadata, GameMetadataJob, JobStatus, engine
from metadata_worker import metadata_jobs
from metadata_worker.tasks import collect_metadata
from enrichment_worker.tasks import games_imported
from metadata_worker.steam_metadata import SteamMetadata, SteamGenre, SteamMetadataError


class MetadataJobTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            game = Game(steam_app_id=620, name='Portal 2', hltb_id=123)
            session.add(game)
            session.flush()
            self.game_id = game.id
            metadata_jobs.create_ready(session, [game.id])
            session.commit()
        self.result = SteamMetadata(True, (SteamGenre('1', 'Action'),), trailer_url='https://video.akamai.steamstatic.com/a.m3u8', trailer_format='hls')

    def dispatch(self):
        publisher = MagicMock()
        metadata_jobs.dispatch(publisher)
        return publisher

    def test_concurrent_dispatch_claim_and_duplicate_lookup(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            publishers = list(pool.map(lambda _: self.dispatch(), range(2)))
        calls = [p.send_task.call_args for p in publishers if p.send_task.called]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs['queue'], 'steam_metadata')
        args = calls[0].kwargs['args']
        with patch('metadata_worker.tasks.wait_for_slot'), patch(
                'metadata_worker.tasks.lookup_metadata', new_callable=AsyncMock, return_value=self.result) as lookup:
            self.assertEqual(collect_metadata.run(*args), 'completed')
            self.assertEqual(collect_metadata.run(*args), 'skipped')
            lookup.assert_awaited_once_with(620)
        with Session(engine) as session:
            record = session.get(GameMetadata, self.game_id)
            self.assertTrue(record.single_player)
            self.assertEqual(record.trailer_format, 'hls')
            self.assertEqual(record.trailer_url, self.result.trailer_url)
            self.assertEqual(record.genres, [{'id': '1', 'name': 'Action'}])
            self.assertIsNotNone(record.last_success_at)
            self.assertEqual(session.get(GameMetadataJob, self.game_id).status, JobStatus.COMPLETED)
            self.assertEqual(session.get(Game, self.game_id).hltb_id, 123)
        with patch('enrichment_worker.tasks.app.connection_for_write'), patch('enrichment_worker.tasks.app.send_task') as send:
            games_imported.run(schema_version=1, steam_app_ids=[620, 620])
            send.assert_not_called()

    def test_cover_replaces_fallback_and_survives_sync(self):
        from sync_service.steam import insert_missing_games, OwnedGames, SteamGame
        fallback = 'https://cdn.akamai.steamstatic.com/steam/apps/620/header.jpg'
        actual = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/620/hash/header.jpg'
        with Session(engine) as session:
            game = session.get(Game, self.game_id)
            game.cover_url = fallback
            session.add(game)
            session.commit()
        args = self.dispatch().send_task.call_args.kwargs['args']
        with patch('metadata_worker.tasks.wait_for_slot'), patch(
                'metadata_worker.tasks.lookup_metadata', new_callable=AsyncMock,
                return_value=SteamMetadata(True, (), actual)):
            self.assertEqual(collect_metadata.run(*args), 'completed')
        with Session(engine) as session:
            insert_missing_games(session, OwnedGames(game_count=1,
                games=[SteamGame(appid=620, name='Portal 2')]))
            session.commit()
            self.assertEqual(session.get(Game, self.game_id).cover_url, actual)

    def test_metadata_preserves_existing_nonfallback_cover(self):
        existing = 'https://example.com/custom.jpg'
        with Session(engine) as session:
            game = session.get(Game, self.game_id)
            game.cover_url = existing
            session.add(game)
            session.commit()
        args = self.dispatch().send_task.call_args.kwargs['args']
        with patch('metadata_worker.tasks.wait_for_slot'), patch(
                'metadata_worker.tasks.lookup_metadata', new_callable=AsyncMock,
                return_value=SteamMetadata(True, (), 'https://shared.akamai.steamstatic.com/new.jpg')):
            self.assertEqual(collect_metadata.run(*args), 'completed')
        with Session(engine) as session:
            self.assertEqual(session.get(Game, self.game_id).cover_url, existing)

    def test_library_returns_owned_game_modes_and_keeps_them_on_status_update(self):
        from app.database import User, OwnedGame, CompletionStatus
        from library_service.games import get_owned_games, update_completion_status, UpdateCompletionStatusRequest
        with Session(engine) as session:
            user = User(username='mode-owner', hashed_password='unused')
            session.add(user)
            session.flush()
            session.add(OwnedGame(user_id=user.id, game_id=self.game_id))
            session.add(GameMetadata(game_id=self.game_id, single_player=True, multiplayer=True))
            session.add(Game(steam_app_id=9999, name='Not owned'))
            session.commit()
            rows = get_owned_games(user_id=user.id, session=session)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].single_player)
            self.assertTrue(rows[0].multiplayer)
            updated = update_completion_status(self.game_id,
                UpdateCompletionStatusRequest(completion_status=CompletionStatus.FINISHED),
                user_id=user.id, session=session)
            self.assertTrue(updated.multiplayer)

    def test_mode_backfill_preserves_existing_metadata_and_cover(self):
        from metadata_worker.backfill_modes import backfill
        with Session(engine) as session:
            session.add(GameMetadata(game_id=self.game_id, single_player=True,
                genres=[{'id': '25', 'name': 'Adventure'}], last_success_at=datetime.now(UTC)))
            game = session.get(Game, self.game_id)
            game.cover_url = 'https://example.com/keep.jpg'
            session.add(game)
            session.commit()
        with patch('metadata_worker.backfill_modes.wait_for_slot'), patch(
                'metadata_worker.backfill_modes.lookup_metadata', new_callable=AsyncMock,
                return_value=SteamMetadata(False, (), None, True)):
            self.assertEqual(backfill(limit=1)['updated'], 1)
        with Session(engine) as session:
            record = session.get(GameMetadata, self.game_id)
            self.assertTrue(record.multiplayer)
            self.assertTrue(record.single_player)
            self.assertEqual(record.genres, [{'id': '25', 'name': 'Adventure'}])
            self.assertEqual(session.get(Game, self.game_id).cover_url, 'https://example.com/keep.jpg')

    def test_failure_is_persisted_and_retry_is_explicit(self):
        args = self.dispatch().send_task.call_args.kwargs['args']
        with patch('metadata_worker.tasks.wait_for_slot'), patch(
                'metadata_worker.tasks.lookup_metadata', new_callable=AsyncMock, side_effect=SteamMetadataError('bad')):
            self.assertEqual(collect_metadata.run(*args), 'failed')
        with Session(engine) as session:
            metadata_jobs.create_ready(session, [self.game_id])
            session.commit()
            self.assertIsNone(session.get(GameMetadata, self.game_id).last_success_at)
            self.assertIsNone(session.get(GameMetadata, self.game_id).single_player)
            self.assertEqual(session.get(GameMetadataJob, self.game_id).status, JobStatus.FAILED)
        self.assertFalse(self.dispatch().send_task.called)
        self.assertTrue(metadata_jobs.retry_failed(self.game_id))
        new_args = self.dispatch().send_task.call_args.kwargs['args']
        self.assertNotEqual(new_args[1], args[1])
        self.assertFalse(metadata_jobs.claim_running(*args))

    def test_stale_attempt_cannot_save(self):
        args = self.dispatch().send_task.call_args.kwargs['args']
        async def replace(*unused):
            with Session(engine) as session:
                job = session.get(GameMetadataJob, self.game_id)
                job.status = JobStatus.PENDING
                job.attempt_token = 'new'
                session.add(job)
                session.commit()
            return self.result
        with patch('metadata_worker.tasks.wait_for_slot'), patch(
                'metadata_worker.tasks.lookup_metadata', side_effect=replace):
            self.assertEqual(collect_metadata.run(*args), 'stale')
        with Session(engine) as session:
            self.assertIsNone(session.get(GameMetadata, self.game_id))
            self.assertEqual(session.get(GameMetadataJob, self.game_id).attempt_token, 'new')

    def test_publication_and_timeout_recovery(self):
        publisher = MagicMock()
        publisher.connection_for_write.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            metadata_jobs.dispatch(publisher)
        with Session(engine) as session:
            job = session.get(GameMetadataJob, self.game_id)
            token = job.attempt_token
            job.last_published_at = datetime.now(UTC) - timedelta(seconds=301)
            session.add(job)
            session.commit()
        self.assertEqual(self.dispatch().send_task.call_args.kwargs['args'], [self.game_id, token])
        self.assertTrue(metadata_jobs.claim_running(self.game_id, token))
        with Session(engine) as session:
            job = session.get(GameMetadataJob, self.game_id)
            job.updated_at = datetime.now(UTC) - timedelta(seconds=121)
            session.add(job)
            session.commit()
        self.dispatch()
        with Session(engine) as session:
            self.assertEqual(session.get(GameMetadataJob, self.game_id).last_error, 'worker_timeout')

    def test_successful_data_is_never_refetched_or_overwritten(self):
        args = self.dispatch().send_task.call_args.kwargs['args']
        with Session(engine) as session:
            session.add(GameMetadata(game_id=self.game_id, single_player=True,
                genres=[{'id': '25', 'name': 'Adventure'}], last_success_at=datetime.now(UTC)))
            session.commit()
        with patch('metadata_worker.tasks.lookup_metadata') as lookup:
            self.assertEqual(collect_metadata.run(*args), 'skipped')
            lookup.assert_not_called()
        with Session(engine) as session:
            self.assertEqual(session.get(GameMetadata, self.game_id).genres[0]['id'], '25')

    def test_backfill_is_bounded_and_does_not_reset_jobs(self):
        from metadata_worker.backfill_metadata import backfill
        with Session(engine) as session:
            session.add_all([Game(steam_app_id=1000+i) for i in range(3)])
            session.commit()
        with patch('metadata_worker.backfill_metadata.app.connection_for_write'), patch(
                'metadata_worker.backfill_metadata.app.send_task'):
            self.assertEqual(backfill(limit=2)['created'], 2)
            self.assertEqual(backfill(limit=2)['created'], 1)
            self.assertEqual(backfill(limit=2)['created'], 0)


class SteamPacingTests(unittest.TestCase):
    def test_shared_redis_slot_and_fail_closed(self):
        from metadata_worker import steam_pacing
        from redis import Redis
        from redis.exceptions import ConnectionError
        import os
        client = Redis.from_url(os.environ['HLTB_REVIEW_CACHE_URL'])
        key = 'test:metadata:pacing'
        client.delete(key)
        import time
        started = time.monotonic()
        with patch.object(steam_pacing, 'client', client), patch.object(steam_pacing, 'KEY', key):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: steam_pacing.wait_for_slot(), range(2)))
            self.assertGreaterEqual(time.monotonic() - started, 0.9)
            self.assertGreater(client.pttl(key), 0)
        client.delete(key)
        with patch.object(steam_pacing.client, 'set', side_effect=ConnectionError('offline')):
            with self.assertRaises(steam_pacing.SteamPacingError):
                steam_pacing.wait_for_slot()


class MetadataRoutingTests(unittest.TestCase):
    def test_metadata_routing_and_beat_use_separate_queue(self):
        from enrichment_worker.worker import app
        queues = {queue.name for queue in app.conf.task_queues}
        self.assertEqual(queues, {'enrichment', 'steam_metadata'})
        for task in ('catalog.collect_metadata', 'catalog.maintain_metadata_jobs'):
            route = app.amqp.router.route({}, task, args=[], kwargs={})
            self.assertEqual(route['queue'].name, 'steam_metadata')
        route = app.amqp.router.route({}, 'enrichment.enrich_game', args=[], kwargs={})
        self.assertEqual(route['queue'].name, 'enrichment')
        self.assertEqual(app.conf.beat_schedule['maintain-metadata-jobs']['options']['queue'],
                         'steam_metadata')
