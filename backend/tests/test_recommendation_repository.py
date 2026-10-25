import unittest
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.database import (User, Game, OwnedGame, GameMetadata, GameEnrichment,
    PersonalHLTBOverride, PlayQueueEntry, EnrichmentStatus, CompletionStatus)
from recommendation_service.repository import load_candidates
from recommendation_service.ranking import rank_candidates
from recommendation_service.api import RecommendationRequest


class RecommendationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                   poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            users = [User(username=name, hashed_password='unused') for name in ('owner', 'other', 'empty')]
            game = Game(steam_app_id=620, name='Example', hltb_id=10,
                        main_story_hours=10, main_extra_hours=18)
            private = Game(steam_app_id=570, name='Private')
            session.add_all(users + [game, private])
            session.flush()
            self.owner, self.other, self.empty = [user.id for user in users]
            self.game_id = game.id
            session.add_all([OwnedGame(user_id=self.owner, game_id=game.id),
                OwnedGame(user_id=self.other, game_id=game.id, completion_status=CompletionStatus.FINISHED),
                OwnedGame(user_id=self.other, game_id=private.id)])
            session.flush()
            session.add(GameMetadata(game_id=game.id, single_player=True,
                                    genres=[{'id': '1', 'name': 'Action'}]))
            session.add(PlayQueueEntry(user_id=self.owner, game_id=game.id, position=0))
            session.add(PlayQueueEntry(user_id=self.other, game_id=game.id, position=7))
            session.add(PersonalHLTBOverride(user_id=self.other, game_id=game.id,
                hltb_id=99, main_story_hours=50, candidate={}, selected_at=datetime.now(UTC)))
            session.commit()

    def test_owned_rows_and_personal_data_are_isolated(self):
        with Session(self.engine) as session:
            mine = load_candidates(session, self.owner)
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0].main_story_hours, 10)
            self.assertEqual(mine[0].estimate_source, 'shared')
            self.assertEqual(mine[0].queue_position, 0)
            self.assertEqual(mine[0].completion_status, 'not started')
            self.assertEqual(mine[0].genre_ids, frozenset({'1'}))
            other = load_candidates(session, self.other)
            self.assertEqual(len(other), 2)
            self.assertEqual(other[0].main_story_hours, 50)
            self.assertEqual(other[0].queue_position, 7)
            self.assertFalse(other[1].match_resolved)
            self.assertIsNone(other[1].single_player)
            self.assertEqual(load_candidates(session, self.empty), [])
            self.assertEqual(load_candidates(session, 9999), [])

    def test_personal_override_resolves_ambiguity_without_shared_value_fallback(self):
        with Session(self.engine) as session:
            session.add(GameEnrichment(game_id=self.game_id, status=EnrichmentStatus.AMBIGUOUS,
                reason='multiple', last_lookup_at=datetime.now(UTC)))
            session.add(PersonalHLTBOverride(user_id=self.owner, game_id=self.game_id,
                hltb_id=20, main_story_hours=12, candidate={}, selected_at=datetime.now(UTC)))
            session.commit()
            candidate = load_candidates(session, self.owner)[0]
            self.assertTrue(candidate.match_resolved)
            self.assertEqual(candidate.main_story_hours, 12)
            self.assertIsNone(candidate.main_extra_hours)
            self.assertEqual(candidate.estimate_source, 'personal')
            prefs = RecommendationRequest(weekly_hours=4, target_weeks=3)
            self.assertEqual(len(rank_candidates([candidate], prefs)), 1)
            self.assertEqual(rank_candidates([candidate], prefs.model_copy(
                update={'completion_goal': 'main_extra'})), [])

    def test_explicit_unresolved_outcomes_excluded_without_personal_override(self):
        with Session(self.engine) as session:
            for status in EnrichmentStatus:
                record = session.get(GameEnrichment, self.game_id)
                if record is None:
                    record = GameEnrichment(game_id=self.game_id, status=status,
                        reason='test', last_lookup_at=datetime.now(UTC))
                record.status = status
                session.add(record)
                session.commit()
                self.assertEqual(load_candidates(session, self.owner)[0].match_resolved,
                                 status == EnrichmentStatus.MATCHED)

    def test_loading_is_one_select_and_does_not_mutate_queue_or_progress(self):
        statements = []
        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        with Session(self.engine) as session:
            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                candidates = load_candidates(session, self.owner)
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)
            self.assertEqual(len(statements), 1)
            self.assertTrue(statements[0].lstrip().upper().startswith('SELECT'))
            rank_candidates(candidates, RecommendationRequest(weekly_hours=4, target_weeks=3))
            self.assertEqual(session.get(PlayQueueEntry, (self.owner, self.game_id)).position, 0)
            self.assertEqual(session.get(OwnedGame, (self.owner, self.game_id)).completion_status,
                             CompletionStatus.NOT_STARTED)

    def test_invalid_identity_rejected_and_malformed_genres_ignored(self):
        from recommendation_service.repository import _genre_ids
        self.assertEqual(_genre_ids([{'id': '01'}, None, {'id': True}, {'id': '1'}]), frozenset({'1'}))
        with Session(self.engine) as session:
            for user_id in (True, 0, -1, '1', None):
                with self.assertRaises(ValueError):
                    load_candidates(session, user_id)
