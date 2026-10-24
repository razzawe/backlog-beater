"""Conservative matching of Steam titles to already-fetched HLTB candidates."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from .hltb import HLTBCandidate, normalize_search_title


class MatchStatus(str, Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    candidate: HLTBCandidate | None
    reason: str
    # Exact-title candidates retained for future review, in stable ID order.
    candidates: tuple[HLTBCandidate, ...]


def normalize_title(title: str) -> str:
    """Ignore case, trademarks and whitespace, preserving meaningful wording.

    Punctuation, numbers, accents, subtitles and edition labels are preserved.
    NFC handles equivalent Unicode spellings without collapsing Roman numerals.
    """
    return normalize_search_title(title).casefold()


def match_candidates(
    title: str,
    candidates: Iterable[HLTBCandidate],
    *,
    platform: str | None = "PC",
    release_year: int | None = None,
) -> MatchResult:
    """Accept one distinct exact-title result with no known metadata conflict.

    Unknown metadata does not count as a conflict. Release-year disagreement
    requires review rather than rejection: Steam may describe a later PC port.
    Multiple exact identities remain ambiguous even if metadata favors one.
    Aliases and fuzzy similarities never independently authorize a match.
    """
    normalized = normalize_title(title)
    if platform is not None and (not isinstance(platform, str) or not platform.strip()):
        raise ValueError("Platform must be nonempty or None")
    if release_year is not None and (type(release_year) is not int or not 1 <= release_year <= 9999):
        raise ValueError("Release year must be between 1 and 9999")
    exact = set(candidate for candidate in candidates if normalize_title(candidate.name) == normalized)
    ordered = tuple(sorted(exact, key=lambda c: (c.hltb_id, repr(c))))
    if not ordered:
        return MatchResult(MatchStatus.NO_MATCH, None, "no_exact_title", ())
    if len(ordered) > 1:
        # Identical repeated rows are deduplicated; conflicting rows for the
        # same HLTB ID still need review instead of depending on response order.
        return MatchResult(MatchStatus.AMBIGUOUS, None, "multiple_exact_candidates", ordered)
    candidate = ordered[0]
    if candidate.game_type and candidate.game_type.strip().casefold() != "game":
        return MatchResult(MatchStatus.AMBIGUOUS, None, "entry_type_requires_review", ordered)
    if platform and candidate.platforms and platform.strip().casefold() not in {
        value.strip().casefold() for value in candidate.platforms
    }:
        return MatchResult(MatchStatus.AMBIGUOUS, None, "platform_conflict", ordered)
    if release_year is not None and candidate.release_year is not None and release_year != candidate.release_year:
        return MatchResult(MatchStatus.AMBIGUOUS, None, "release_year_conflict", ordered)
    return MatchResult(MatchStatus.MATCHED, candidate, "unique_exact_title", ordered)


def review_match_candidates(title: str, candidates: Iterable[HLTBCandidate]) -> MatchResult:
    """Rank plausible suggestions; similarity never authorizes automatic matches.

    Require 0.65 title/alias similarity, reward PC metadata, and retain at most
    three distinct IDs. More than one plausible identity requires user review,
    including near-title originals/remakes even if one name is an exact match.
    """
    from difflib import SequenceMatcher
    import re

    def search_title(value):
        return " ".join(re.sub(r"[^\w\s]", " ", normalize_title(value)).split())

    query = search_title(title)
    candidates = tuple(candidates)
    exact_base = match_candidates(title, candidates)
    if (exact_base.status == MatchStatus.MATCHED
            and (exact_base.candidate.game_type or "").strip().casefold() == "game"):
        # Only explicit add-on metadata can stop competing with an exact base
        # game. Unknown types, editions and remakes still require review.
        candidates = tuple(c for c in candidates
            if (c.game_type or "").strip().casefold() not in {"dlc", "expansion"}
            or normalize_title(c.name) == normalize_title(title))
    ranked = {}
    for candidate in candidates:
        names = [candidate.name] + ([candidate.alias] if candidate.alias and candidate.alias.strip() else [])
        similarity = max(SequenceMatcher(None, query, search_title(name)).ratio() for name in names)
        if similarity < 0.65:
            continue
        pc = any(platform.casefold() == "pc" for platform in candidate.platforms)
        score = similarity + (0.03 if pc else 0)
        previous = ranked.get(candidate.hltb_id)
        item = (score, candidate)
        if previous is None or (score, repr(candidate)) > (previous[0], repr(previous[1])):
            ranked[candidate.hltb_id] = item
    ordered = tuple(item[1] for item in sorted(ranked.values(),
        key=lambda item: (-item[0], item[1].hltb_id))[:3])
    if not ordered:
        return MatchResult(MatchStatus.NO_MATCH, None, "no_plausible_candidates", ())
    exact = match_candidates(title, ordered)
    if len(ranked) == 1 and exact.status == MatchStatus.MATCHED:
        return exact
    reason = exact.reason if exact.status == MatchStatus.AMBIGUOUS else "similar_title_candidates"
    return MatchResult(MatchStatus.AMBIGUOUS, None, reason, ordered)
