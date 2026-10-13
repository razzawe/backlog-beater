import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "enrichment-service"))

from enrichment_service.hltb import HLTBCandidate
from enrichment_service.matching import MatchStatus, match_candidates, normalize_title


def candidate(name="Example", **changes):
    return replace(HLTBCandidate(1, name, None, "game", ("PC",), None, None, None, None), **changes)


class HLTBMatchingTests(unittest.TestCase):
    def test_case_whitespace_and_trademarks(self):
        game = candidate("Portal 2")
        result = match_candidates("  PORTAL®\t2™  ", [game])
        self.assertEqual(result.status, MatchStatus.MATCHED)
        self.assertEqual(result.candidate, game)
        self.assertEqual(result.reason, "unique_exact_title")

    def test_preserves_meaningful_title_differences(self):
        for steam, hltb in [
            ("Portal 2", "Portal"), ("Example II", "Example 2"),
            ("Example Remastered", "Example"), ("Example Remake", "Example"),
            ("Example: Chapter Two", "Example"),
            ("Example Deluxe Edition", "Example"),
            ("Example - Soundtrack", "Example"),
            ("Example", "Example!"),
        ]:
            with self.subTest(steam=steam, hltb=hltb):
                result = match_candidates(steam, [candidate(hltb)])
                self.assertEqual(result.status, MatchStatus.NO_MATCH)
                self.assertIsNone(result.candidate)

    def test_unicode_equivalent_accents(self):
        self.assertEqual(normalize_title("Pokémon"), normalize_title("Poke\u0301mon"))
        self.assertNotEqual(normalize_title("Pokémon"), normalize_title("Pokemon"))

    def test_alias_does_not_authorize_a_match(self):
        result = match_candidates("Example", [candidate("Other title", alias="Example")])
        self.assertEqual(result.status, MatchStatus.NO_MATCH)

    def test_multiple_identities_require_review_even_with_year(self):
        games = [candidate(hltb_id=2, release_year=2020), candidate(release_year=1990)]
        result = match_candidates("Example", games, release_year=2020)
        self.assertEqual(result.status, MatchStatus.AMBIGUOUS)
        self.assertIsNone(result.candidate)
        self.assertEqual([c.hltb_id for c in result.candidates], [1, 2])
        self.assertEqual(result, match_candidates("Example", reversed(games), release_year=2020))

    def test_identical_duplicate_rows_are_one_identity(self):
        game = candidate()
        self.assertEqual(match_candidates("Example", [game, game]).status, MatchStatus.MATCHED)

    def test_conflicting_rows_for_same_id_require_review(self):
        result = match_candidates("Example", [candidate(), candidate(main_story_hours=10)])
        self.assertEqual(result.status, MatchStatus.AMBIGUOUS)

    def test_metadata_conflicts_require_review(self):
        for game, reason in [
            (candidate(platforms=("PlayStation 5",)), "platform_conflict"),
            (candidate(release_year=1990), "release_year_conflict"),
            (candidate(game_type="dlc"), "entry_type_requires_review"),
        ]:
            with self.subTest(reason=reason):
                result = match_candidates("Example", [game], release_year=2020)
                self.assertEqual(result.status, MatchStatus.AMBIGUOUS)
                self.assertEqual(result.reason, reason)
                self.assertIsNone(result.candidate)

    def test_missing_metadata_does_not_conflict(self):
        game = candidate(platforms=(), game_type=None)
        self.assertEqual(match_candidates("Example", [game], release_year=2020).status, MatchStatus.MATCHED)

    def test_matching_metadata(self):
        game = candidate(platforms=("pc", "PlayStation 5"), release_year=2020)
        self.assertEqual(match_candidates("Example", [game], release_year=2020).status, MatchStatus.MATCHED)

    def test_unrelated_candidates_do_not_prevent_unique_match(self):
        result = match_candidates("Example", [candidate("Other", hltb_id=2), candidate()])
        self.assertEqual(result.status, MatchStatus.MATCHED)

    def test_no_results(self):
        result = match_candidates("Example", [])
        self.assertEqual(result.status, MatchStatus.NO_MATCH)
        self.assertEqual(result.candidates, ())

    def test_invalid_inputs(self):
        for title in (None, "", "  ", "™®"):
            with self.assertRaises(ValueError):
                match_candidates(title, [])
        for year in (0, -1, 10000, True, "2020"):
            with self.assertRaises(ValueError):
                match_candidates("Example", [], release_year=year)
        with self.assertRaises(ValueError):
            match_candidates("Example", [], platform=" ")
