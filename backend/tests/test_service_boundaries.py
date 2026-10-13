"""Exercise service boundaries with a disposable in-memory database."""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import User, get_session
from app.core.security import ALGORITHM, SECRET_KEY
from auth_service.api import create_access_token
from auth_service.main import app as auth_app
from library_service.main import app as library_app
from sync_service.main import app as sync_app
from enrichment_service.main import app as enrichment_app


class ServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(engine.dispose)
        self.engine = engine
        SQLModel.metadata.create_all(engine)

        def test_session():
            with Session(engine) as session:
                yield session

        for app in (auth_app, library_app, sync_app, enrichment_app):
            previous = app.dependency_overrides.copy()
            app.dependency_overrides[get_session] = test_session
            self.addCleanup(self.restore_overrides, app, previous)

        # These tests create their own schema; do not invoke PostgreSQL startup.
        self.clients = [TestClient(app) for app in (auth_app, library_app, sync_app, enrichment_app)]
        for client in self.clients:
            self.addCleanup(client.close)
        self.auth, self.library, self.sync, self.enrichment = self.clients

    @staticmethod
    def restore_overrides(app, previous):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    def test_verified_auth_identity_works_across_services(self):
        credentials = {"username": "service-user", "password": "test-password"}
        self.assertEqual(
            self.auth.post("/api/v1/auth/register", json=credentials).status_code, 201,
        )
        login = self.auth.post("/api/v1/auth/login", json=credentials)
        self.assertEqual(login.status_code, 200)
        check = self.auth.get("/internal/auth/check", headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
        })
        self.assertEqual(check.status_code, 204)
        headers = {"X-User-ID": check.headers["x-user-id"]}
        me = self.auth.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], credentials["username"])
        library = self.library.get("/api/v1/games", headers=headers)
        self.assertEqual(library.status_code, 200)
        self.assertEqual(library.json(), [])
        sync = self.sync.post("/api/v1/steam/sync", headers=headers)
        self.assertEqual(sync.status_code, 409)
        self.assertEqual(sync.json()["detail"], "Connect Steam before syncing")

    def test_services_only_expose_their_own_routes(self):
        requests = (
            (self.auth, "GET", "/api/v1/games"),
            (self.auth, "POST", "/api/v1/steam/sync"),
            (self.library, "POST", "/api/v1/auth/login"),
            (self.library, "POST", "/api/v1/steam/link"),
            (self.library, "POST", "/api/v1/steam/sync"),
            (self.sync, "GET", "/api/v1/games"),
            (self.sync, "POST", "/api/v1/auth/login"),
            (self.library, "GET", "/api/v1/games/1/hltb-review"),
            (self.library, "PUT", "/api/v1/games/1/hltb-selection"),
            (self.enrichment, "GET", "/api/v1/games"),
            (self.enrichment, "GET", "/api/v1/games/1/hltb-review"),
            (self.enrichment, "PUT", "/api/v1/games/1/hltb-selection"),
            (self.library, "GET", "/api/v1/enrichment/games/1/hltb-review"),
            (self.enrichment, "PATCH", "/api/v1/games/1"),
        )
        for client, method, path in requests:
            with self.subTest(service=client.app.title, method=method, path=path):
                self.assertEqual(client.request(method, path).status_code, 404)

    def test_enrichment_uses_gateway_identity_without_decoding_tokens(self):
        with patch("app.core.security.jwt.decode", side_effect=AssertionError("Duplicate JWT check")):
            self.assertEqual(self.enrichment.get("/api/v1/enrichment/games/1/hltb-review",
                                               headers={"X-User-ID": "1"}).status_code, 404)
        for headers in ({}, {"X-User-ID": "invalid"}, {"Authorization": "Bearer invalid"}):
            self.assertEqual(self.enrichment.get("/api/v1/enrichment/games/1/hltb-review", headers=headers).status_code, 401)

    def test_sync_uses_authenticated_users_linked_steam_account(self):
        with Session(self.engine) as session:
            user = User(
                username="linked-user", hashed_password="unused",
                steam_id="76561198000000000",
            )
            session.add(user)
            session.commit()
            user_id = user.id
        headers = {"X-User-ID": str(user_id)}
        steam_response = httpx.Response(
            200, json={"response": {"game_count": 1, "games": [{"appid": 570}]}},
            request=httpx.Request("GET", "https://api.steampowered.com"),
        )
        with (
            patch("sync_service.steam.STEAM_API_KEY", "test-key"),
            patch("sync_service.steam.httpx.AsyncClient") as client,
            patch("sync_service.main.save_library", return_value=(1, 1)) as save,
            patch("sync_service.main.publish_games_imported") as enrich,
        ):
            from sync_service.events import ImportEventSubmission
            enrich.return_value = ImportEventSubmission()
            client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=steam_response,
            )
            fetch = client.return_value.__aenter__.return_value.get
            response = self.sync.post(
                "/api/v1/steam/sync?steam_id=attacker", headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "game_count": 1, "inserted_count": 1, "ownerships_inserted_count": 1,
                "enrichment": ImportEventSubmission().model_dump(),
            })
            fetch.assert_awaited_once()
            self.assertEqual(
                fetch.call_args.kwargs["params"]["steamid"], "76561198000000000",
            )
            self.assertEqual(save.call_args.args[0], user_id)
            self.assertEqual(save.call_args.args[1].games[0].appid, 570)

    def test_invalid_tokens_are_rejected_by_each_service(self):
        for client, method, path in (
            (self.auth, "GET", "/api/v1/auth/me"),
            (self.library, "GET", "/api/v1/games"),
            (self.sync, "POST", "/api/v1/steam/sync"),
        ):
            with self.subTest(service=client.app.title):
                response = client.request(
                    method, path, headers={"Authorization": "Bearer invalid"},
                )
                self.assertEqual(response.status_code, 401)

    def test_gateway_check_returns_verified_identity(self):
        with Session(self.engine) as session:
            user = User(username="gateway-user", hashed_password="unused")
            session.add(user)
            session.commit()
            user_id = user.id
        response = self.auth.get("/internal/auth/check", headers={
            "Authorization": f"Bearer {create_access_token(user_id)}",
            "X-User-ID": "99999",
        })
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["x-user-id"], str(user_id))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.content, b"")
        self.assertNotIn("/internal/auth/check", auth_app.openapi()["paths"])
        for client in (self.library, self.sync):
            self.assertEqual(client.get("/internal/auth/check").status_code, 404)

    def test_gateway_check_rejects_unverified_identity(self):
        expired = jwt.encode(
            {"sub": "1", "exp": datetime.now(UTC) - timedelta(minutes=1)},
            SECRET_KEY, algorithm=ALGORITHM,
        )
        wrong_signature = jwt.encode(
            {"sub": "1", "exp": datetime.now(UTC) + timedelta(minutes=1)},
            "a-different-secret-at-least-32-characters", algorithm=ALGORITHM,
        )
        missing_expiration = jwt.encode({"sub": "1"}, SECRET_KEY, algorithm=ALGORITHM)
        for credentials in (
            {}, {"Authorization": "Basic invalid"},
            {"Authorization": "Bearer invalid"},
            {"Authorization": f"Bearer {expired}"},
            {"Authorization": f"Bearer {wrong_signature}"},
            {"Authorization": f"Bearer {missing_expiration}"},
            {"Authorization": f"Bearer {create_access_token(99999)}"},
        ):
            with self.subTest(credentials=credentials):
                response = self.auth.get(
                    "/internal/auth/check", headers={**credentials, "X-User-ID": "1"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
                self.assertNotIn("x-user-id", response.headers)

    def test_downstream_routes_use_identity_without_decoding_jwt(self):
        with Session(self.engine) as session:
            user = User(username="identity-user", hashed_password="unused")
            session.add(user)
            session.commit()
            user_id = user.id
        with patch("app.core.security.jwt.decode", side_effect=AssertionError("Duplicate JWT check")):
            for client, method, path, expected in (
                (self.auth, "GET", "/api/v1/auth/me", 200),
                (self.library, "GET", "/api/v1/games", 200),
                (self.sync, "POST", "/api/v1/steam/sync", 409),
            ):
                response = client.request(method, path, headers={
                    "X-User-ID": str(user_id), "Authorization": "Bearer ignored",
                })
                self.assertEqual(response.status_code, expected)

    def test_downstream_rejects_missing_malformed_or_unknown_identity(self):
        for client, method, path in (
            (self.auth, "GET", "/api/v1/auth/me"),
            (self.library, "GET", "/api/v1/games"),
            (self.sync, "POST", "/api/v1/steam/sync"),
        ):
            for identity in (None, "", "-1", "0", "1,2", "abc", "9" * 100):
                with self.subTest(service=client.app.title, identity=identity):
                    headers = {} if identity is None else {"X-User-ID": identity}
                    self.assertEqual(client.request(method, path, headers=headers).status_code, 401)

    def test_library_uses_verified_id_without_loading_user(self):
        with patch("app.core.identity.Session.get", side_effect=AssertionError("Unnecessary user lookup")):
            response = self.library.get("/api/v1/games", headers={"X-User-ID": "99999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_account_routes_reject_unknown_user_identity(self):
        for client, method, path in (
            (self.auth, "GET", "/api/v1/auth/me"),
            (self.sync, "POST", "/api/v1/steam/sync"),
        ):
            self.assertEqual(client.request(method, path, headers={"X-User-ID": "99999"}).status_code, 401)
