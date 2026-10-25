import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from time import monotonic
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enrichment-service"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enrichment-worker"))

from app.database import (CompletionStatus, EnrichmentStatus, Game, GameEnrichment,
                          OwnedGame, PersonalHLTBOverride, User, engine)
from enrichment_service import review_cache
from enrichment_service.hltb import HLTBCandidate
from enrichment_service.matching import match_candidates
from enrichment_service.storage import record_lookup_failure, save_match_result as _save_match_result, save_cached_override


def save_match_result(session, game_id, result):
    # Test setup mirrors the worker's cache publication within a game transaction.
    from enrichment_service import review_cache as cache
    record = _save_match_result(session, game_id, result)
    token = cache.claim_refresh(game_id)
    if token is not None:
        cache.begin_refresh(game_id, token)
        cache.put_review(game_id, token, result=result, last_success_at=record.last_success_at)
        cache.release_refresh(game_id, token)
    return record


def save_personal_override(session, user_id, game_id, *, candidate_index, expected_last_success_at):
    # Test convenience: translate the fixture index to the new ID/version contract.
    from enrichment_service.review_cache import get_review
    from enrichment_service.storage import _lock_game
    _lock_game(session, game_id)
    if session.get(OwnedGame, (user_id, game_id)) is None:
        raise PermissionError("Game is not owned by this user")
    record = session.get(GameEnrichment, game_id, populate_existing=True)
    if record.last_success_at != expected_last_success_at:
        raise ValueError("Review candidates changed; reload before selecting")
    cached = get_review(game_id)
    if type(candidate_index) is not int or not 0 <= candidate_index < len(cached['candidates']):
        raise ValueError("Invalid candidate selection")
    return save_cached_override(session, user_id, game_id,
        hltb_id=cached['candidates'][candidate_index]['hltb_id'], cache_version=cached['version'])


class EnrichmentStorageTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("wait_for_slot", 0), ("cooldown", None)):
            mocked = patch("enrichment_worker.tasks.hltb_pacing." + target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.assertEqual(engine.url.database, "steam_test")
        from enrichment_service.review_cache import client
        client.flushdb()
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            game = Game(steam_app_id=123, name="Example")
            users = [User(username=name, hashed_password="unused") for name in ("owner", "other", "outsider")]
            session.add(game)
            session.add_all(users)
            session.flush()
            self.game_id = game.id
            self.owner, self.other, self.outsider = [user.id for user in users]
            session.add_all([OwnedGame(user_id=user.id, game_id=game.id,
                                      completion_status=CompletionStatus.FINISHED) for user in users[:2]])
            session.commit()
        self.first = HLTBCandidate(10, "Example", None, "game", ("PC",), 2020, 1.5, None, 4.25)
        self.second = replace(self.first, hltb_id=20, main_story_hours=3)

    def ambiguous(self, session):
        return save_match_result(session, self.game_id, match_candidates("Example", [self.first, self.second]))

    def test_matched_result_round_trip_and_progress_unchanged(self):
        with Session(engine) as session:
            save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
            session.commit()
        with Session(engine) as session:
            stored = session.get(GameEnrichment, self.game_id)
            self.assertEqual(stored.status, EnrichmentStatus.MATCHED)
            game = session.get(Game, self.game_id)
            self.assertEqual((game.hltb_id, game.main_story_hours, game.completionist_hours), (10, 1.5, 4.25))
            self.assertIsNone(game.main_extra_hours)
            self.assertIsNotNone(stored.last_success_at.tzinfo)
            self.assertTrue(all(o.completion_status == CompletionStatus.FINISHED for o in session.exec(select(OwnedGame))))

    def test_ambiguous_candidates_round_trip_and_replace(self):
        with Session(engine) as session:
            self.ambiguous(session)
            session.commit()
        with Session(engine) as session:
            record = session.get(GameEnrichment, self.game_id)
            self.assertEqual([c["hltb_id"] for c in review_cache.get_review(self.game_id)["candidates"]], [10, 20])
            self.assertEqual(review_cache.get_review(self.game_id)["candidates"][0]["platforms"], ["PC"])
            self.assertIsNone(session.get(Game, self.game_id).hltb_id)
            save_match_result(session, self.game_id, match_candidates("Example", []))
            session.commit()
        with Session(engine) as session:
            record = session.get(GameEnrichment, self.game_id)
            self.assertEqual(record.status, EnrichmentStatus.NO_MATCH)
            self.assertIsNone(review_cache.get_review(self.game_id))

    def test_personal_choices_are_isolated_and_survive_refresh(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            snapshot = shared.last_success_at
            save_personal_override(session, self.owner, self.game_id, candidate_index=0, expected_last_success_at=snapshot)
            save_personal_override(session, self.other, self.game_id, candidate_index=1, expected_last_success_at=snapshot)
            session.commit()
        with Session(engine) as session:
            save_match_result(session, self.game_id, match_candidates("Example", [self.second]))
            session.commit()
        with Session(engine) as session:
            self.assertEqual(session.get(PersonalHLTBOverride, (self.owner, self.game_id)).hltb_id, 10)
            self.assertEqual(session.get(PersonalHLTBOverride, (self.other, self.game_id)).hltb_id, 20)
            self.assertEqual(session.get(Game, self.game_id).hltb_id, 20)

    def test_invalid_unauthorized_and_stale_selections(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            old_snapshot = shared.last_success_at
            with self.assertRaises(PermissionError):
                save_personal_override(session, self.outsider, self.game_id, candidate_index=0, expected_last_success_at=old_snapshot)
            for index in (-1, 2, True):
                with self.assertRaises(ValueError):
                    save_personal_override(session, self.owner, self.game_id, candidate_index=index, expected_last_success_at=old_snapshot)
            self.ambiguous(session)
            with self.assertRaisesRegex(ValueError, "changed"):
                save_personal_override(session, self.owner, self.game_id, candidate_index=0, expected_last_success_at=old_snapshot)
            self.assertEqual(session.exec(select(PersonalHLTBOverride)).all(), [])

    def test_failure_retains_last_good_result_and_success_clears_error(self):
        with Session(engine) as session:
            good = save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
            previous_success = good.last_success_at
            failed = record_lookup_failure(session, self.game_id, timed_out=True)
            self.assertEqual(failed.last_success_at, previous_success)
            self.assertEqual(session.get(Game, self.game_id).hltb_id, 10)
            self.assertEqual(failed.status, EnrichmentStatus.MATCHED)
            self.assertEqual(failed.last_error, "lookup_timeout")
            recovered = save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
            self.assertIsNone(recovered.last_error)
            session.commit()

    def test_first_failure_is_distinct_from_no_match(self):
        with Session(engine) as session:
            record = record_lookup_failure(session, self.game_id)
            session.commit()
            self.assertEqual(record.status, EnrichmentStatus.LOOKUP_FAILED)
            self.assertIsNone(record.last_success_at)

    def test_caller_rollback_removes_result_and_override(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            save_personal_override(session, self.owner, self.game_id, candidate_index=0,
                                   expected_last_success_at=shared.last_success_at)
            session.rollback()
        with Session(engine) as session:
            self.assertIsNone(session.get(GameEnrichment, self.game_id))
            self.assertIsNone(session.get(Game, self.game_id).hltb_id)
            self.assertEqual(session.exec(select(PersonalHLTBOverride)).all(), [])

    def test_database_rejects_override_for_nonowner(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            selection = save_personal_override(session, self.owner, self.game_id, candidate_index=0,
                                               expected_last_success_at=shared.last_success_at)
            session.commit()
            session.add(PersonalHLTBOverride(user_id=self.outsider, game_id=self.game_id,
                                            hltb_id=10, candidate=selection.candidate,
                                            selected_at=selection.selected_at))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

    def test_refresh_clears_old_automatic_times_and_repeats_do_not_duplicate(self):
        with Session(engine) as session:
            for _ in range(2):
                save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
            self.ambiguous(session)
            session.commit()
        with Session(engine) as session:
            records = session.exec(select(GameEnrichment)).all()
            self.assertEqual(len(records), 1)
            game = session.get(Game, self.game_id)
            self.assertIsNone(game.main_story_hours)
            self.assertIsNone(game.hltb_id)

    def test_concurrent_first_results_do_not_duplicate(self):
        barrier = Barrier(2)

        def persist():
            with Session(engine) as session:
                barrier.wait(timeout=10)
                save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(persist) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(GameEnrichment)).all()), 1)

    def test_reselecting_updates_one_personal_record(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            for index in (0, 1):
                save_personal_override(session, self.owner, self.game_id, candidate_index=index,
                                       expected_last_success_at=shared.last_success_at)
                session.commit()
            overrides = session.exec(select(PersonalHLTBOverride)).all()
            self.assertEqual(len(overrides), 1)
            self.assertEqual(overrides[0].hltb_id, 20)
            self.assertEqual(overrides[0].main_story_hours, 3)
            self.assertEqual(shared.status, EnrichmentStatus.AMBIGUOUS)

    def test_stale_session_cannot_select_old_candidate_snapshot(self):
        with Session(engine) as session:
            shared = self.ambiguous(session)
            session.commit()
            old_snapshot = shared.last_success_at
            session.commit()  # release read transaction before another worker refreshes
            with Session(engine) as worker:
                self.ambiguous(worker)
                worker.commit()
            with self.assertRaisesRegex(ValueError, "changed"):
                save_personal_override(session, self.owner, self.game_id, candidate_index=0,
                                       expected_last_success_at=old_snapshot)

    def test_steam_metadata_refresh_preserves_game_estimates(self):
        from sync_service.steam import OwnedGames, sync_owned_games

        with Session(engine) as session:
            save_match_result(session, self.game_id, match_candidates("Example", [self.first]))
            session.commit()
            library = OwnedGames.model_validate({"game_count": 1, "games": [
                {"appid": 123, "name": "Updated Steam title"},
            ]})
            sync_owned_games(session, self.owner, library)
            session.commit()
            game = session.get(Game, self.game_id)
            self.assertEqual(game.name, "Updated Steam title")
            self.assertEqual((game.hltb_id, game.main_story_hours, game.completionist_hours), (10, 1.5, 4.25))

    def test_game_hours_require_a_linked_hltb_id(self):
        with Session(engine) as session:
            game = session.get(Game, self.game_id)
            game.main_story_hours = 2
            session.add(game)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

    def run_job(self, game_id):
        from enrichment_worker import jobs
        from enrichment_worker.tasks import app, enrich_game
        with Session(engine) as session:
            jobs.create_ready(session, [game_id] if session.get(Game, game_id) else [])
            session.commit()
        with patch.object(app, "connection_for_write"), patch.object(app, "send_task") as send:
            jobs.dispatch(app, [game_id])
        if not send.called:
            return "skipped"
        return enrich_game.run(*send.call_args.kwargs["args"])

    def test_task_enriches_once_and_reuses_shared_game(self):
        from enrichment_worker.tasks import enrich_game
        with patch("enrichment_worker.tasks.lookup_candidates", new_callable=AsyncMock, return_value=[self.first]) as lookup:
            self.assertEqual(self.run_job(self.game_id), "matched")
            self.assertEqual(self.run_job(self.game_id), "skipped")
        lookup.assert_awaited_once()

    def test_task_failure_can_retry_without_losing_ownership(self):
        from enrichment_worker.tasks import enrich_game
        from enrichment_service.hltb import HLTBLookupTimeout
        with patch("enrichment_worker.tasks.lookup_candidates", new_callable=AsyncMock,
                   side_effect=[HLTBLookupTimeout("timeout"), [self.first]]):
            self.assertEqual(self.run_job(self.game_id), "failed")
            from app.database import EnrichmentJob, JobStatus
            with Session(engine) as session:
                job = session.get(EnrichmentJob, self.game_id)
                self.assertEqual(job.status, JobStatus.READY)
                self.assertEqual(job.attempt_count, 1)
                self.assertIsNotNone(job.next_attempt_at)
                job.next_attempt_at = None
                session.add(job)
                session.commit()
            self.assertEqual(self.run_job(self.game_id), "matched")
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(OwnedGame)).all()), 2)
            self.assertIsNone(session.get(GameEnrichment, self.game_id).last_error)

    def test_task_skips_review_no_match_and_missing_game(self):
        from enrichment_worker.tasks import enrich_game
        for candidates in ([self.first, self.second], []):
            with Session(engine) as session:
                save_match_result(session, self.game_id, match_candidates("Example", candidates))
                session.commit()
            with patch("enrichment_worker.tasks.lookup_candidates", new_callable=AsyncMock) as lookup:
                self.assertEqual(self.run_job(self.game_id), "skipped")
                self.assertEqual(self.run_job(999999), "skipped")
                lookup.assert_not_awaited()

    def test_task_rejects_changed_title(self):
        from enrichment_worker.tasks import enrich_game
        async def lookup(*args, **kwargs):
            with Session(engine) as session:
                game = session.get(Game, self.game_id)
                game.name = "Changed"
                session.add(game)
                session.commit()
            return [self.first]
        with patch("enrichment_worker.tasks.lookup_candidates", side_effect=lookup):
            self.assertEqual(self.run_job(self.game_id), "stale")
        with Session(engine) as session:
            self.assertIsNone(session.get(GameEnrichment, self.game_id))

    def test_task_rejects_concurrent_result(self):
        from enrichment_worker.tasks import enrich_game
        async def lookup(*args, **kwargs):
            with Session(engine) as session:
                save_match_result(session, self.game_id, match_candidates("Example", [self.second]))
                session.commit()
            return [self.first]
        with patch("enrichment_worker.tasks.lookup_candidates", side_effect=lookup):
            self.assertEqual(self.run_job(self.game_id), "stale")
        with Session(engine) as session:
            self.assertEqual(session.get(Game, self.game_id).hltb_id, 20)

    def test_task_cannot_save_after_its_attempt_is_replaced(self):
        from enrichment_worker.tasks import app, enrich_game
        from enrichment_worker import jobs
        from app.database import EnrichmentJob, JobStatus
        with Session(engine) as session:
            jobs.create_ready(session, [self.game_id])
            session.commit()
        with patch.object(app, "connection_for_write"), patch.object(app, "send_task") as send:
            jobs.dispatch(app, [self.game_id])
        args = send.call_args.kwargs["args"]

        async def replace_attempt(*unused, **kwargs):
            with Session(engine) as session:
                job = session.get(EnrichmentJob, self.game_id)
                job.status = JobStatus.PENDING
                job.attempt_token = "replacement-attempt"
                session.add(job)
                session.commit()
            return [self.first]

        with patch("enrichment_worker.tasks.lookup_candidates", side_effect=replace_attempt):
            self.assertEqual(enrich_game.run(*args), "stale")
        with Session(engine) as session:
            self.assertIsNone(session.get(GameEnrichment, self.game_id))
            self.assertIsNone(session.get(Game, self.game_id).hltb_id)
            job = session.get(EnrichmentJob, self.game_id)
            self.assertEqual(job.status, JobStatus.PENDING)
            self.assertEqual(job.attempt_token, "replacement-attempt")

    def test_task_preserves_personal_override(self):
        from enrichment_worker.tasks import enrich_game
        with Session(engine) as session:
            shared = self.ambiguous(session)
            save_personal_override(session, self.owner, self.game_id, candidate_index=1,
                                   expected_last_success_at=shared.last_success_at)
            session.delete(shared)
            session.commit()
        with patch("enrichment_worker.tasks.lookup_candidates", new_callable=AsyncMock, return_value=[self.first]):
            self.assertEqual(self.run_job(self.game_id), "matched")
        with Session(engine) as session:
            self.assertEqual(session.get(PersonalHLTBOverride, (self.owner, self.game_id)).hltb_id, 20)

    def test_import_event_schedules_only_eligible_games_once_per_id(self):
        from enrichment_worker.tasks import games_imported
        with Session(engine) as session:
            session.add(Game(steam_app_id=456, name="Other", hltb_id=88))
            session.add(Game(steam_app_id=789, name=" "))
            session.commit()
        with patch("enrichment_worker.tasks.app.connection_for_write"), \
             patch("enrichment_worker.tasks.app.send_task") as send:
            self.assertEqual(games_imported.run(schema_version=1,
                steam_app_ids=[123, 123, 456, 789, 999]), 1)
        hltb_calls = [call for call in send.call_args_list if call.args == ("enrichment.enrich_game",)]
        self.assertEqual(len(hltb_calls), 1)
        self.assertEqual(hltb_calls[0].kwargs["args"][0], self.game_id)
        self.assertTrue(hltb_calls[0].kwargs["args"][1])
        self.assertEqual(len([call for call in send.call_args_list
                              if call.args == ("catalog.collect_metadata",)]), 3)

    def test_import_event_rejects_invalid_contract(self):
        from enrichment_worker.tasks import games_imported
        for version, ids in ((2, [123]), (True, [123]), (1, [True]), (1, [-1]), (1, "123")):
            with self.assertRaises(ValueError):
                games_imported.run(schema_version=version, steam_app_ids=ids)

    def test_import_event_publication_failure_is_visible(self):
        from enrichment_worker.tasks import games_imported
        with patch("enrichment_worker.tasks.app.connection_for_write", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                games_imported.run(schema_version=1, steam_app_ids=[123])

    def test_expired_shortlist_refreshes_and_preserves_personal_choice(self):
        from enrichment_worker.tasks import refresh_review
        with Session(engine) as session:
            shared = self.ambiguous(session)
            save_personal_override(session, self.owner, self.game_id, candidate_index=1,
                                   expected_last_success_at=shared.last_success_at)
            session.commit()
        review_cache.client.delete(review_cache.key(self.game_id))
        token = review_cache.claim_refresh(self.game_id)
        with patch('enrichment_worker.tasks.lookup_candidates', new_callable=AsyncMock, return_value=[self.first, self.second]) as lookup:
            self.assertEqual(refresh_review.run(self.game_id, token), 'ambiguous')
            self.assertEqual(refresh_review.run(self.game_id, token), 'skipped')
            lookup.assert_awaited_once()
        self.assertEqual(len(review_cache.get_review(self.game_id)['candidates']), 2)
        with Session(engine) as session:
            self.assertEqual(session.get(PersonalHLTBOverride, (self.owner, self.game_id)).hltb_id, 20)

    def test_review_refresh_failure_is_not_cached_as_empty_results(self):
        from enrichment_worker.tasks import refresh_review
        from enrichment_service.hltb import HLTBLookupTimeout
        with Session(engine) as session:
            self.ambiguous(session)
            session.commit()
        review_cache.client.delete(review_cache.key(self.game_id))
        token = review_cache.claim_refresh(self.game_id)
        with patch('enrichment_worker.tasks.lookup_candidates', new_callable=AsyncMock, side_effect=HLTBLookupTimeout('timeout')):
            from celery.exceptions import Retry
            with patch.object(refresh_review, "retry", side_effect=Retry()) as retry:
                with self.assertRaises(Retry):
                    refresh_review.run(self.game_id, token)
                self.assertEqual(retry.call_args.kwargs["countdown"], 60)
                self.assertEqual(retry.call_args.kwargs["args"][2], 1)
        self.assertEqual(review_cache.get_review(self.game_id)['status'], 'lookup_failed')
        self.assertLessEqual(review_cache.client.ttl(review_cache.key(self.game_id)), 60)
        with Session(engine) as session:
            self.assertEqual(session.get(GameEnrichment, self.game_id).status, EnrichmentStatus.AMBIGUOUS)
