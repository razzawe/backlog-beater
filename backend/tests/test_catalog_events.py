"""Producer contract tests without a live broker or consumer imports."""

import unittest
from unittest.mock import patch

from sync_service.events import publish_games_imported


class CatalogEventTests(unittest.TestCase):
    def test_publishes_one_versioned_event_with_unique_steam_ids(self):
        with patch("sync_service.events.client.connection_for_write"), \
             patch("sync_service.events.client.send_task") as send:
            result = publish_games_imported([620, 570, 620])
        self.assertTrue(result.submitted)
        send.assert_called_once()
        self.assertEqual(send.call_args.args, ("catalog.games_imported",))
        self.assertEqual(send.call_args.kwargs["kwargs"],
                         {"schema_version": 1, "steam_app_ids": [570, 620]})
        self.assertFalse(send.call_args.kwargs["retry"])

    def test_empty_import_does_not_publish(self):
        with patch("sync_service.events.client.connection_for_write") as connect:
            self.assertFalse(publish_games_imported([]).submitted)
        connect.assert_not_called()

    def test_unavailable_broker_returns_nonfatal_submission_result(self):
        with patch("sync_service.events.client.connection_for_write", side_effect=RuntimeError("offline")), \
             self.assertLogs("sync_service.events", level="ERROR"):
            result = publish_games_imported([570])
        self.assertFalse(result.submitted)
        self.assertTrue(result.unavailable)
