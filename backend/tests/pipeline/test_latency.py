"""T-16: LatencyLogger's tool_ms/model plumbing.

Before this ticket, `first_audio()` always wrote `tool_ms=0.0` -- its only
caller (`session_wiring._on_agent_state_changed`) never passed a value, and
there was no way for a tool dispatch to hand one in -- and `model` was always
whatever `LatencyLogger.model` was set to at construction (the session's
always-on CONVO_MODEL), with no per-turn override even when a `heavy` tool
escalated that turn to $TOOL_MODEL. `note_tool_span()` is the fix: it lets a
caller (`tools_wiring._dispatch`) record the real measured span + the
actually-routed model for the turn currently open, and `first_audio()` reports
that instead of the old hardcoded defaults. Every test below FAILS against
the pre-T-16 `LatencyLogger` (`note_tool_span` does not exist at all).
"""

from __future__ import annotations

import json

from jarvis.pipeline.latency import LatencyLogger


def _read_records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_first_audio_defaults_tool_ms_zero_and_session_model_with_no_tool_span(tmp_path):
    """No-lookup-turn baseline: nothing recorded a tool span, so the honest
    defaults (0.0 / the session's always-on model) still apply."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=1000.0)
    record = latency.first_audio(ts=1000.5)

    assert record["tool_ms"] == 0.0
    assert record["model"] == "claude-convo-test"


def test_first_audio_reports_the_recorded_tool_span_and_model(tmp_path):
    """Acceptance #1, core case: a tool fired this turn (note_tool_span was
    called) -- first_audio() must report the REAL measured tool_ms and the
    ACTUAL model that answered, not the always-0.0/always-CONVO_MODEL
    defaults. Fails against pre-T-16 LatencyLogger: no note_tool_span method
    exists to call."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=2000.0)
    latency.note_tool_span(tool_ms=1234.5, model="claude-tool-test")
    record = latency.first_audio(ts=2003.0)

    assert record["tool_ms"] == 1234.5
    assert record["model"] == "claude-tool-test"

    # and the JSONL line on disk carries the same values, not just the
    # in-memory return value.
    (written,) = _read_records(tmp_path / "latency.jsonl")
    assert written["tool_ms"] == 1234.5
    assert written["model"] == "claude-tool-test"


def test_pending_tool_span_does_not_leak_into_the_next_turn(tmp_path):
    """A tool-firing turn must not contaminate the NEXT turn's record if that
    next turn fires no tool -- turn_ended() has to clear the previous turn's
    note_tool_span() before the new turn can record anything of its own."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=3000.0)
    latency.note_tool_span(tool_ms=999.0, model="claude-tool-test")
    latency.first_audio(ts=3001.0)

    # turn 2: no tool fires.
    latency.turn_ended(ts=3010.0)
    record = latency.first_audio(ts=3010.4)

    assert record["tool_ms"] == 0.0
    assert record["model"] == "claude-convo-test"


def test_note_tool_span_is_a_noop_without_an_open_turn(tmp_path):
    """Defensive: note_tool_span() before any turn_ended() (or after the
    matching first_audio() already closed it) must not raise, and must not
    fabricate a turn out of nothing."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.note_tool_span(tool_ms=42.0, model="claude-tool-test")  # no turn open

    assert latency.first_audio(ts=4000.0) is None
    assert not (tmp_path / "latency.jsonl").exists()


def test_explicit_first_audio_arguments_still_override(tmp_path):
    """Backward-compat: first_audio()'s own tool_ms/model kwargs still win
    over whatever note_tool_span() recorded, for callers that want to be
    fully explicit."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=5000.0)
    latency.note_tool_span(tool_ms=1.0, model="claude-tool-test")
    record = latency.first_audio(ts=5000.2, tool_ms=77.0, model="explicit-override")

    assert record["tool_ms"] == 77.0
    assert record["model"] == "explicit-override"


# ---- T-16 follow-up: a filler's premature "speaking" transition must not
# finalize the turn before the tool's real span/model are known (verifier-
# reported live against the real FillerGate; see test_tool_wiring.py for the
# end-to-end reproduction). These are the LatencyLogger-level unit tests for
# the same fix. ------------------------------------------------------------


def test_a_speaking_transition_during_a_pending_tool_dispatch_does_not_finalize(tmp_path):
    """The core race fix: once note_tool_dispatch_started() marks a tool as
    in flight, a "speaking" transition that arrives before the matching
    note_tool_span() (i.e. a filler acknowledgment) must NOT write a record
    with the pre-tool defaults -- it must decline, leaving the turn open."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=6000.0)
    latency.note_tool_dispatch_started()
    # a filler speaks while the tool is still running
    record = latency.first_audio(ts=6000.5)

    assert record is None
    assert not (tmp_path / "latency.jsonl").exists(), "must not write with pre-tool defaults"


def test_tool_span_known_after_a_filler_finalizes_with_the_fillers_own_timestamp(tmp_path):
    """Once the tool's real span/model ARE known, if a filler already spoke
    for this turn (first_audio() deferred and remembered its timestamp), the
    record must be written right then -- using the filler's genuine
    first-audio timestamp (it WAS the first audible response, R2) together
    with the real tool_ms/model. This is the exact scenario the verifier
    reproduced live: a heavy tool slow enough to trigger the real filler
    must still end up with a correct record, not a lost one and not one
    frozen at the filler's pre-tool defaults."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=7000.0)
    latency.note_tool_dispatch_started()
    deferred = latency.first_audio(ts=7000.5)  # filler speaks; tool still running
    assert deferred is None

    # tool + synthesis finish ~900ms after turn_end, well after the filler
    record = latency.note_tool_span(tool_ms=917.3, model="claude-tool-test")

    assert record is not None
    assert record["first_audio_ts"] == 7000.5, "must use the filler's real timestamp, not now()"
    assert record["tool_ms"] == 917.3
    assert record["model"] == "claude-tool-test"

    (written,) = _read_records(tmp_path / "latency.jsonl")
    assert written == record

    # the real answer's own (possible) later "speaking" transition must be a
    # harmless no-op against the now-closed turn, not a second record.
    assert latency.first_audio(ts=7001.0) is None
    assert len(_read_records(tmp_path / "latency.jsonl")) == 1


def test_no_filler_fast_tool_finalizes_normally_on_its_own_speaking_transition(tmp_path):
    """When the tool finishes before any filler ever speaks (the common
    case: fast tools, non-heavy tools, or a real dispatch that simply never
    triggers the gate), nothing is deferred -- the turn's own "speaking"
    transition finalizes it exactly as before the filler-race fix."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    latency.turn_ended(ts=8000.0)
    latency.note_tool_dispatch_started()
    latency.note_tool_span(tool_ms=120.0, model="claude-convo-test")  # fast, no filler
    record = latency.first_audio(ts=8000.15)

    assert record is not None
    assert record["tool_ms"] == 120.0
    assert record["model"] == "claude-convo-test"


def test_dispatch_pending_and_deferred_timestamp_do_not_leak_across_turns(tmp_path):
    """turn_ended() must reset the new dispatch-pending/deferred-timestamp
    state too, exactly like it already resets tool_ms/model -- a fresh turn
    must never open already "blocked" by a stale flag from a previous,
    unusually-shaped turn."""
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="claude-convo-test")

    # turn 1: dispatch started but never resolved before the turn somehow
    # closes some other way (defensive scenario -- shouldn't happen on the
    # real ordering, but must not poison the next turn if it did).
    latency.turn_ended(ts=9000.0)
    latency.note_tool_dispatch_started()

    # turn 2: no tool at all -- must finalize immediately on its own
    # "speaking" transition, not be silently blocked forever.
    latency.turn_ended(ts=9010.0)
    record = latency.first_audio(ts=9010.3)

    assert record is not None
    assert record["tool_ms"] == 0.0
    assert record["model"] == "claude-convo-test"
