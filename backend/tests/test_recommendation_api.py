import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from app.database import (Game, GameMetadata, OwnedGame, User, PlayQueueEntry,
                          PersonalHLTBOverride, get_session)
from recommendation_service.main import app


class RecommendationAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with Session(self.engine) as session:
            users = [User(username=name, hashed_password='unused') for name in ('owner', 'other', 'empty')]
            games = [Game(steam_app_id=100+i, name=f'Game {i}', hltb_id=10+i,
                          main_story_hours=hours) for i, hours in enumerate((12, 13, 8, 6))]
            session.add_all(users + games)
            session.flush()
            self.owner, self.other, self.empty = [u.id for u in users]
            self.ids = [g.id for g in games]
            for game in games:
                session.add(OwnedGame(user_id=self.owner, game_id=game.id))
                session.add(GameMetadata(game_id=game.id, single_player=True, genres=[{'id': '1', 'name': 'Action'}]))
            session.add(OwnedGame(user_id=self.other, game_id=games[0].id))
            session.flush()
            session.add(PlayQueueEntry(user_id=self.owner, game_id=games[0].id, position=0))
            session.add(PersonalHLTBOverride(user_id=self.other, game_id=games[0].id,
                hltb_id=99, main_story_hours=50, candidate={}, selected_at=datetime.now(UTC)))
            session.commit()
        self.body = {'weekly_hours': 4, 'target_weeks': 3, 'preferred_genres': ['1']}

    def post(self, user_id=None, **body):
        return self.client.post('/api/v1/recommendations', json=self.body | body,
            headers={'X-User-ID': str(user_id if user_id is not None else self.owner)})

    def test_budget_queue_and_reasons(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['available_hours'], 12)
        self.assertIsNone(body['empty_reason'])
        self.assertEqual([item['game_id'] for item in body['recommendations']], [self.ids[0], self.ids[2], self.ids[3]])
        first = body['recommendations'][0]
        self.assertEqual(first['estimated_weeks'], 3)
        self.assertEqual(first['queue_position'], 0)
        self.assertEqual(first['matched_genres'], ['1'])
        self.assertEqual(first['estimate_source'], 'shared')
        self.assertTrue(first['reasons'])
        self.assertEqual(body['coverage']['games_within_budget'], 3)

    def test_personal_overrides_and_ownership(self):
        body = self.post(self.other).json()
        self.assertEqual(body['recommendations'], [])
        self.assertEqual(body['empty_reason'], 'no_games_within_budget')
        self.assertEqual(body['coverage']['owned_games'], 1)

    def test_empty_reasons(self):
        self.assertEqual(self.post(self.empty).json()['empty_reason'], 'empty_library')
        body = self.post(completion_goal='main_extra').json()
        self.assertEqual(body['empty_reason'], 'no_eligible_games')
        self.assertEqual(body['coverage']['eligible_games_before_budget'], 0)

    def test_validation_and_identity(self):
        for header in ({}, {'X-User-ID': 'bad'}, {'X-User-ID': '0'}):
            self.assertEqual(self.client.post('/api/v1/recommendations', json=self.body, headers=header).status_code, 401)
        for body in ({'preferred_genres': ['999999']}, {'weekly_hours': True},
                     {'weekly_hours': -1}, {'target_weeks': 1.5}, {'unexpected': 123}):
            self.assertEqual(self.post(**body).status_code, 422)
        self.assertEqual(self.post(preferred_genres=[]).status_code, 200)

    def test_no_async_provider_client_or_state_mutation(self):
        with patch('httpx.AsyncClient', side_effect=AssertionError('No outbound HTTP')):
            self.assertEqual(self.post().status_code, 200)
        with Session(self.engine) as session:
            self.assertEqual(session.get(PlayQueueEntry, (self.owner, self.ids[0])).position, 0)
            self.assertEqual(session.get(OwnedGame, (self.owner, self.ids[0])).completion_status.value, 'not started')


    def test_authenticated_genre_choices(self):
        self.assertEqual(self.client.get('/api/v1/recommendations').status_code, 401)
        response = self.client.get('/api/v1/recommendations', headers={'X-User-ID': str(self.owner)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'genres': [{'id': '1', 'name': 'Action'}]})


    def test_pagination_metadata_and_invalid_offsets(self):
        first = self.post().json()
        self.assertEqual(first['pagination'], {'offset': 0, 'limit': 3, 'total': 3, 'has_more': False})
        second = self.post(offset=2).json()
        self.assertEqual([g['game_id'] for g in second['recommendations']], [self.ids[3]])
        end = self.post(offset=3).json()
        self.assertEqual(end['recommendations'], [])
        self.assertIsNone(end['empty_reason'])
        for offset in (-1, True, '3', 1.5, 1000001):
            self.assertEqual(self.post(offset=offset).status_code, 422)


    def test_stored_trailers_returned_without_excluding_missing_videos(self):
        with Session(self.engine) as session:
            metadata=session.get(GameMetadata,self.ids[0])
            metadata.trailer_url='https://video.akamai.steamstatic.com/a.m3u8'
            metadata.trailer_format='hls'
            session.add(metadata);session.commit()
        result=self.post().json()['recommendations']
        self.assertEqual(result[0]['trailer_url'],'https://video.akamai.steamstatic.com/a.m3u8')
        self.assertEqual(result[0]['trailer_format'],'hls')
        self.assertEqual(len(result),3)
        self.assertIsNone(result[1]['trailer_url'])
