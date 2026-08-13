"""Per-turn latency JSONL logging (docs/DESIGN.md §Data models).

One JSON object per line: {turn_id, turn_end_ts, first_audio_ts, tool_ms,
model}. `scripts/latency_report.py` (T-9) reads this file to print p50/p95.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LATENCY_LOG_PATH = Path("var/latency.jsonl")


@dataclass
class LatencyLogger:
    """Tracks one turn at a time: `turn_ended()` opens it, `first_audio()`
    closes it and appends the JSONL record. T-1 has no tools, so `tool_ms` is
    always 0 here -- T-2+ tool timing plugs into the same `first_audio()` call.
    """

    path: Path = DEFAULT_LATENCY_LOG_PATH
    model: str = "unknown"
    _next_turn_id: int = field(default=1, init=False)
    _pending_turn_id: int | None = field(default=None, init=False)
    _turn_end_ts: float | None = field(default=None, init=False)

    def turn_ended(self, *, ts: float | None = None) -> None:
        """Call when the user's turn ends and the agent begins thinking."""
        self._pending_turn_id = self._next_turn_id
        self._next_turn_id += 1
        self._turn_end_ts = ts if ts is not None else time.time()

    def first_audio(self, *, ts: float | None = None, tool_ms: float = 0.0) -> dict | None:
        """Call when the agent's reply begins speaking; appends one JSONL line.

        Returns the record written, or None if there was no open turn (e.g. a
        "speaking" transition with no preceding "thinking" -- shouldn't
        happen, but this stays a no-op rather than emitting a bogus record).
        """
        if self._pending_turn_id is None or self._turn_end_ts is None:
            return None

        record = {
            "turn_id": self._pending_turn_id,
            "turn_end_ts": self._turn_end_ts,
            "first_audio_ts": ts if ts is not None else time.time(),
            "tool_ms": tool_ms,
            "model": self.model,
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        self._pending_turn_id = None
        self._turn_end_ts = None
        return record
