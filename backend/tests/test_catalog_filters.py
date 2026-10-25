import unittest
from dataclasses import replace
from app.catalog_filters import effective_free_flag, is_test_build
from recommendation_service.ranking import Candidate, rank_candidates
from recommendation_service.schemas import RecommendationRequest


class CatalogFilterTests(unittest.TestCase):
    def test_explicit_free_genre_overrides_conflicting_boolean(self):
        self.assertTrue(effective_free_flag(False,[{'id':'37','name':'Free To Play'}]))
        self.assertTrue(effective_free_flag(None,[{'id':'37'}]))
        self.assertIsNone(effective_free_flag(None,[]))
        self.assertFalse(effective_free_flag(False,[{'id':'1'}]))

    def test_explicit_test_markers_only(self):
        for title in ('For Honor - Public Test','Siege - Test Server','ARC Raiders Playtest','Wildlands Open Beta'):
            self.assertTrue(is_test_build(title))
        for title in ('Ready Player One: OASIS beta','The Test','Beta Protocol',None):
            self.assertFalse(is_test_build(title))

    def test_test_builds_excluded_even_with_free_games_enabled(self):
        candidate=Candidate(1,'Public Test',None,'not started',True,frozenset(),True,'shared',main_story_hours=5,is_test_build=True)
        self.assertEqual(rank_candidates([candidate],RecommendationRequest(weekly_hours=4,target_weeks=3,include_free_games=True)),[])
