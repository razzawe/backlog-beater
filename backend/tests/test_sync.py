import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

from app.database import Game, OwnedGame, User, engine
from sync_service.main import app
from sync_service.steam import OwnedGames, insert_missing_games, sync_owned_games


class SyncTests(unittest.TestCase):
    def setUp(self):
        # Existing Steam tests must never contact the live HLTB service.
        lookup = patch("sync_service.events.client.send_task")
        lookup.start()
        connection = patch("sync_service.events.client.connection_for_write")
        connection.start()
        self.addCleanup(connection.stop)
        self.addCleanup(lookup.stop)
        # Only recreate the isolated test database.
        self.assertEqual(engine.url.database, "steam_test")
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self.client = TestClient(app)
        self.client.__enter__()
        with Session(engine) as session:
            user = User(username="sync-user", hashed_password="unused", steam_id="76561198000000000")
            session.add(user)
            session.commit()
            self.user_id = user.id
            self.headers = {"X-User-ID": str(user.id)}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def sync(self, payload):
        # Keep the API and PostgreSQL real while substituting Steam's HTTP response.
        with patch("sync_service.steam.STEAM_API_KEY", "test-key"), patch("sync_service.steam.httpx.AsyncClient") as client:
            steam_client = client.return_value.__aenter__.return_value
            steam_client.get = AsyncMock(return_value=httpx.Response(
                200, json=payload, request=httpx.Request("GET", "https://api.steampowered.com"),
            ))
            response = self.client.post("/api/v1/steam/sync", headers=self.headers)
            steam_client.get.assert_awaited_once()
            self.assertEqual(steam_client.get.call_args.kwargs["params"]["steamid"], "76561198000000000")
            return response

    def test_import_and_repeat(self):
        payload = {"response": {"game_count": 3, "games": [{"appid": 570}, {"appid": 730}, {"appid": 570}]}}
        # Repeated imports must not duplicate catalog entries or ownership links.
        self.assertEqual({k: v for k, v in self.sync(payload).json().items() if k != "enrichment"}, {"game_count": 3, "inserted_count": 2, "ownerships_inserted_count": 2})
        self.assertEqual({k: v for k, v in self.sync(payload).json().items() if k != "enrichment"}, {"game_count": 3, "inserted_count": 0, "ownerships_inserted_count": 0})
        with Session(engine) as session:
            self.assertEqual(sorted(game.steam_app_id for game in session.exec(select(Game))), [570, 730])
            links = session.exec(select(OwnedGame)).all()
            self.assertEqual(len(links), 2)
            self.assertEqual({link.user_id for link in links}, {self.user_id})

    def test_invalid_response_writes_nothing(self):
        for payload in [{"response": {}}, {"response": {"game_count": 2, "games": [{"appid": 570}]}},
                        {"response": {"game_count": 1, "games": [{"appid": -1}]}}]:
            self.assertEqual(self.sync(payload).status_code, 502)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(Game)).all(), [])
            self.assertEqual(session.exec(select(OwnedGame)).all(), [])

    def test_repeat_sync_preserves_updated_completion_status(self):
        from library_service.main import app as api_app

        payload = {"response": {"game_count": 1, "games": [{"appid": 570}]}}
        self.assertEqual(self.sync(payload).status_code, 200)
        with Session(engine) as session:
            game_id = session.exec(select(Game)).one().id
        with TestClient(api_app) as api_client:
            response = api_client.patch(
                f"/api/v1/games/{game_id}", headers=self.headers,
                json={"completion_status": "finished"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.sync(payload).status_code, 200)
            library = api_client.get("/api/v1/games", headers=self.headers)
            self.assertEqual(library.status_code, 200)
            self.assertEqual(library.json()[0]["completion_status"], "finished")

    def test_exhausted_retries_preserve_existing_library(self):
        self.sync({"response": {"game_count": 1, "games": [{"appid": 570}]}})
        with Session(engine) as session:
            ownership = session.exec(select(OwnedGame)).one()
            ownership.completion_status = "finished"
            session.add(ownership)
            session.commit()
        with patch("sync_service.steam.STEAM_API_KEY", "test-key"), \
                patch("sync_service.steam.httpx.AsyncClient") as client, \
                patch("sync_service.steam.asyncio.sleep", new_callable=AsyncMock):
            get = client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ReadTimeout("Steam timed out"))
            response = self.client.post("/api/v1/steam/sync", headers=self.headers)
            self.assertEqual(response.status_code, 504)
            self.assertEqual(get.await_count, 4)
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(Game)).all()), 1)
            ownership = session.exec(select(OwnedGame)).one()
            self.assertEqual(ownership.completion_status, "finished")

    def test_empty_library(self):
        # An empty library creates no catalog entries or ownership links.
        self.assertEqual({k: v for k, v in self.sync({"response": {"game_count": 0}}).json().items() if k != "enrichment"}, {"game_count": 0, "inserted_count": 0, "ownerships_inserted_count": 0})

    def test_existing_game_can_belong_to_two_users(self):
        # A second user receives a new link to the same catalog entry.
        library = OwnedGames.model_validate({"game_count": 1, "games": [{"appid": 570}]})
        with Session(engine) as session:
            other = User(username="other-user", hashed_password="unused")
            session.add(other)
            session.commit()
            self.assertEqual(sync_owned_games(session, self.user_id, library), (1, 1))
            session.commit()
            self.assertEqual(sync_owned_games(session, other.id, library), (0, 1))
            session.commit()
            self.assertEqual(len(session.exec(select(Game)).all()), 1)
            links = session.exec(select(OwnedGame)).all()
            self.assertEqual({link.user_id for link in links}, {self.user_id, other.id})
            self.assertEqual(len({link.game_id for link in links}), 1)

    def test_ownership_failure_rolls_back_catalog_insert(self):
        # A failure after both inserts must leave neither kind of row committed.
        from sync_service.steam import insert_owned_games

        def fail_after_insert(*args):
            insert_owned_games(*args)
            raise RuntimeError("ownership failure")

        payload = {"response": {"game_count": 1, "games": [{"appid": 570}]}}
        with patch("sync_service.steam.insert_owned_games", side_effect=fail_after_insert):
            with self.assertRaisesRegex(RuntimeError, "ownership failure"):
                self.sync(payload)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(Game)).all(), [])
            self.assertEqual(session.exec(select(OwnedGame)).all(), [])

    def test_concurrent_syncs_create_one_ownership(self):
        # Concurrent syncs for one user must create a single ownership link.
        library = OwnedGames.model_validate({"game_count": 1, "games": [{"appid": 570}]})
        barrier = Barrier(2)

        def sync_library():
            with Session(engine) as session:
                barrier.wait(timeout=10)
                counts = sync_owned_games(session, self.user_id, library)
                session.commit()
                return counts

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(sync_library) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=20) for future in futures), [(0, 0), (1, 1)])
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(OwnedGame)).all()), 1)

    def test_auth_and_unlinked_user(self):
        self.assertIn(self.client.post("/api/v1/steam/sync").status_code, (401, 403))
        with Session(engine) as session:
            user = session.exec(select(User)).one()
            user.steam_id = None
            session.add(user)
            session.commit()
        self.assertEqual(self.client.post("/api/v1/steam/sync", headers=self.headers).status_code, 409)

    def test_same_app_id_concurrently_creates_one_game(self):
        library = OwnedGames.model_validate({"game_count": 1, "games": [{"appid": 570}]})
        barrier = Barrier(2)

        def insert_game():
            with Session(engine) as session:
                barrier.wait(timeout=10)
                inserted = insert_missing_games(session, library)
                session.commit()
                return inserted

        # Two independent transactions compete to create the same Steam game.
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(insert_game) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=20) for future in futures), [0, 1])
        with Session(engine) as session:
            games = session.exec(select(Game)).all()
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].steam_app_id, 570)

    def test_concurrent_inserts(self):
        library = OwnedGames.model_validate({"game_count": 2, "games": [{"appid": 570}, {"appid": 730}]})
        barrier = Barrier(2)

        def insert_batch():
            with Session(engine) as session:
                barrier.wait(timeout=10)
                inserted = insert_missing_games(session, library)
                session.commit()
                return inserted

        # Independent transactions attempt to insert the same games simultaneously.
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(insert_batch) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=20) for future in futures), [0, 2])
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(Game)).all()), 2)

    def test_sync_refreshes_catalog_metadata_and_preserves_progress(self):
        payload = {"response": {"game_count": 1, "games": [{"appid": 570, "name": "Dota 2"}]}}
        first = self.sync(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["inserted_count"], 1)
        with Session(engine) as session:
            game = session.exec(select(Game)).one()
            self.assertEqual(game.name, "Dota 2")
            self.assertEqual(game.cover_url, "https://cdn.akamai.steamstatic.com/steam/apps/570/header.jpg")
            ownership = session.get(OwnedGame, (self.user_id, game.id))
            ownership.completion_status = "finished"
            session.add(ownership)
            session.commit()
        payload["response"]["games"][0]["name"] = "Updated title"
        second = self.sync(payload)
        self.assertEqual(second.json()["inserted_count"], 0)
        from library_service.main import app as library_app
        with TestClient(library_app) as client:
            game = client.get("/api/v1/games", headers=self.headers).json()[0]
            self.assertEqual(game["name"], "Updated title")
            self.assertEqual(game["completion_status"], "finished")
            self.assertTrue(game["cover_url"].endswith("/570/header.jpg"))

    def test_sync_queues_after_import_without_inline_lookup(self):
        with patch("sync_service.main.publish_games_imported") as queue:
            from sync_service.events import ImportEventSubmission
            def submit(ids):
                with Session(engine) as session:
                    self.assertEqual(session.exec(select(Game)).one().steam_app_id, 123)
                    self.assertEqual(len(session.exec(select(OwnedGame)).all()), 1)
                self.assertEqual(ids, [123])
                return ImportEventSubmission(submitted=True)
            queue.side_effect = submit
            response = self.sync({"response": {"game_count": 1, "games": [{"appid": 123, "name": "Example"}]}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["enrichment"], {"submitted": True, "unavailable": False})

    def test_broker_failure_does_not_undo_successful_sync(self):
        with patch("sync_service.events.client.connection_for_write", side_effect=RuntimeError("offline")):
            result = self.sync({"response": {"game_count": 1, "games": [{"appid": 123, "name": "Example"}]}})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["enrichment"]["unavailable"])
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(OwnedGame)).all()), 1)
