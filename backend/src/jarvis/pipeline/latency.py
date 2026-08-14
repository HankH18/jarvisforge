"""Per-turn latency JSONL logging (docs/DESIGN.md §Data models).

One JSON object per line: {turn_id, turn_end_ts, first_audio_ts, tool_ms,
model} -- this exact 5-field shape is pinned in docs/DESIGN.md §Data models;
do not add fields here (docs/DESIGN.md is outside this ticket's file scope,
so a schema change could not be documented alongside it -- see
`scripts/latency_report.py` for how lookup/no-lookup classification is
handled instead, without touching this schema). `scripts/latency_report.py`
(T-9) reads this file to print p50/p95.
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
    closes it and appends the JSONL record.

    T-16: `tool_ms`/`model` used to be dead weight -- `first_audio()` always
    wrote `tool_ms=0.0` (its caller, `session_wiring._on_agent_state_changed`,
    never passed a value) and `model` was whatever `session.llm.model` (the
    always-on CONVO_MODEL) was at construction time, even on a turn that
    escalated to $TOOL_MODEL via a `heavy` tool. `note_tool_span()` below is
    the fix: `tools_wiring._dispatch` calls it with the real measured
    handler+synthesis time and the actually-routed model once both complete;
    `first_audio()` then reports that instead of the always-0.0/always-
    CONVO_MODEL defaults, and only for a genuine no-tool turn (nothing
    recorded) do the honest defaults still apply.

    Second bug, found live by an independent verifier reproducing it against
    the REAL FillerGate (not caught by the first pass -- neither existing
    integration test wired one): a `heavy` tool call slow enough to trigger
    T-15's spoken filler acknowledgment speaks it via the same
    `AgentSession.say()` mechanism the real answer uses, which fires a
    "speaking" transition BEFORE the tool (by definition still running --
    that is WHY the filler fired) finishes and `note_tool_span()` runs.
    `github_query`/`load_repo_digest` are the only two `heavy` tools (the
    only ones that ever escalate `model` at all), and the real `github_query`
    handler+synthesis span is measured at ~350ms+~2.7s (see
    `tools_wiring._dispatch`'s docstring) -- routinely past the 500ms
    threshold -- so this is not a rare corner case; it defeats the
    `model`/`tool_ms` fix for essentially every escalated turn. This DOES
    threaten R1 (acceptance #1 is not scoped to no-lookup turns): a
    misrecorded lookup turn looks, from the JSONL alone, exactly like a fast
    no-lookup turn, which is precisely the population R1's bar is measured
    over.

    A first attempt at fixing this simply deferred `first_audio()` while a
    tool dispatch was in flight, expecting a LATER "speaking" transition (the
    real answer's own) to finalize the turn once the tool was done. Verified
    live against the real FillerGate that this is wrong: `agent_state_changed`
    fires on a state CHANGE, not once per `session.say()` call -- once the
    filler's own `say()` has already put the session into "speaking", the
    real answer's later `say()` (queued while still "speaking", since
    `FillerGate.guard()`'s player is fire-and-forget and does not await
    playback completion) does not reliably produce a SECOND "speaking"
    transition to catch. Deferring unconditionally lost the record entirely
    (no JSONL line at all) whenever a filler fired, rather than merely
    mis-attributing it.

    Actual fix: a filler's "speaking" transition genuinely is the first
    audible response for that turn (a spoken acknowledgment, R2) -- its
    timestamp is real and correct as `first_audio_ts`, it is only the
    `tool_ms`/`model` that aren't known yet. So `first_audio()`, on a
    "speaking" transition that arrives while a tool dispatch is still
    pending (`note_tool_dispatch_started()` was called and `note_tool_span()`
    hasn't run yet), remembers that timestamp (`_deferred_first_audio_ts`)
    instead of writing a record, and declines to finalize. `note_tool_span()`
    -- called once the real span and model ARE known -- checks for that
    remembered timestamp and, if present, finalizes and writes the record
    itself right then, using it. If the SDK does still emit a second
    "speaking" transition later (harmless either way), `first_audio()` finds
    the turn already closed and no-ops, matching its existing "no open turn"
    contract. If the tool finishes before any filler ever speaks (the common
    case -- fast tools, non-`heavy` tools, or genuine no-tool turns), nothing
    is deferred and `first_audio()`'s own later "speaking" transition writes
    the record exactly as before. Verified against a live reproduction of
    the exact reported scenario: the real JarvisAgent + the real
    `cadence_wiring.build_filler_gate` (production wiring, real 500ms
    `FILLER_THRESHOLD_MS`) + the real `github_query` ToolSpec with its HTTP
    call delayed ~900ms -- see `backend/tests/pipeline/test_tool_wiring.py`.

    This ticket deliberately does NOT add a `lookup`/"tool fired" field to
    the JSONL record to make lookup status machine-readable: the record
    shape is pinned in docs/DESIGN.md §Data models, which is outside this
    ticket's file scope, so a schema change could not be reflected in the
    docs that define it. `scripts/latency_report.py` instead treats its
    `tool_ms`-inferred lookup/no-lookup split as diagnostic-only (never an
    R1 PASS/FAIL verdict) whenever it has no independent ground truth
    (`--driver-json`) to confirm the inference against -- see that script.
    """

    path: Path = DEFAULT_LATENCY_LOG_PATH
    model: str = "unknown"
    _next_turn_id: int = field(default=1, init=False)
    _pending_turn_id: int | None = field(default=None, init=False)
    _turn_end_ts: float | None = field(default=None, init=False)
    _pending_tool_ms: float = field(default=0.0, init=False)
    _pending_model: str | None = field(default=None, init=False)
    _tool_dispatch_pending: bool = field(default=False, init=False)
    _deferred_first_audio_ts: float | None = field(default=None, init=False)

    def turn_ended(self, *, ts: float | None = None) -> None:
        """Call when the user's turn ends and the agent begins thinking."""
        self._pending_turn_id = self._next_turn_id
        self._next_turn_id += 1
        self._turn_end_ts = ts if ts is not None else time.time()
        # A fresh turn starts with no tool span recorded, no dispatch in
        # flight, and no deferred timestamp -- clears whatever a PREVIOUS
        # turn left set, so a no-tool turn right after a tool turn never
        # inherits stale state, and a turn never opens already "blocked" by
        # leftover flags (T-16).
        self._pending_tool_ms = 0.0
        self._pending_model = None
        self._tool_dispatch_pending = False
        self._deferred_first_audio_ts = None

    def note_tool_dispatch_started(self) -> None:
        """Mark that a tool dispatch is now in flight for the currently open
        turn (T-16). Call this the moment `tools_wiring._dispatch` begins --
        before the handler runs -- so that a "speaking" transition which
        happens before the matching `note_tool_span()` (a filler
        acknowledgment, T-15) is recognized as premature: `first_audio()`
        remembers its timestamp instead of finalizing the turn from it. A
        no-op if no turn is currently open (defensive).
        """
        if self._pending_turn_id is None:
            return
        self._tool_dispatch_pending = True

    def note_tool_span(self, *, tool_ms: float, model: str) -> dict | None:
        """Record the real measured tool-handler + grounded-synthesis time
        and the model that produced this turn's answer (T-16), and clear the
        dispatch-pending flag `first_audio()` checks. Called by
        `tools_wiring._dispatch` after both complete, before the answer is
        spoken.

        If a filler already spoke for this turn (`first_audio()` saw the
        dispatch still pending and deferred, remembering its timestamp),
        there is no reliable second "speaking" transition to finalize the
        turn later -- see the class docstring -- so this finalizes and
        writes the record itself, right now, using that remembered
        timestamp, and returns it. Otherwise this just records the span for
        the turn's own (still upcoming) "speaking" transition to pick up,
        exactly as before, and returns None (nothing written yet).

        A no-op (returns None) if no turn is currently open (defensive;
        should not happen on the real turn-loop ordering -- a tool only ever
        dispatches between a `turn_ended()` and the matching `first_audio()`).
        """
        if self._pending_turn_id is None:
            return None
        self._pending_tool_ms = tool_ms
        self._pending_model = model
        self._tool_dispatch_pending = False
        if self._deferred_first_audio_ts is not None:
            return self._write_record(ts=self._deferred_first_audio_ts)
        return None

    def first_audio(
        self,
        *,
        ts: float | None = None,
        tool_ms: float | None = None,
        model: str | None = None,
    ) -> dict | None:
        """Call when the agent's reply begins speaking; appends one JSONL line
        (or, if a tool dispatch is still in flight, remembers this moment as
        the turn's real first-audio timestamp and defers -- see the class
        docstring).

        `tool_ms`/`model` default to whatever `note_tool_span()` recorded for
        this turn (or the honest no-tool defaults -- 0.0 / `self.model`, the
        always-on conversational model -- if nothing was recorded), but can
        still be passed explicitly to override.

        Returns the record written, or None if there was no open turn (e.g. a
        "speaking" transition with no preceding "thinking", or one that
        arrives after the turn was already finalized by `note_tool_span()`'s
        own deferred write -- both shouldn't-happen-but-safe no-ops) OR if
        this call itself deferred (tool dispatch still in flight).
        """
        if self._pending_turn_id is None or self._turn_end_ts is None:
            return None
        if self._tool_dispatch_pending:
            if self._deferred_first_audio_ts is None:
                self._deferred_first_audio_ts = ts if ts is not None else time.time()
            return None

        return self._write_record(ts=ts, tool_ms=tool_ms, model=model)

    def _write_record(
        self,
        *,
        ts: float | None = None,
        tool_ms: float | None = None,
        model: str | None = None,
    ) -> dict:
        record = {
            "turn_id": self._pending_turn_id,
            "turn_end_ts": self._turn_end_ts,
            "first_audio_ts": ts if ts is not None else time.time(),
            "tool_ms": tool_ms if tool_ms is not None else self._pending_tool_ms,
            "model": model if model is not None else (self._pending_model or self.model),
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        self._pending_turn_id = None
        self._turn_end_ts = None
        self._pending_tool_ms = 0.0
        self._pending_model = None
        self._tool_dispatch_pending = False
        self._deferred_first_audio_ts = None
        return record
