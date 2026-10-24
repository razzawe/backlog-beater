"""Title cleanup shared by provider queries and candidate matching."""

import unicodedata


def normalize_search_title(title: str) -> str:
    """Remove trademarks and normalize Unicode/spacing without losing wording."""
    if not isinstance(title, str):
        raise ValueError("A nonempty game title is required")
    title = unicodedata.normalize("NFC", title).translate(str.maketrans("", "", "™®©"))
    normalized = " ".join(title.split())
    if not normalized:
        raise ValueError("A nonempty game title is required")
    return normalized
