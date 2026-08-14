"""DataChannelPublisher: the production EventPublisher (docs/DESIGN.md §Interfaces).

test_turn_loop.py only ever exercises RecordingPublisher, a test-only stub --
it proves the turn-loop wiring calls *some* publisher correctly, but never
proves the pinned wire format survives the real publish path. This file
closes that gap: a mocked `rtc.Room` stands in for the network, and we assert
on the literal bytes handed to `publish_data`, decoded back to JSON.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from jarvis.pipeline.events import DataChannelPublisher, agent_state_event, transcript_event


def test_agent_state_event_reaches_publish_data_in_the_pinned_shape():
    room = MagicMock()
    publisher = DataChannelPublisher(room)

    publisher.publish(agent_state_event("thinking", ts=123.5))

    room.local_participant.publish_data.assert_called_once()
    args, kwargs = room.local_participant.publish_data.call_args
    sent = json.loads(args[0])

    assert sent == {"type": "agent_state", "state": "thinking", "ts": 123.5}
    assert kwargs["reliable"] is True


def test_transcript_event_reaches_publish_data_in_the_pinned_shape():
    room = MagicMock()
    publisher = DataChannelPublisher(room)

    publisher.publish(transcript_event("assistant", "hello there", final=True))

    args, kwargs = room.local_participant.publish_data.call_args
    sent = json.loads(args[0])

    assert sent == {
        "type": "transcript",
        "role": "assistant",
        "text": "hello there",
        "final": True,
    }
    assert kwargs["reliable"] is True


def test_publish_data_receives_bytes_not_a_str():
    # publish_data accepts bytes|str, but we pin bytes so the payload is
    # unambiguous over the wire regardless of what the SDK defaults to.
    room = MagicMock()
    publisher = DataChannelPublisher(room)

    publisher.publish(agent_state_event("listening", ts=1.0))

    args, _ = room.local_participant.publish_data.call_args
    assert isinstance(args[0], bytes)


def test_publisher_targets_the_room_it_was_constructed_with():
    room = MagicMock()
    publisher = DataChannelPublisher(room)

    assert publisher.room is room


# --- the real SDK is async, and MagicMock is not -------------------------
#
# Every test above passes a MagicMock room, whose publish_data returns a Mock
# rather than a coroutine. The real livekit-rtc publish_data IS a coroutine
# function, so the production code path "called it and threw the coroutine
# away" -- no bytes ever left the process -- while assert_called_once() stayed
# green. These tests model the SDK's actual asynchrony, so that failure mode
# is visible.


class _AsyncLocalParticipant:
    """Stand-in whose publish_data is a real coroutine function, like the SDK's."""

    def __init__(self) -> None:
        self.awaited: list[tuple[bytes, dict]] = []

    async def publish_data(self, payload, **kwargs):
        self.awaited.append((payload, kwargs))


class _AsyncRoom:
    def __init__(self) -> None:
        self.local_participant = _AsyncLocalParticipant()


@pytest.mark.asyncio
async def test_publish_actually_awaits_the_sdk_coroutine():
    room = _AsyncRoom()
    publisher = DataChannelPublisher(room)

    publisher.publish(agent_state_event("speaking", ts=7.0))
    # publish() is synchronous by contract, so the send is scheduled rather
    # than awaited; yield once to let the scheduled task run.
    await asyncio.sleep(0)

    assert len(room.local_participant.awaited) == 1, (
        "publish_data coroutine was created but never scheduled -- "
        "no data-channel bytes would reach the client"
    )
    payload, kwargs = room.local_participant.awaited[0]
    assert json.loads(payload) == {"type": "agent_state", "state": "speaking", "ts": 7.0}
    assert kwargs["reliable"] is True
    assert kwargs["topic"] == "jarvis"


@pytest.mark.asyncio
async def test_publish_survives_a_failing_send_without_raising_into_the_pipeline():
    class _ExplodingParticipant:
        async def publish_data(self, payload, **kwargs):
            raise RuntimeError("data channel is down")

    class _ExplodingRoom:
        local_participant = _ExplodingParticipant()

    publisher = DataChannelPublisher(_ExplodingRoom())

    publisher.publish(agent_state_event("thinking", ts=1.0))  # must not raise
    await asyncio.sleep(0)


def test_publish_without_a_running_loop_drops_the_event_instead_of_crashing():
    # Called from a sync context there is no loop to schedule on. Dropping one
    # event is survivable; raising into a pipeline callback is not.
    room = _AsyncRoom()
    publisher = DataChannelPublisher(room)

    publisher.publish(agent_state_event("listening", ts=2.0))

    assert room.local_participant.awaited == []
