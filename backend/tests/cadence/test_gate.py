"""FillerGate: speaks at most one filler when a heavy tool call outlives
FILLER_THRESHOLD_MS. Uses cadence_fakes.VirtualClock so these run in milliseconds
against the real pinned 500ms threshold -- no real sleep, ever.
"""

from __future__ import annotations

import asyncio

import pytest
from cadence_fakes import RecordingPlayer, RecordingPublisher, VirtualClock, make_clips

from jarvis.cadence.fillers import FillerBank
from jarvis.cadence.gate import FillerDecision, FillerGate, decide
from jarvis.cadence.policy import FILLER_THRESHOLD_MS
from jarvis.pipeline.events import agent_state_event


class _Boom(Exception):
    """Marker exception for propagation tests."""


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    """Pump the event loop until ``predicate()`` is true, with no real delay
    -- lets the FillerGate's internal timer task run and register its
    sleep() call before the test drives the next step."""

    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout=timeout)


def _new_gate(tmp_path, *, publisher=None):
    bank = FillerBank(make_clips(3, tmp_path))
    player = RecordingPlayer()
    clock = VirtualClock()
    gate = FillerGate(bank, player, sleep=clock.sleep, publisher=publisher)
    return gate, player, clock


# ---- pure decide() ----------------------------------------------------------


def test_decide_at_the_exact_threshold_boundary_does_not_play():
    decision = decide(heavy=True, elapsed_ms=500.0, threshold_ms=FILLER_THRESHOLD_MS)
    assert decision == FillerDecision(play=False, reason="tool_faster_than_threshold")


def test_decide_just_past_the_threshold_boundary_plays():
    decision = decide(heavy=True, elapsed_ms=500.1, threshold_ms=FILLER_THRESHOLD_MS)
    assert decision == FillerDecision(play=True, reason="heavy_tool_exceeded_threshold")


def test_decide_non_heavy_never_plays_regardless_of_elapsed():
    decision = decide(heavy=False, elapsed_ms=10_000, threshold_ms=FILLER_THRESHOLD_MS)
    assert decision == FillerDecision(play=False, reason="tool_not_heavy")


def test_decide_uses_the_pinned_default_threshold():
    # no threshold_ms passed -> defaults to FILLER_THRESHOLD_MS (500)
    assert decide(heavy=True, elapsed_ms=499.9).play is False
    assert decide(heavy=True, elapsed_ms=500.1).play is True


# ---- FillerGate.guard() ------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_heavy_tool_triggers_exactly_one_filler(tmp_path):
    gate, player, clock = _new_gate(tmp_path)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)

    # Sleeper takes seconds; FILLER_THRESHOLD_MS is milliseconds.
    assert clock.requested == [FILLER_THRESHOLD_MS / 1000]

    clock.release()  # simulate the threshold elapsing
    await _wait_until(lambda: player.played)

    tool.set_result("tool-return-value")
    result = await task

    assert result == "tool-return-value"
    assert len(player.played) == 1
    assert gate.played == player.played


@pytest.mark.asyncio
async def test_fast_heavy_tool_triggers_zero_fillers(tmp_path):
    gate, player, clock = _new_gate(tmp_path)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)

    tool.set_result("fast-return-value")  # never call clock.release()
    result = await task

    assert result == "fast-return-value"
    assert player.played == []
    assert gate.played == []
    await _wait_until(lambda: clock.pending == 0)


@pytest.mark.asyncio
async def test_slow_non_heavy_tool_triggers_zero_fillers(tmp_path):
    gate, player, clock = _new_gate(tmp_path)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=False))
    await asyncio.sleep(0)  # let guard() run past the non-heavy branch
    tool.set_result("value")
    result = await task

    assert result == "value"
    assert clock.requested == []  # no timer ever armed for a non-heavy tool
    assert player.played == []
    assert gate.played == []


@pytest.mark.asyncio
async def test_exception_from_the_tool_propagates_and_leaks_no_timer_task(tmp_path):
    gate, player, clock = _new_gate(tmp_path)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)

    tool.set_exception(_Boom("kaboom"))
    with pytest.raises(_Boom):
        await task

    await _wait_until(lambda: clock.pending == 0)
    leftover = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert not leftover, leftover
    assert player.played == []


@pytest.mark.asyncio
async def test_cancelling_guard_itself_does_not_leak_the_timer_task(tmp_path):
    gate, player, clock = _new_gate(tmp_path)
    tool = asyncio.get_running_loop().create_future()  # never resolved

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _wait_until(lambda: clock.pending == 0)
    leftover = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert not leftover, leftover
    assert player.played == []


@pytest.mark.asyncio
async def test_at_most_one_filler_per_guard_call_across_repeated_calls(tmp_path):
    gate, player, clock = _new_gate(tmp_path)

    for i in range(3):
        tool = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(gate.guard(tool, heavy=True))
        await _wait_until(lambda expected=i + 1: len(clock.requested) == expected)
        clock.release()
        await _wait_until(lambda expected=i + 1: len(player.played) == expected)
        tool.set_result(i)
        await task

    assert len(player.played) == 3
    assert gate.played == player.played


@pytest.mark.asyncio
async def test_publisher_receives_working_state_only_when_a_filler_plays(tmp_path):
    publisher = RecordingPublisher()
    gate, player, clock = _new_gate(tmp_path, publisher=publisher)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)
    clock.release()
    await _wait_until(lambda: player.played)
    tool.set_result(None)
    await task

    assert len(publisher.events) == 1
    event = publisher.events[0]
    assert event["type"] == "agent_state"
    assert event["state"] == "working"
    # shape matches the pinned agent_state_event helper (ts aside)
    expected = agent_state_event("working", ts=event["ts"])
    assert event == expected


@pytest.mark.asyncio
async def test_publisher_receives_nothing_when_no_filler_plays(tmp_path):
    publisher = RecordingPublisher()
    gate, player, clock = _new_gate(tmp_path, publisher=publisher)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)
    tool.set_result(None)  # fast tool -- never release the clock
    await task

    assert publisher.events == []


@pytest.mark.asyncio
async def test_no_publisher_supplied_does_not_break_filler_playback(tmp_path):
    # publisher is optional (defaults to None); a filler must still play.
    gate, player, clock = _new_gate(tmp_path, publisher=None)
    tool = asyncio.get_running_loop().create_future()

    task = asyncio.create_task(gate.guard(tool, heavy=True))
    await _wait_until(lambda: clock.requested)
    clock.release()
    await _wait_until(lambda: player.played)
    tool.set_result(None)
    await task

    assert len(player.played) == 1
