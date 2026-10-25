import unittest
from dataclasses import replace

from recommendation_service.api import RecommendationRequest
from recommendation_service.ranking import Candidate, rank_candidates


class RecommendationRankingTests(unittest.TestCase):
    def setUp(self):
        self.preferences = RecommendationRequest(weekly_hours=4, target_weeks=3,
            preferred_genres=['1', '25'])
        self.game = Candidate(game_id=1, title='Example', cover_url=None,
            completion_status='not started', single_player=True,
            genre_ids=frozenset({'1', '25'}), match_resolved=True,
            estimate_source='shared', main_story_hours=12)

    def rank(self, *games, **preferences):
        request = RecommendationRequest(**(self.preferences.model_dump() | preferences))
        return rank_candidates(games, request)

    def test_exact_budget_and_no_rounding_or_category_substitution(self):
        self.assertEqual(self.rank(self.game)[0].estimated_weeks, 3)
        for hours in (12.000001, None, 0, -1, float('nan'), float('inf'), True):
            with self.subTest(hours=hours):
                self.assertEqual(self.rank(replace(self.game, main_story_hours=hours)), [])
        self.assertEqual(self.rank(self.game, completion_goal='main_extra'), [])
        extra = replace(self.game, main_extra_hours=8, completionist_hours=11)
        self.assertEqual(self.rank(extra, completion_goal='main_extra')[0].estimated_hours, 8)
        self.assertEqual(self.rank(extra, completion_goal='completionist')[0].estimated_hours, 11)

    def test_eligibility_requires_verified_mode_unstarted_and_resolved_match(self):
        for change in ({'single_player': None}, {'single_player': False},
                       {'match_resolved': False}, {'completion_status': 'finished'},
                       {'completion_status': 'in progress'}):
            with self.subTest(change=change):
                self.assertEqual(self.rank(replace(self.game, **change)), [])

    def test_genre_fit_dominates_then_time_fit_and_genres_are_preferences(self):
        full = replace(self.game, game_id=3, main_story_hours=2)
        partial = replace(self.game, game_id=2, genre_ids=frozenset({'1'}))
        none = replace(self.game, game_id=1, genre_ids=frozenset())
        self.assertEqual([g.game_id for g in self.rank(none, partial, full)], [3, 2, 1])
        shorter = replace(self.game, game_id=4, main_story_hours=6)
        self.assertEqual([g.game_id for g in self.rank(shorter, self.game)], [1, 4])
        self.assertEqual(self.rank(partial)[0].matched_genres, ('1',))
        self.assertIn('Matches 0 of your 2 preferred genres.', self.rank(none)[0].reasons)

    def test_empty_genres_time_only_and_stable_ties(self):
        games = [replace(self.game, game_id=i) for i in (5, 2, 3, 1)]
        self.assertEqual([g.game_id for g in self.rank(*games, preferred_genres=[])], [1, 2, 3])
        self.assertEqual(self.rank(*games), self.rank(*reversed(games)))

    def test_queued_games_and_personal_estimates_are_preserved(self):
        queued = replace(self.game, queue_position=0, estimate_source='personal', main_story_hours=7)
        result = self.rank(queued)[0]
        self.assertEqual(result.queue_position, 0)
        self.assertEqual(result.estimate_source, 'personal')
        self.assertEqual(result.estimated_hours, 7)
        self.assertEqual(result.estimated_weeks, 1.75)
        self.assertEqual(queued.queue_position, 0)

    def test_distinct_results_empty_input_and_conflicting_duplicates(self):
        self.assertEqual(self.rank(), [])
        self.assertEqual(len(self.rank(self.game, self.game)), 1)
        with self.assertRaises(ValueError):
            self.rank(self.game, replace(self.game, main_story_hours=5))


    def test_pagination_returns_successive_distinct_ranked_groups(self):
        games = [replace(self.game, game_id=i) for i in range(1, 9)]
        pages = [self.rank(*games, offset=offset) for offset in (0, 3, 6, 9)]
        self.assertEqual([[item.game_id for item in page] for page in pages],
                         [[1, 2, 3], [4, 5, 6], [7, 8], []])

    def test_trailers_pass_through_without_changing_ranking_or_eligibility(self):
        video=replace(self.game,trailer_url='https://video.akamai.steamstatic.com/a.m3u8',trailer_format='hls')
        result=self.rank(video)[0]
        self.assertEqual(result.trailer_url,video.trailer_url)
        self.assertEqual(result.trailer_format,'hls')
        self.assertIsNone(self.rank(self.game)[0].trailer_url)
