"""Provider construction, selected purely by environment variables (SPEC C2).

Every provider is built here and nowhere else in the pipeline, so the STT/TTS/
LLM backends stay swappable without touching the turn-loop code that uses them.
"""

from __future__ import annotations

import os

from livekit.agents import llm as llm_base
from livekit.agents import stt as stt_base
from livekit.agents import tts as tts_base
from livekit.plugins import anthropic, cartesia, deepgram

# TTS_PROVIDER is a backend *selector*, not a model id -- the hard constraint
# ("model IDs come from $CONVO_MODEL/$TOOL_MODEL only") does not apply to it,
# and DESIGN.md pins "cartesia" as the documented default.
_DEFAULT_TTS_PROVIDER = "cartesia"
TTS_PROVIDERS = ("cartesia", "aura2")


def build_llm() -> llm_base.LLM:
    """The single fast conversational model (T-1: no routing/escalation; that's T-2).

    Reads $CONVO_MODEL; never hardcodes a model id here. When the env var is
    unset, defers to the plugin's own default model rather than embedding a
    literal id in this call.
    """
    model = os.environ.get("CONVO_MODEL")
    return anthropic.LLM(model=model) if model else anthropic.LLM()


def build_stt() -> stt_base.STT:
    """Deepgram STT (SPEC C2). No env-var swap is pinned for STT."""
    return deepgram.STT()


def build_tts() -> tts_base.TTS:
    """Select the TTS backend by $TTS_PROVIDER alone.

    SPEC C2: the swap is env-var-only -- no code change, no abstraction layer
    beyond this function. "cartesia" (default) and "aura2" (Deepgram Aura-2,
    the plugin's own default model) are the two wired branches.
    """
    provider = os.environ.get("TTS_PROVIDER", _DEFAULT_TTS_PROVIDER).strip().lower()
    if provider == "aura2":
        return deepgram.TTS()
    if provider == "cartesia":
        return cartesia.TTS()
    raise ValueError(f"unknown TTS_PROVIDER={provider!r}, expected one of {TTS_PROVIDERS}")
