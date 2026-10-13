import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from auth_service.steam import STEAM_LINK_COOKIE
from app.database import SteamLinkRequest, SteamOpenIDNonce, User, engine
from auth_service.main import app


class SteamLinkTests(unittest.TestCase):
    def setUp(self):
        # Recreate only the disposable test database for each scenario.
        self.assertEqual(engine.url.database, "steam_test")
        SQLModel.metadata.drop_all(engine)
        self.client = TestClient(app)
        self.client.__enter__()
        registered = self.client.post("/api/v1/auth/register", json={
            "username": "tester", "password": "test-password",
        })
        self.assertEqual(registered.status_code, 201)
        self.assertIsNone(registered.json()["steam_id"])
        login = self.client.post("/api/v1/auth/login", json={
            "username": "tester", "password": "test-password",
        })
        self.assertEqual(login.status_code, 200)
        self.headers = {"X-User-ID": str(registered.json()["id"])}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def start(self):
        result = self.client.post("/api/v1/steam/link", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/api/v1/steam/callback", cookie)
        self.assertEqual(result.headers["cache-control"], "no-store")
        url = urlsplit(result.json()["login_url"])
        self.assertEqual(url.netloc, "steamcommunity.com")
        return_to = parse_qs(url.query)["openid.return_to"][0]
        state = parse_qs(urlsplit(return_to).query)["state"][0]
        return {
            "state": state,
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "id_res",
            "openid.op_endpoint": "https://steamcommunity.com/openid/login",
            "openid.return_to": return_to,
            "openid.claimed_id": "https://steamcommunity.com/openid/id/76561198000000000",
            "openid.identity": "https://steamcommunity.com/openid/id/76561198000000000",
            "openid.response_nonce": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") + "random",
            "openid.assoc_handle": "test-association",
            "openid.signed": "op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
            "openid.sig": "test-signature",
        }

    def callback(self, params, valid=True, browser=False):
        # Mock only the outgoing Steam verification call, keeping API and PostgreSQL real.
        with patch("auth_service.steam.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = httpx.Response(
                200, request=httpx.Request("POST", "https://steamcommunity.com/openid/login"),
                text="ns:http://specs.openid.net/auth/2.0\nis_valid:" + str(valid).lower() + "\n",
            )
            result = self.client.get(
                "/api/v1/steam/callback", params=params,
                headers={"Accept": "text/html" if browser else "application/json"},
                follow_redirects=False,
            )
        return result

    def test_browser_callback_returns_to_frontend_and_clears_cookie(self):
        result = self.callback(self.start(), browser=True)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/?steam=linked")
        self.assertIn("Max-Age=0", result.headers["set-cookie"])
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIsNone(self.client.cookies.get(STEAM_LINK_COOKIE))

    def test_browser_callback_error_returns_to_frontend(self):
        result = self.client.get(
            "/api/v1/steam/callback", headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code, 303)
        query = parse_qs(urlsplit(result.headers["location"]).query)
        self.assertEqual(query["steam"], ["error"])
        self.assertIn("Invalid or expired", query["message"][0])

    def test_success_and_replay(self):
        params = self.start()
        browser_token = self.client.cookies.get(STEAM_LINK_COOKIE)
        self.assertEqual(self.callback(params).status_code, 200)
        me = self.client.get("/api/v1/auth/me", headers=self.headers)
        self.assertEqual(me.json()["steam_id"], "76561198000000000")
        self.assertIsNone(self.client.cookies.get(STEAM_LINK_COOKIE))
        with Session(engine) as session:
            self.assertIsNotNone(session.exec(select(SteamLinkRequest)).one().consumed_at)
            self.assertEqual(len(session.exec(select(SteamOpenIDNonce)).all()), 1)
        self.client.cookies.set(STEAM_LINK_COOKIE, browser_token)
        self.assertEqual(self.callback(params).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/steam/link", headers=self.headers).status_code, 409)

    def test_requires_authentication(self):
        self.assertIn(self.client.post("/api/v1/steam/link").status_code, (401, 403))

    def test_missing_cookie(self):
        params = self.start()
        self.client.cookies.clear()
        self.assertEqual(self.callback(params).status_code, 400)

    def test_wrong_cookie(self):
        params = self.start()
        self.client.cookies.clear()
        self.client.cookies.set(STEAM_LINK_COOKIE, "wrong")
        self.assertEqual(self.callback(params).status_code, 400)

    def test_expired_request(self):
        params = self.start()
        with Session(engine) as session:
            pending = session.exec(select(SteamLinkRequest)).one()
            pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.add(pending)
            session.commit()
        self.assertEqual(self.callback(params).status_code, 400)

    def test_invalid_assertions(self):
        params = self.start()
        for field, value in {
            "state": "wrong", "openid.op_endpoint": "https://example.com",
            "openid.return_to": "http://localhost:8000/wrong",
            "openid.identity": "https://steamcommunity.com/openid/id/76561198000000001",
            "openid.signed": "identity", "openid.response_nonce": "invalid",
            "openid.claimed_id": "https://example.com/openid/id/76561198000000000",
        }.items():
            with self.subTest(field=field):
                self.assertEqual(self.callback({**params, field: value}).status_code, 400)

    def test_duplicate_parameters(self):
        params = self.start()
        self.assertEqual(self.callback(list(params.items()) + [("state", params["state"])]).status_code, 400)

    def test_steam_rejects_signature(self):
        self.assertEqual(self.callback(self.start(), valid=False).status_code, 400)
        self.assertIsNone(self.client.get("/api/v1/auth/me", headers=self.headers).json()["steam_id"])

    def test_duplicate_steam_account(self):
        params = self.start()
        with Session(engine) as session:
            session.add(User(username="other", hashed_password="unused", steam_id="76561198000000000"))
            session.commit()
        self.assertEqual(self.callback(params).status_code, 409)

    def test_used_nonce(self):
        from auth_service.steam import token_hash
        params = self.start()
        with Session(engine) as session:
            session.add(SteamOpenIDNonce(nonce_hash=token_hash(params["openid.response_nonce"])))
            session.commit()
        self.assertEqual(self.callback(params).status_code, 400)

    def test_steam_unavailable(self):
        params = self.start()
        with patch("auth_service.steam.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("offline")
            self.assertEqual(self.client.get("/api/v1/steam/callback", params=params).status_code, 502)
