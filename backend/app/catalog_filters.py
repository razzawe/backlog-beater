"""Shared visibility rules; raw Steam metadata stays unchanged."""
import re

_TEST_MARKERS = re.compile(r'\b(?:public\s+test(?:ing)?|test\s+server|playtest|open\s+beta|closed\s+beta)\b', re.I)


def is_test_build(name):
    # Explicit title markers only; a generic 'beta' or 'test' is insufficient.
    return isinstance(name, str) and bool(_TEST_MARKERS.search(name))


def effective_free_flag(is_free, genres):
    if isinstance(genres, list) and any(isinstance(g, dict) and g.get('id') == '37' for g in genres):
        return True
    return is_free
