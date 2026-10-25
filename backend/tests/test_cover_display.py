import os
import unittest
from unittest.mock import MagicMock, patch
from botocore.exceptions import NoCredentialsError
from app.covers import resolve_cover
from app.database import Game, OwnedGame
from library_service.games import _response
from recommendation_service.api import cover_response
from recommendation_service.ranking import Candidate, rank_candidates
from recommendation_service.schemas import RecommendationRequest


class CoverDisplayTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, S3_COVERS_ENABLED='true', S3_BUCKET='bucket')
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_and_missing_key_never_initialize_sdk(self):
        with patch('app.covers.s3_client') as client:
            self.assertEqual(resolve_cover('steam', None)['cover_url'], 'steam')
            with patch.dict(os.environ, S3_COVERS_ENABLED='false'):
                self.assertEqual(resolve_cover('steam', 'covers/a.jpg')['cover_url'], 'steam')
            client.assert_not_called()

    def test_signs_locally_and_preserves_fallback(self):
        with patch('app.covers.s3_client') as create:
            client = create.return_value
            client.generate_presigned_url.return_value = 'signed'
            self.assertEqual(resolve_cover('steam', 'covers/a.jpg'),
                {'cover_url': 'signed', 'cover_fallback_url': 'steam'})
            client.generate_presigned_url.assert_called_once_with('get_object',
                Params={'Bucket': 'bucket', 'Key': 'covers/a.jpg'}, ExpiresIn=900)
            client.head_object.assert_not_called()
            client.get_object.assert_not_called()

    def test_missing_credentials_fall_back(self):
        with patch('app.covers.s3_client', side_effect=NoCredentialsError()):
            self.assertEqual(resolve_cover('steam', 'covers/a.jpg')['cover_url'], 'steam')

    def test_library_and_ranked_api_share_resolution_without_leaking_key(self):
        game = Game(id=1, steam_app_id=620, name='Portal 2', cover_url='steam', cover_s3_key='covers/a.jpg')
        candidate = Candidate(game_id=1, title='Portal 2', cover_url='steam',
            completion_status='not started', single_player=True, genre_ids=frozenset(),
            match_resolved=True, estimate_source='shared', main_story_hours=8,
            cover_s3_key=game.cover_s3_key)
        ranked = rank_candidates([candidate], RecommendationRequest(weekly_hours=4, target_weeks=3))[0]
        with patch('app.covers.s3_client') as client:
            client.return_value.generate_presigned_url.return_value = 'signed'
            library = _response(game, OwnedGame(user_id=1, game_id=1), None).model_dump()
            recommended = cover_response(ranked).model_dump()
        for data in (library, recommended):
            self.assertEqual((data['cover_url'], data['cover_fallback_url']), ('signed', 'steam'))
            self.assertNotIn('cover_s3_key', data)
