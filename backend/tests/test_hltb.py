import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from howlongtobeatpy.JSONResultParser import JSONResultParser

# The service directory uses a hyphen; its Python package uses an underscore.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enrichment-service"))

from enrichment_service.hltb import HLTBLookupError, HLTBLookupTimeout, lookup_candidates


class HLTBLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("enrichment_service.hltb.HowLongToBeat")
        self.provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.search = self.provider.return_value.async_search = AsyncMock()

    async def test_maps_real_wrapper_entry_without_selecting_a_match(self):
        parser = JSONResultParser("Example", "https://howlongtobeat.com/game/", 0)
        entry = parser.parse_json_element({
            "game_id": 123, "game_name": "Example", "game_alias": "Alias",
            "game_type": "game", "profile_platform": "PC, PlayStation 5",
            "release_world": 2020, "comp_main": 5400, "comp_plus": 9000,
            "comp_100": 18000,
        })
        self.search.return_value = [entry, entry]
        candidates = await lookup_candidates(" Example ")
        self.assertEqual(len(candidates), 2)
        candidate = candidates[0]
        self.assertEqual((candidate.hltb_id, candidate.name, candidate.alias), (123, "Example", "Alias"))
        self.assertEqual(candidate.platforms, ("PC", "PlayStation 5"))
        self.assertEqual(candidate.release_year, 2020)
        self.assertEqual((candidate.main_story_hours, candidate.main_extra_hours,
                          candidate.completionist_hours), (1.5, 2.5, 5.0))
        self.provider.assert_called_once_with(input_minimum_similarity=0.0, input_auto_filter_times=False)
        self.search.assert_awaited_once_with("Example", similarity_case_sensitive=False)

    async def test_cleans_search_query_without_losing_title_identity(self):
        self.search.return_value = []
        await lookup_candidates("  Tom Clancy's Ghost Recon\u00ae Wildlands\u2122\t ")
        self.search.assert_awaited_once_with("Tom Clancy's Ghost Recon Wildlands", similarity_case_sensitive=False)
        self.search.reset_mock()
        await lookup_candidates("Example II: Deluxe Edition")
        self.search.assert_awaited_once_with("Example II: Deluxe Edition", similarity_case_sensitive=False)
        with self.assertRaises(ValueError):
            await lookup_candidates("\u00ae\u2122\u00a9")

    async def test_missing_and_zero_estimates_are_unknown(self):
        parser = JSONResultParser("Example", "https://howlongtobeat.com/game/", 0)
        self.search.return_value = [parser.parse_json_element({
            "game_id": 123, "game_name": "Example", "release_world": 0, "comp_main": 0,
        })]
        candidate = (await lookup_candidates("Example"))[0]
        self.assertIsNone(candidate.main_story_hours)
        self.assertIsNone(candidate.main_extra_hours)
        self.assertIsNone(candidate.completionist_hours)
        self.assertIsNone(candidate.release_year)
        self.assertEqual(candidate.platforms, ())

    async def test_empty_results_are_successful(self):
        self.search.return_value = []
        self.assertEqual(await lookup_candidates("Example"), [])

    async def test_invalid_estimates_reject_the_response(self):
        parser = JSONResultParser("Example", "https://howlongtobeat.com/game/", 0)
        entry = parser.parse_json_element({"game_id": 123, "game_name": "Example"})
        for value in (-1, float("nan"), float("inf"), "10", True):
            entry.main_story = value
            self.search.return_value = [entry]
            with self.assertRaises(HLTBLookupError):
                await lookup_candidates("Example")

    async def test_provider_failures_are_not_empty_results(self):
        for outcome in (None, {}, [SimpleNamespace(game_id=-1)]):
            self.search.return_value = outcome
            with self.assertRaises(HLTBLookupError):
                await lookup_candidates("Example")
        self.search.side_effect = RuntimeError("provider failure")
        with self.assertRaises(HLTBLookupError):
            await lookup_candidates("Example")

    async def test_deadline_cancels_provider(self):
        cancelled = asyncio.Event()

        async def slow(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.search.side_effect = slow
        with self.assertRaises(HLTBLookupTimeout):
            await lookup_candidates("Example", timeout_seconds=0.01)
        self.assertTrue(cancelled.is_set())

    async def test_caller_cancellation_propagates(self):
        self.search.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await lookup_candidates("Example")

    async def test_invalid_inputs_never_call_provider(self):
        for title in ("", "   ", None):
            with self.assertRaises(ValueError):
                await lookup_candidates(title)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                await lookup_candidates("Example", timeout_seconds=timeout)
        self.provider.assert_not_called()

    async def test_retryable_transport_status_and_retry_after(self):
        import aiohttp
        from enrichment_service.hltb import HLTBTemporaryError
        for error in (aiohttp.ClientConnectionError('offline'),
                      aiohttp.ClientResponseError(None, (), status=503),
                      aiohttp.ClientResponseError(None, (), status=429, headers={'Retry-After': '120'})):
            self.search.side_effect = error
            with self.assertRaises(HLTBTemporaryError) as raised:
                await lookup_candidates('Example')
            if isinstance(error, aiohttp.ClientResponseError) and error.status == 429:
                self.assertEqual(raised.exception.retry_after, 120)
        self.search.side_effect = aiohttp.ClientResponseError(None, (), status=403)
        with self.assertRaises(HLTBLookupError) as raised:
            await lookup_candidates('Example')
        self.assertNotIsInstance(raised.exception, HLTBTemporaryError)
