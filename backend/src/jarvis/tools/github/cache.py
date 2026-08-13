"""In-memory (repo, head_sha) digest cache, 15-minute TTL.

docs/DESIGN.md §Interfaces pins ``load_repo_digest``'s caching as "cache key
(repo, head_sha), TTL 15 min". The clock is injected (default: real UTC
time) so tests can prove hit / changed-head-sha-miss / expired-TTL-miss
without sleeping 15 minutes -- see backend/tests/tools/test_github.py, which
either swaps this class's ``clock`` or replaces
``jarvis.tools.github.digest._DIGEST_CACHE`` with an instance built with a
fake clock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now(UTC)


class DigestCache[DigestT]:
    """Keyed by ``(repo, head_sha)``. An entry older than ``ttl_seconds``
    (per ``clock()``) is a miss and is evicted the next time it's looked up.
    """

    def __init__(self, ttl_seconds: int, clock: Clock = _real_clock) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._store: dict[tuple[str, str], tuple[datetime, DigestT]] = {}

    def get(self, repo: str, head_sha: str) -> DigestT | None:
        key = (repo, head_sha)
        entry = self._store.get(key)
        if entry is None:
            return None
        cached_at, digest = entry
        if self._clock() - cached_at > self._ttl:
            del self._store[key]
            return None
        return digest

    def set(self, repo: str, head_sha: str, digest: DigestT) -> None:
        self._store[(repo, head_sha)] = (self._clock(), digest)

    def __len__(self) -> int:
        return len(self._store)
