"""Cadence policy values and stop-phrase/utterance normalization (T-6).

Pinned in docs/DESIGN.md §Interfaces -- do not change the numbers or the
phrase tuple.
"""

from __future__ import annotations

import re
from typing import Final

FILLER_THRESHOLD_MS: Final[int] = 500
FILLER_START_DEADLINE_MS: Final[int] = 300
STOP_PHRASES: Final[tuple[str, ...]] = ("quiet jarvis", "jarvis quiet", "stop")

_DISCOURSE_MARKERS: Final[frozenset[str]] = frozenset({"hey", "ok", "okay", "um", "uh", "so"})
_NON_ALNUM_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


def normalize_utterance(text: str) -> str:
    """Lowercase, strip punctuation to spaces, collapse whitespace, drop
    leading discourse markers ('hey', 'ok', 'okay', 'um', 'uh', 'so')."""
    lowered = text.lower()
    collapsed = _NON_ALNUM_RE.sub(" ", lowered).strip()
    words = collapsed.split()
    while words and words[0] in _DISCOURSE_MARKERS:
        words.pop(0)
    return " ".join(words)


def is_stop_phrase(text: str) -> bool:
    """True when the normalized utterance is exactly one of STOP_PHRASES."""
    return normalize_utterance(text) in STOP_PHRASES
