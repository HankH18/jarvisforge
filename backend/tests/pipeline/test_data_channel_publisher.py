"""DataChannelPublisher: the production EventPublisher (docs/DESIGN.md §Interfaces).

test_turn_loop.py only ever exercises RecordingPublisher, a test-only stub --
it proves the turn-loop wiring calls *some* publisher correctly, but never
proves the pinned wire format survives the real publish path. This file
closes that gap: a mocked `rtc.Room` stands in for the network, and we assert
on the literal bytes handed to `publish_data`, decoded back to JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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
