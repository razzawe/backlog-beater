import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from sync_service.steam import get_owned_games


class SteamRetryTests(unittest.IsolatedAsyncioTestCase):
    def response(self, status=200, payload=None):
        return httpx.Response(status, json=payload or {"response": {"game_count": 0}},
                              request=httpx.Request("GET", "https://api.steampowered.com"))

    async def fetch(self, outcomes):
        with patch("sync_service.steam.STEAM_API_KEY", "test-key"), \
                patch("sync_service.steam.httpx.AsyncClient") as client, \
                patch("sync_service.steam.asyncio.sleep", new_callable=AsyncMock) as sleep:
            get = client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=outcomes)
            try:
                result = await get_owned_games("76561198000000000")
            except HTTPException as error:
                result = error
            return result, get.await_count, [call.args[0] for call in sleep.await_args_list]

    async def test_transient_failures_then_success(self):
        result, attempts, delays = await self.fetch([
            httpx.ConnectError("connection failed"), self.response(503),
            self.response(429), self.response(),
        ])
        self.assertEqual(result.game_count, 0)
        self.assertEqual(attempts, 4)
        self.assertEqual(delays, [1, 2, 4])

    async def test_exhausted_retries(self):
        for failure, status in [(httpx.ReadTimeout("timeout"), 504),
                                (httpx.ConnectError("connection failed"), 502),
                                (self.response(503), 502)]:
            result, attempts, delays = await self.fetch([failure] * 4)
            self.assertEqual(result.status_code, status)
            self.assertEqual(attempts, 4)
            self.assertEqual(delays, [1, 2, 4])

    async def test_permanent_errors_are_not_retried(self):
        for response in [self.response(401), self.response(403), self.response(404),
                         self.response(payload={"response": {"game_count": 1}})]:
            result, attempts, delays = await self.fetch([response])
            self.assertEqual(result.status_code, 502)
            self.assertEqual(attempts, 1)
            self.assertEqual(delays, [])

    async def test_missing_key_never_calls_steam(self):
        with patch("sync_service.steam.STEAM_API_KEY", ""), \
                patch("sync_service.steam.httpx.AsyncClient") as client:
            with self.assertRaises(HTTPException) as caught:
                await get_owned_games("76561198000000000")
            self.assertEqual(caught.exception.status_code, 503)
            client.assert_not_called()

    async def test_overall_deadline_cancels_slow_request(self):
        real_timeout = asyncio.timeout
        cancelled = asyncio.Event()

        async def slow_request(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("sync_service.steam.STEAM_API_KEY", "test-key"), \
                patch("sync_service.steam.httpx.AsyncClient") as client, \
                patch("sync_service.steam.asyncio.timeout", side_effect=lambda _: real_timeout(0.01)):
            client.return_value.__aenter__.return_value.get = slow_request
            with self.assertRaises(HTTPException) as caught:
                await get_owned_games("76561198000000000")
            self.assertEqual(caught.exception.status_code, 504)
            self.assertTrue(cancelled.is_set())
