import unittest

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

from app.api.main import create_access_token
from app.database import CompletionStatus, Game, OwnedGame, User, engine
from app.main import app


class OwnedGamesTests(unittest.TestCase):
    def setUp(self):
        # Recreate only the isolated test database and start the main API.
        self.assertEqual(engine.url.database, "steam_test")
        SQLModel.metadata.drop_all(engine)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

        # Seed two users with private games and a shared catalog game.
        with Session(engine) as session:
            users = [User(username=name, hashed_password="unused") for name in ("first", "second", "empty")]
            games = [Game(steam_app_id=app_id) for app_id in (570, 730, 440)]
            session.add_all(users + games)
            session.flush()
            session.add_all([
                OwnedGame(user_id=users[0].id, game_id=games[0].id),
                OwnedGame(user_id=users[0].id, game_id=games[1].id),
                OwnedGame(user_id=users[1].id, game_id=games[1].id),
                OwnedGame(user_id=users[1].id, game_id=games[2].id),
            ])
            session.commit()
            self.headers = [
                {"Authorization": f"Bearer {create_access_token(user.id)}"}
                for user in users
            ]
            self.games = [
                {"id": game.id, "steam_app_id": game.steam_app_id,
                 "cover_url": None, "completion_status": "not started"}
                for game in games
            ]

    def test_users_receive_only_their_games(self):
        # Each owner sees the shared game and only their own private game.
        for index, expected in ((0, self.games[:2]), (1, self.games[1:])):
            response = self.client.get("/api/v1/games", headers=self.headers[index])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)

    def test_empty_library_without_steam_link(self):
        # Reading stored ownership does not require a linked Steam account.
        response = self.client.get("/api/v1/games", headers=self.headers[2])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_shared_game_has_shared_cover_and_independent_progress(self):
        """Verify that owners share a cover but keep separate completion status.
        Return each owner's status without exposing the other owner's progress."""
        game_id = self.games[1]["id"]
        with Session(engine) as session:
            game = session.get(Game, game_id)
            game.cover_url = "https://example.com/cover.jpg"
            first_user = session.exec(
                select(User).where(User.username == "first")
            ).one()
            ownership = session.get(OwnedGame, (first_user.id, game_id))
            ownership.completion_status = CompletionStatus.IN_PROGRESS
            session.add(game)
            session.add(ownership)
            session.commit()

        for index, expected_status in ((0, "in progress"), (1, "not started")):
            response = self.client.get("/api/v1/games", headers=self.headers[index])
            self.assertEqual(response.status_code, 200)
            shared_game = next(game for game in response.json() if game["id"] == game_id)
            self.assertEqual(shared_game["cover_url"], "https://example.com/cover.jpg")
            self.assertEqual(shared_game["completion_status"], expected_status)

    def test_requires_authentication(self):
        # Reject requests without credentials or with an invalid token.
        self.assertIn(self.client.get("/api/v1/games").status_code, (401, 403))
        response = self.client.get("/api/v1/games", headers={"Authorization": "Bearer invalid"})
        self.assertEqual(response.status_code, 401)

    def test_query_user_id_cannot_select_another_library(self):
        # Ignore caller-supplied ownership filters and use the authenticated ID.
        response = self.client.get("/api/v1/games?user_id=2", headers=self.headers[0])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.games[:2])
