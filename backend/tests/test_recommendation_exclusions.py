import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.database import OwnedGame, GameMetadata, engine
from recommendation_service.schemas import RecommendationRequest
from recommendation_service.ranking import Candidate, rank_candidates
import test_recommendation_api as fixtures


class ExclusionRankingTests(unittest.TestCase):
    def test_free_flag_and_hidden_preference(self):
        base = Candidate(1,'Example',None,'not started',True,frozenset(),True,'shared',main_story_hours=5)
        prefs = RecommendationRequest(weekly_hours=4,target_weeks=3)
        self.assertEqual(rank_candidates([replace(base,is_free=True)], prefs), [])
        self.assertEqual(len(rank_candidates([replace(base,is_free=True)], prefs.model_copy(update={'include_free_games':True}))),1)
        self.assertEqual(len(rank_candidates([base],prefs)),1)  # Unknown is not guessed free.
        self.assertEqual(rank_candidates([replace(base,recommendation_hidden=True)], prefs.model_copy(update={'include_free_games':True})), [])


class ExclusionAPITests(fixtures.RecommendationAPITests):
    def test_verified_free_filter_and_coverage(self):
        with Session(self.engine) as session:
            metadata=session.get(GameMetadata,self.ids[0]); metadata.is_free=True
            session.add(metadata);session.commit()
        response=self.post().json()
        self.assertNotIn(self.ids[0],[item['game_id'] for item in response['recommendations']])
        self.assertEqual(response['pagination']['total'],2)
        self.assertIn(self.ids[0],[item['game_id'] for item in self.post(include_free_games=True).json()['recommendations']])

    def test_hide_restore_ownership_and_sync_preservation(self):
        from library_service.main import app as library_app
        from app.database import get_session
        def sessions():
            with Session(self.engine) as session: yield session
        library_app.dependency_overrides[get_session]=sessions
        self.addCleanup(library_app.dependency_overrides.clear)
        client=TestClient(library_app);self.addCleanup(client.close)
        path=f'/api/v1/games/{self.ids[2]}/recommendation-preference'
        headers={'X-User-ID':str(self.owner)}
        self.assertEqual(client.put(path,json={'hidden':True}).status_code,401)
        self.assertEqual(client.put(path,json={'hidden':True},headers={'X-User-ID':str(self.other)}).status_code,404)
        self.assertEqual(client.put(path,json={'hidden':'true'},headers=headers).status_code,422)
        for _ in range(2): self.assertEqual(client.put(path,json={'hidden':True},headers=headers).status_code,200)
        self.assertNotIn(self.ids[2],[g['game_id'] for g in self.post(include_free_games=True).json()['recommendations']])
        with Session(self.engine) as session:
            ownership=session.get(OwnedGame,(self.owner,self.ids[2]))
            self.assertTrue(ownership.recommendation_hidden)
            self.assertEqual(ownership.completion_status.value,'not started')
            from sync_service.steam import OwnedGames, SteamGame, insert_owned_games
            self.assertEqual(insert_owned_games(session,self.owner,
                OwnedGames(game_count=1,games=[SteamGame(appid=102)])),0)
            session.commit()
            self.assertTrue(session.get(OwnedGame,(self.owner,self.ids[2])).recommendation_hidden)
            # Other-owner hidden preference remains independent.
            self.assertFalse(session.get(OwnedGame,(self.other,self.ids[0])).recommendation_hidden)
        self.assertEqual(client.put(path,json={'hidden':False},headers=headers).status_code,200)
        self.assertIn(self.ids[2],[g['game_id'] for g in self.post().json()['recommendations']])

    def test_free_backfill_preserves_other_metadata(self):
        from unittest.mock import patch, AsyncMock
        from metadata_worker.backfill_free import backfill
        from metadata_worker.steam_metadata import SteamMetadata
        with Session(self.engine) as session:
            record=session.get(GameMetadata,self.ids[0]);record.last_success_at=datetime.now(UTC)
            session.add(record);session.commit()
        # Backfill uses the app database engine; isolate it to this test fixture.
        with patch('metadata_worker.backfill_free.engine',self.engine), patch(
                'metadata_worker.backfill_free.wait_for_slot'), patch(
                'metadata_worker.backfill_free.lookup_metadata',new_callable=AsyncMock,
                return_value=SteamMetadata(False,(),is_free=True)):
            self.assertEqual(backfill(limit=1)['updated'],1)
        with Session(self.engine) as session:
            record=session.get(GameMetadata,self.ids[0])
            self.assertTrue(record.is_free)
            self.assertTrue(record.single_player)
            self.assertEqual(record.genres,[{'id':'1','name':'Action'}])


class ExclusionMigrationTests(unittest.TestCase):
    def test_rerun_preserves_records_and_defaults(self):
        self.assertEqual(engine.url.database,'steam_test')
        schema='exclusions_'+uuid4().hex
        sql=(Path(__file__).resolve().parents[1]/'migrations/010_add_recommendation_exclusions.sql').read_text()
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
            try:
                connection.exec_driver_sql(f'SET search_path TO {schema}')
                connection.exec_driver_sql('CREATE TABLE game_metadata(game_id INTEGER PRIMARY KEY, genres JSON)')
                connection.exec_driver_sql('CREATE TABLE ownedgame(user_id INTEGER, game_id INTEGER, completion_status TEXT)')
                connection.exec_driver_sql("INSERT INTO game_metadata VALUES (1,'[]')")
                connection.exec_driver_sql("INSERT INTO ownedgame VALUES (1,1,'finished')")
                for _ in range(2): connection.exec_driver_sql(sql)
                self.assertEqual(tuple(connection.exec_driver_sql('SELECT completion_status,recommendation_hidden FROM ownedgame').one()),('finished',False))
                self.assertIsNone(connection.exec_driver_sql('SELECT is_free FROM game_metadata').scalar_one())
            finally:
                connection.exec_driver_sql('SET search_path TO public')
                connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
