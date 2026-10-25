import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from recommendation_service.api import RecommendationRequest
from recommendation_service.main import app


class RecommendationRequestTests(unittest.TestCase):
    def test_example_and_empty_genre_defaults(self):
        preferences = RecommendationRequest(weekly_hours=4, target_weeks=3,
            preferred_genres=['1', '25'])
        self.assertEqual(preferences.available_hours, 12)
        self.assertEqual(preferences.completion_goal, 'main_story')
        self.assertEqual(RecommendationRequest(weekly_hours=1.5, target_weeks=2).available_hours, 3)
        self.assertEqual(RecommendationRequest(weekly_hours=4, target_weeks=3).preferred_genres, [])

    def test_numeric_boundaries_and_types(self):
        for value in (True, False, '4', None, 0, -1, 169, float('nan'), float('inf')):
            with self.subTest(hours=value), self.assertRaises(ValidationError):
                RecommendationRequest(weekly_hours=value, target_weeks=3)
        for value in (True, '3', 3.0, 0, -1, 53):
            with self.subTest(weeks=value), self.assertRaises(ValidationError):
                RecommendationRequest(weekly_hours=4, target_weeks=value)
        self.assertEqual(RecommendationRequest(weekly_hours=168, target_weeks=52).available_hours, 8736)

    def test_genre_shape_uniqueness_and_limits(self):
        for values in (['1', '1'], ['0'], ['01'], ['Action'], ['-1'], [' 1'],
                       [1], [True], ['١'], [str(i) for i in range(1, 22)]):
            with self.subTest(genres=values), self.assertRaises(ValidationError):
                RecommendationRequest(weekly_hours=4, target_weeks=3, preferred_genres=values)

    def test_goals_and_extra_fields(self):
        for goal in ('main_story', 'main_extra', 'completionist'):
            self.assertEqual(RecommendationRequest(weekly_hours=4, target_weeks=3,
                completion_goal=goal).completion_goal, goal)
        for extra in ({'completion_goal': 'anything'}, {'user_id': 123}):
            with self.assertRaises(ValidationError):
                RecommendationRequest(weekly_hours=4, target_weeks=3, **extra)

    def test_health_and_recommendation_authentication(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/internal/health').json(), {'status': 'ok'})
            self.assertEqual(client.post('/api/v1/recommendations', json={'weekly_hours': 4, 'target_weeks': 3}).status_code, 401)
