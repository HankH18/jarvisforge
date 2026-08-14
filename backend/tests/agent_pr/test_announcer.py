"""Announcer: fans one announcement out to the data channel (agent_state:
working) and TTS (T-11 acceptance #2), with both channels optional."""

from __future__ import annotations

import pytest
from agent_pr_fakes import RecordingPublisher, RecordingSpeaker

from jarvis.agents_pr.announcer import Announcer
from jarvis.pipeline.events import agent_state_event


@pytest.mark.asyncio
async def test_announce_publishes_working_and_speaks_the_message():
    publisher = RecordingPublisher()
    speaker = RecordingSpeaker()
    announcer = Announcer(publisher=publisher, speak=speaker)

    await announcer.announce("Starting on issue #1.")

    assert speaker.spoken == ["Starting on issue #1."]
    assert len(publisher.events) == 1
    event = publisher.events[0]
    assert event["type"] == "agent_state"
    assert event["state"] == "working"
    assert event == agent_state_event("working", ts=event["ts"])


@pytest.mark.asyncio
async def test_announce_with_no_publisher_still_speaks():
    speaker = RecordingSpeaker()
    announcer = Announcer(publisher=None, speak=speaker)

    await announcer.announce("hello")

    assert speaker.spoken == ["hello"]


@pytest.mark.asyncio
async def test_announce_with_no_speaker_still_publishes():
    publisher = RecordingPublisher()
    announcer = Announcer(publisher=publisher, speak=None)

    await announcer.announce("hello")

    assert len(publisher.events) == 1


@pytest.mark.asyncio
async def test_announce_with_neither_channel_is_a_clean_no_op():
    announcer = Announcer()  # publisher=None, speak=None (defaults)

    await announcer.announce("hello")  # must not raise


@pytest.mark.asyncio
async def test_each_announce_call_publishes_and_speaks_independently():
    publisher = RecordingPublisher()
    speaker = RecordingSpeaker()
    announcer = Announcer(publisher=publisher, speak=speaker)

    await announcer.announce("one")
    await announcer.announce("two")

    assert speaker.spoken == ["one", "two"]
    assert len(publisher.events) == 2
