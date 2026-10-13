import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'enrichment-service'))
from enrichment_service import review_cache as cache
from enrichment_service.hltb import HLTBCandidate
from enrichment_service.matching import review_match_candidates


class ReviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('test-cache', cache.client.connection_pool.connection_kwargs['host'])
        cache.client.flushdb()
        self.candidate = HLTBCandidate(1, 'Call of Duty: Modern Warfare', None, 'game', ('PC',), 2019, 6, None, None)

    def test_top_three_are_distinct_and_similar_titles_require_review(self):
        entries = [self.candidate, self.candidate,
            replace(self.candidate, hltb_id=2, name='Call of Duty 4: Modern Warfare', release_year=2007),
            replace(self.candidate, hltb_id=3, name='Call of Duty: Modern Warfare Remastered'),
            replace(self.candidate, hltb_id=4, name='Call of Duty: Modern Warfare II'),
            replace(self.candidate, hltb_id=5, name='Unrelated Puzzle')]
        result = review_match_candidates(self.candidate.name, entries)
        self.assertEqual(result.status.value, 'ambiguous')
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(len({c.hltb_id for c in result.candidates}), 3)
        self.assertNotIn(5, [c.hltb_id for c in result.candidates])

    def test_ttl_expiry_and_stale_worker_fencing(self):
        token = cache.claim_refresh(1)
        self.assertTrue(cache.begin_refresh(1, token))
        self.assertFalse(cache.begin_refresh(1, token))
        result = review_match_candidates(self.candidate.name, [self.candidate, replace(self.candidate, hltb_id=2)])
        cache.put_review(1, token, result=result, last_success_at=datetime.now(UTC))
        self.assertGreater(cache.client.ttl(cache.key(1)), 86390)
        cache.client.expire(cache.key(1), 0)
        self.assertIsNone(cache.get_review(1))
        cache.client.delete(cache.lease_key(1))
        new = cache.claim_refresh(1)
        cache.release_refresh(1, token)
        self.assertEqual(cache.client.get(cache.lease_key(1)), new)
        with self.assertRaises(ValueError):
            cache.put_review(1, token, result=result, last_success_at=datetime.now(UTC))

    def test_only_games_with_review_suggestions_are_cached(self):
        token = cache.claim_refresh(1)
        self.assertTrue(cache.begin_refresh(1, token))
        ambiguous = review_match_candidates(self.candidate.name,
            [self.candidate, replace(self.candidate, hltb_id=2)])
        for candidates in ([self.candidate], []):
            cache.put_review(1, token, result=ambiguous, last_success_at=datetime.now(UTC))
            self.assertIsNotNone(cache.get_review(1))
            resolved = review_match_candidates(self.candidate.name, candidates)
            cache.put_review(1, token, result=resolved, last_success_at=datetime.now(UTC))
            self.assertIsNone(cache.get_review(1))

    def test_concurrent_refresh_requests_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(pool.map(lambda _: cache.claim_refresh(1), range(8)))
        self.assertEqual(sum(token is not None for token in tokens), 1)
