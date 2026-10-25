import unittest
import sys
from pathlib import Path
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

from app.database import CompletionStatus, Game, OwnedGame, PersonalHLTBOverride, User, engine
from library_service.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enrichment-service"))
from enrichment_service.main import app as enrichment_app


class OwnedGamesTests(unittest.TestCase):
    def setUp(self):
        # Recreate only the isolated test database and start the library service.
        self.assertEqual(engine.url.database, "steam_test")
        from enrichment_service.review_cache import client
        client.flushdb()
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self.review_client = TestClient(enrichment_app)
        self.addCleanup(self.review_client.close)
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
                {"X-User-ID": str(user.id)}
                for user in users
            ]
            self.games = [
                {"id": game.id, "steam_app_id": game.steam_app_id,
                 "name": None, "cover_url": None, "cover_fallback_url": None, "completion_status": "not started",
                 "single_player": None, "multiplayer": None, "recommendation_hidden": False, "is_free": None, "is_test_build": False,
                 "hltb_id": None, "main_story_hours": None, "main_extra_hours": None,
                 "completionist_hours": None, "hltb_source": None, "hltb_review_needed": False, "enrichment_status": None, "enrichment_last_lookup_at": None}
                for game in games
            ]

    def test_users_receive_only_their_games(self):
        # Each owner sees the shared game and only their own private game.
        for index, expected in ((0, self.games[:2]), (1, self.games[1:])):
            response = self.client.get("/api/v1/games", headers=self.headers[index])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)

    def test_personal_estimates_override_shared_values_without_leaking(self):
        game_id = self.games[1]["id"]
        with Session(engine) as session:
            game = session.get(Game, game_id)
            game.hltb_id = 10
            game.main_story_hours = 12.5
            game.main_extra_hours = 20
            game.completionist_hours = 30
            session.add(game)
            session.add(PersonalHLTBOverride(
                user_id=int(self.headers[0]["X-User-ID"]), game_id=game_id,
                hltb_id=20, main_story_hours=15, completionist_hours=40,
                candidate={"hltb_id": 20}, selected_at=datetime.now(UTC),
            ))
            session.commit()
        for index, expected in [(0, (20, 15, None, 40, "personal")),
                                (1, (10, 12.5, 20, 30, "shared"))]:
            games = self.client.get("/api/v1/games", headers=self.headers[index]).json()
            game = next(g for g in games if g["id"] == game_id)
            fields = ("hltb_id", "main_story_hours", "main_extra_hours", "completionist_hours", "hltb_source")
            self.assertEqual(tuple(game[f] for f in fields), expected)
            patched = self.client.patch(f"/api/v1/games/{game_id}", headers=self.headers[index],
                                        json={"completion_status": "finished"})
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(tuple(patched.json()[f] for f in fields), expected)

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

    def test_update_status_persists_and_keeps_other_owner_independent(self):
        game = self.games[1]
        for status in ("in progress", "finished", "not started", "not started"):
            with self.subTest(status=status):
                response = self.client.patch(
                    f"/api/v1/games/{game['id']}", headers=self.headers[0],
                    json={"completion_status": status},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {**game, "completion_status": status})
                library = self.client.get("/api/v1/games", headers=self.headers[0]).json()
                self.assertEqual(library[1]["completion_status"], status)
                other_library = self.client.get("/api/v1/games", headers=self.headers[1]).json()
                self.assertEqual(other_library[0]["completion_status"], "not started")

    def test_update_rejects_invalid_or_missing_status(self):
        for body in ({}, {"completion_status": "invalid"}, {"completion_status": None}):
            with self.subTest(body=body):
                response = self.client.patch(
                    f"/api/v1/games/{self.games[0]['id']}",
                    headers=self.headers[0], json=body,
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(
            self.client.get("/api/v1/games", headers=self.headers[0]).json(),
            self.games[:2],
        )

    def test_update_unknown_or_other_users_game_returns_not_found(self):
        for game_id in (self.games[2]["id"], max(game["id"] for game in self.games) + 1):
            response = self.client.patch(
                f"/api/v1/games/{game_id}?user_id=2", headers=self.headers[0],
                json={"completion_status": "finished"},
            )
            self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/games", headers=self.headers[1]).json(),
            self.games[1:],
        )

    def test_update_requires_authentication(self):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = self.client.patch(
                f"/api/v1/games/{self.games[0]['id']}", headers=headers,
                json={"completion_status": "finished"},
            )
            self.assertIn(response.status_code, (401, 403))

    def seed_review(self, game_id):
        from enrichment_service.hltb import HLTBCandidate
        from enrichment_service.matching import match_candidates
        from enrichment_service.storage import save_match_result

        candidates = [HLTBCandidate(i, "Example", None, "game", ("PC",), 2020,
                                    float(i), None, None) for i in (10, 20)]
        with Session(engine) as session:
            result = match_candidates("Example", candidates)
            record = save_match_result(session, game_id, result)
            from enrichment_service.review_cache import claim_refresh, begin_refresh, put_review, release_refresh
            token = claim_refresh(game_id)
            begin_refresh(game_id, token)
            put_review(game_id, token, result=result, last_success_at=record.last_success_at)
            release_refresh(game_id, token)
            session.commit()

    def test_review_selection_is_personal_and_preserves_progress(self):
        game_id = self.games[1]["id"]
        self.seed_review(game_id)
        review = self.review_client.get(f"/api/v1/enrichment/games/{game_id}/hltb-review", headers=self.headers[0])
        self.assertEqual(review.status_code, 200)
        self.assertEqual([c["hltb_id"] for c in review.json()["candidates"]], [10, 20])
        selected = self.review_client.put(f"/api/v1/enrichment/games/{game_id}/hltb-selection", headers=self.headers[0], json={
            "hltb_id": 20, "cache_version": review.json()["cache_version"],
        })
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["hltb_id"], 20)
        self.assertEqual(selected.json()["main_story_hours"], 20)
        self.assertEqual(selected.json()["hltb_source"], "personal")
        self.assertFalse(selected.json()["hltb_review_needed"])
        own = next(g for g in self.client.get("/api/v1/games", headers=self.headers[0]).json() if g["id"] == game_id)
        self.assertEqual(own["completion_status"], "not started")
        other = next(g for g in self.client.get("/api/v1/games", headers=self.headers[1]).json() if g["id"] == game_id)
        self.assertIsNone(other["hltb_id"])
        self.assertTrue(other["hltb_review_needed"])
        with Session(engine) as session:
            self.assertIsNone(session.get(Game, game_id).hltb_id)

    def test_review_routes_enforce_ownership_and_auth(self):
        game_id = self.games[0]["id"]
        self.seed_review(game_id)
        for headers, expected in (({}, 401), (self.headers[1], 404)):
            self.assertEqual(self.review_client.get(f"/api/v1/enrichment/games/{game_id}/hltb-review", headers=headers).status_code, expected)
            self.assertEqual(self.review_client.put(f"/api/v1/enrichment/games/{game_id}/hltb-selection", headers=headers, json={
                "hltb_id": 10, "cache_version": "unused",
            }).status_code, expected)
        self.assertEqual(self.review_client.get(f"/api/v1/enrichment/games/{self.games[1]['id']}/hltb-review", headers=self.headers[0]).status_code, 409)

    def test_stale_invalid_and_extra_selection_data_are_rejected(self):
        game_id = self.games[1]["id"]
        self.seed_review(game_id)
        review = self.review_client.get(f"/api/v1/enrichment/games/{game_id}/hltb-review", headers=self.headers[0]).json()
        base = {"hltb_id": 10, "cache_version": review["cache_version"]}
        for body, expected in [({**base, "hltb_id": -1}, 422),
                               ({**base, "hltb_id": True}, 422),
                               ({**base, "hltb_id": 99}, 409),
                               ({**base, "candidate_index": 0}, 422),
                               ({**base, "cache_version": ""}, 422)]:
            self.assertEqual(self.review_client.put(f"/api/v1/enrichment/games/{game_id}/hltb-selection", headers=self.headers[0], json=body).status_code, expected)
        self.seed_review(game_id)
        self.assertEqual(self.review_client.put(f"/api/v1/enrichment/games/{game_id}/hltb-selection", headers=self.headers[0], json=base).status_code, 409)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(PersonalHLTBOverride)).all(), [])

    def test_library_exposes_saved_lookup_progress(self):
        from app.database import GameEnrichment, EnrichmentStatus
        game_id = self.games[1]["id"]
        with Session(engine) as session:
            session.add(GameEnrichment(game_id=game_id, status=EnrichmentStatus.NO_MATCH,
                reason="no_exact_title", last_lookup_at=datetime.now(UTC)))
            session.commit()
        games = self.client.get('/api/v1/games', headers=self.headers[0]).json()
        game = next(game for game in games if game['id'] == game_id)
        self.assertEqual(game['enrichment_status'], 'no_match')
        self.assertIsNotNone(game['enrichment_last_lookup_at'])

    def test_expired_review_queues_one_refresh_and_rejects_selection(self):
        from unittest.mock import patch
        from enrichment_service.review_cache import client, key
        game_id = self.games[1]['id']
        self.seed_review(game_id)
        old = self.review_client.get(f'/api/v1/enrichment/games/{game_id}/hltb-review', headers=self.headers[0]).json()
        client.delete(key(game_id))
        with patch('enrichment_service.review_dispatch.publisher.connection_for_write'), \
             patch('enrichment_service.review_dispatch.publisher.send_task') as send:
            for headers in self.headers[:2]:
                response = self.review_client.get(f'/api/v1/enrichment/games/{game_id}/hltb-review', headers=headers)
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()['status'], 'refreshing')
            send.assert_called_once()
        response = self.review_client.put(f'/api/v1/enrichment/games/{game_id}/hltb-selection', headers=self.headers[0],
            json={'hltb_id': 10, 'cache_version': old['cache_version']})
        self.assertEqual(response.status_code, 409)

    def test_personal_selection_survives_cache_loss(self):
        from enrichment_service.review_cache import client, key
        game_id = self.games[1]['id']
        self.seed_review(game_id)
        review = self.review_client.get(f'/api/v1/enrichment/games/{game_id}/hltb-review', headers=self.headers[0]).json()
        response = self.review_client.put(f'/api/v1/enrichment/games/{game_id}/hltb-selection', headers=self.headers[0],
            json={'hltb_id': 20, 'cache_version': review['cache_version']})
        self.assertEqual(response.status_code, 200)
        client.delete(key(game_id))
        game = next(g for g in self.client.get('/api/v1/games', headers=self.headers[0]).json() if g['id'] == game_id)
        self.assertEqual(game['hltb_id'], 20)
        self.assertEqual(game['hltb_source'], 'personal')

    def test_cache_outage_returns_helpful_error(self):
        from unittest.mock import patch
        from redis.exceptions import ConnectionError
        game_id = self.games[1]['id']
        self.seed_review(game_id)
        with patch('enrichment_service.api.get_review', side_effect=ConnectionError('offline')):
            response = self.review_client.get(f'/api/v1/enrichment/games/{game_id}/hltb-review', headers=self.headers[0])
        self.assertEqual(response.status_code, 503)
