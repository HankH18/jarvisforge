"""Test doubles for backend/tests/cadence.

Named ``cadence_fakes`` rather than ``fakes``: test packages here have no
``__init__.py``, so sibling modules are imported by bare name off sys.path
and a second ``fakes.py`` would shadow ``tests/grounding/fakes.py``. Every
test package uses a distinct basename (``mocks``, ``memory_fakes``, ...).

A virtual clock so gate tests run in milliseconds instead of waiting on the
real 500ms threshold, plus recording stand-ins for Player/EventPublisher and
a clip factory so bank/gate tests never depend on the real committed assets
under backend/src/jarvis/cadence/assets/fillers/ (that is test_assets.py's
job alone).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.cadence.fillers import FillerClip


class VirtualClock:
    """Injectable Sleeper (``async def sleep(seconds) -> None``).

    Every call parks on an internal ``asyncio.Future`` that never resolves on
    its own, instead of touching the real clock. A test decides the outcome
    of each pending sleep explicitly:

    - call ``release()`` to resolve every currently-pending sleep, simulating
      "the threshold elapsed" (the FillerGate's timer fires).
    - never call it, and let the awaiting task be cancelled instead (the
      FillerGate cancels the timer when the guarded tool finishes first) --
      the sleep then raises CancelledError, exactly like real asyncio.sleep
      would under cancellation.

    No real wall-clock delay is ever incurred either way, so gate tests run
    in milliseconds while still exercising the real pinned 500ms threshold.
    """

    def __init__(self) -> None:
        self.requested: list[float] = []
        self._pending: list[asyncio.Future[None]] = []

    async def sleep(self, seconds: float) -> None:
        self.requested.append(seconds)
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending.append(fut)
        try:
            await fut
        finally:
            if fut in self._pending:
                self._pending.remove(fut)

    def release(self) -> None:
        """Resolve every sleep call currently pending."""
        for fut in list(self._pending):
            if not fut.done():
                fut.set_result(None)

    @property
    def pending(self) -> int:
        """Count of sleep calls still parked (not fired, not cancelled)."""
        return sum(1 for f in self._pending if not f.done())


class RecordingPlayer:
    """Async Player double: appends each played clip instead of speaking it."""

    def __init__(self) -> None:
        self.played: list[FillerClip] = []

    async def __call__(self, clip: FillerClip) -> None:
        self.played.append(clip)


class RecordingPublisher:
    """Test EventPublisher: records payloads instead of touching a room.

    Mirrors tests/pipeline/test_turn_loop.py's RecordingPublisher exactly.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


def make_clips(n: int, tmp_path: Path) -> list[FillerClip]:
    """``n`` FillerClip value objects pointing at throwaway files under
    ``tmp_path``, so bank/gate tests never depend on the real committed
    assets.
    """
    clips = []
    for i in range(n):
        path = tmp_path / f"clip-{i}.wav"
        path.write_bytes(b"\x00" * 16)  # gate/bank tests never read audio bytes
        clips.append(
            FillerClip(slug=f"clip-{i}", text=f"Clip {i}.", path=path, duration_ms=400 + i)
        )
    return clips
