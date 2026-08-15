"""TTS provider selection is env-var-only (SPEC C2, docs/DESIGN.md §Decisions).

Cartesia is the default; Deepgram Aura-2 is the documented fallback. Both
branches are wired through jarvis.pipeline.providers.build_tts and nothing
else -- no abstraction layer beyond that one function.
"""

from __future__ import annotations

import pytest
from livekit.plugins import cartesia, deepgram

from jarvis.pipeline.providers import build_tts


def test_defaults_to_cartesia_when_unset(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test")

    result = build_tts()

    assert isinstance(result, cartesia.TTS)


def test_cartesia_selected_explicitly(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "cartesia")
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test")

    assert isinstance(build_tts(), cartesia.TTS)


def test_cartesia_does_not_request_unused_word_timestamps(monkeypatch):
    """T-16 latency lever: this repo never consumes Cartesia's word-level
    alignment data (AgentSession's own use_tts_aligned_transcript stays at
    its disabled default; the published assistant transcript comes from the
    chat_ctx item's text, see session_wiring._on_conversation_item_added),
    so build_tts() must not ask Cartesia to compute and stream it on every
    chunk -- including the first one, which is on R1's critical path.
    ``capabilities.aligned_transcript`` is the plugin's own public reflection
    of the ``word_timestamps`` constructor argument (see the installed
    livekit-plugins-cartesia TTS.__init__: ``aligned_transcript=
    word_timestamps``), so this is observable without reaching into a
    private attribute.
    """
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test")

    result = build_tts()

    assert result.capabilities.aligned_transcript is False


def test_aura2_fallback_selected_by_env_var(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "aura2")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    result = build_tts()

    assert isinstance(result, deepgram.TTS)
    assert result.model.startswith("aura-2")


def test_provider_selection_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "AURA2")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    assert isinstance(build_tts(), deepgram.TTS)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

    with pytest.raises(ValueError):
        build_tts()


def test_cartesia_pins_an_explicit_model_by_default(monkeypatch):
    """2026-08-15: build_tts() used to call cartesia.TTS(word_timestamps=False)
    with no model=, silently inheriting whatever the installed plugin
    defaulted to (.claude/NEEDS_HUMAN.md's Cartesia credit-burn writeup: this
    is how the project ended up on sonic-3 without ever choosing it). Pin
    must match the CURRENT plugin default exactly (reproducibility, not a
    cost claim -- no per-model Cartesia pricing is published)."""
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.delenv("CARTESIA_TTS_MODEL", raising=False)
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test")

    result = build_tts()

    assert result._opts.model == "sonic-3"


def test_cartesia_tts_model_env_var_overrides_the_pin(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "cartesia")
    monkeypatch.setenv("CARTESIA_TTS_MODEL", "sonic-turbo")
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test")

    result = build_tts()

    assert result._opts.model == "sonic-turbo"
