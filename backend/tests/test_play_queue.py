import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select
from sqlalchemy import text
from app.database import engine, User, Game, OwnedGame, PlayQueueEntry
from library_service.main import app


class PlayQueueTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(engine.url.database, 'steam_test')
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            users = [User(username=str(i), hashed_password='unused') for i in range(2)]
            games = [Game(steam_app_id=i + 1) for i in range(3)]
            session.add_all(users + games)
            session.flush()
            self.users = [u.id for u in users]
            self.ids = [g.id for g in games]
            session.add_all([OwnedGame(user_id=users[0].id, game_id=g.id) for g in games[:2]])
            session.add(OwnedGame(user_id=users[1].id, game_id=games[0].id))
            session.commit()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'X-User-ID': str(self.users[0])}

    def request(self, method, suffix='', **kwargs):
        return self.client.request(method, '/api/v1/games/queue' + suffix, headers=self.headers, **kwargs)

    def test_order_duplicates_and_user_isolation(self):
        a, b, foreign = self.ids
        self.assertEqual(self.request('POST', f'/{foreign}').status_code, 404)
        for game in (a, b, a):
            self.assertEqual(self.request('POST', f'/{game}').status_code, 200)
        self.assertEqual(self.request('GET').json(), [a, b])
        self.assertEqual(self.request('PUT', json={'game_ids': [b, a]}).json(), [b, a])
        for invalid in ([a], [a, a], [a, foreign]):
            self.assertEqual(self.request('PUT', json={'game_ids': invalid}).status_code, 409)
        other = self.client.get('/api/v1/games/queue', headers={'X-User-ID': str(self.users[1])})
        self.assertEqual(other.json(), [])
        self.assertEqual(self.request('DELETE', f'/{b}').json(), [a])

    def test_start_is_atomic_and_finish_does_not_start_next(self):
        a, b, _ = self.ids
        for game in (a, b):
            self.request('POST', f'/{game}')
        response = self.client.patch(f'/api/v1/games/{a}', headers=self.headers,
                                     json={'completion_status': 'in progress'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.request('GET').json(), [b])
        self.assertEqual(self.request('POST', f'/{a}').status_code, 409)
        self.client.patch(f'/api/v1/games/{a}', headers=self.headers, json={'completion_status': 'finished'})
        self.assertEqual(self.request('GET').json(), [b])
        self.assertEqual(self.request('POST', f'/{a}').json(), [b, a])
        with Session(engine) as session:
            self.assertEqual(session.get(OwnedGame, (self.users[0], b)).completion_status, 'not started')

    def test_concurrent_adds_are_unique(self):
        a = self.ids[0]
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: self.request('POST', f'/{a}').status_code, range(8)))
        self.assertEqual(statuses, [200] * 8)
        self.assertEqual(self.request('GET').json(), [a])

    def test_authentication_and_migration_preservation(self):
        self.assertEqual(self.client.get('/api/v1/games/queue').status_code, 401)
        self.request('POST', f'/{self.ids[0]}')
        migration = (Path(__file__).parents[1] / 'migrations/005_add_play_queue.sql').read_text()
        with engine.connect() as conn:
            for _ in range(2):
                conn.exec_driver_sql(migration)
                conn.commit()
        self.assertEqual(self.request('GET').json(), [self.ids[0]])
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(OwnedGame)).all()), 3)
