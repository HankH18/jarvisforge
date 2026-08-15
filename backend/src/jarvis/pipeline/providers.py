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

# The installed livekit-plugins-cartesia's own TTS.__init__ default
# (`livekit/plugins/cartesia/tts.py`: `model: TTSModels | str = "sonic-3"`)
# as of this repo's pinned dependency version. Pinning it explicitly here is
# for REPRODUCIBILITY, not cost: before this pin, build_tts()'s Cartesia
# branch called `cartesia.TTS(word_timestamps=False)` with no `model=` at
# all, so it silently inherited whatever the installed plugin happened to
# default to -- a future plugin bump could move that default (and therefore
# this project's voice/latency/cost profile) with zero visibility in this
# repo's diff. Pinning the CURRENT value keeps behavior unchanged today.
#
# Do NOT change this to a "cheaper" model on a guess. Cartesia's pricing
# page and older-models docs publish no per-model credit rates, and since
# billing is by seconds of generated audio (not characters, not model
# choice), a different model is not obviously cheaper -- see
# .claude/NEEDS_HUMAN.md's 2026-08-15 Cartesia credit-burn investigation.
# $CARTESIA_TTS_MODEL overrides this for deliberate, evidence-driven A/B
# testing. Valid values (installed plugin's `models.py`, `TTSModels`):
# "sonic", "sonic-2", "sonic-lite", "sonic-preview", "sonic-turbo", "sonic-3".
_DEFAULT_CARTESIA_TTS_MODEL = "sonic-3"


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

    T-16 (R1 time-to-first-audio lever): the Cartesia branch pins
    ``word_timestamps=False``. The installed plugin (livekit-plugins-cartesia)
    defaults ``word_timestamps=True``, which asks Cartesia to compute and
    stream word-level alignment data alongside every audio chunk -- including
    the first one, on the critical path this ticket is trying to shrink. This
    repo never consumes that data: ``AgentSession``'s own
    ``use_tts_aligned_transcript`` (the one thing that would read it) is left
    at its default, which is disabled (see the installed SDK's
    ``AgentSession.__init__`` docstring: "When NOT_GIVEN, it's disabled"), and
    nothing else in ``pipeline/`` reads a TTS-provided transcript either (the
    spoken transcript published on the data channel comes from the chat_ctx
    item's own text, see ``session_wiring._on_conversation_item_added``).
    Turning off unused alignment work is a real cost removed, not a claimed
    speed number -- Cartesia TTS is unmeasurable right now (see
    docs/latency-report.md); this cannot be verified live until the account's
    credits are restored.

    2026-08-15: the Cartesia branch also now pins ``model=`` explicitly (see
    ``_DEFAULT_CARTESIA_TTS_MODEL`` above for why -- reproducibility, not
    cost), overridable via ``$CARTESIA_TTS_MODEL``.
    """
    provider = os.environ.get("TTS_PROVIDER", _DEFAULT_TTS_PROVIDER).strip().lower()
    if provider == "aura2":
        return deepgram.TTS()
    if provider == "cartesia":
        model = os.environ.get("CARTESIA_TTS_MODEL", _DEFAULT_CARTESIA_TTS_MODEL)
        return cartesia.TTS(model=model, word_timestamps=False)
    raise ValueError(f"unknown TTS_PROVIDER={provider!r}, expected one of {TTS_PROVIDERS}")
