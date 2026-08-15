"""Wires the real T-6 ``cadence/`` package into the live AgentSession (T-15).

cadence/ is reused unmodified: ``FillerBank``/``FillerGate``/``TurnController``
and the pinned policy constants (``jarvis.cadence.policy``) are the real T-6
pieces, imported here and adapted to the concrete shapes the installed
livekit-agents SDK (1.6.10) wants. Nothing about the gating rule, the
stop-phrase table, or the pause/cancel state machine is reimplemented --
this module only builds the SDK-facing glue: a ``Player`` that speaks a
``FillerClip`` as pre-recorded audio through a real ``AgentSession``, and a
loader that degrades to "no fillers" when the committed assets are missing.

**Playing a filler clip (not re-synthesized TTS):** ``AgentSession.say()``
accepts pre-recorded audio directly via ``audio=<AsyncIterable[rtc.AudioFrame]>``,
which skips TTS entirely -- confirmed against the installed SDK's
``voice/agent_activity.py`` (``_tts_task_impl``'s ``else`` branch: "use the
provided audio" -> ``perform_audio_forwarding(...)``, `text` only feeds the
transcript). ``audio_frames_from_file`` (the same helper
``BackgroundAudioPlayer`` uses internally for this exact job) decodes a WAV
on disk into that iterable. The clips committed under
``backend/src/jarvis/cadence/assets/fillers/`` (the single packaged source
of truth -- see docs/filler-asset-packaging.md) are 24kHz mono PCM16 (see
manifest.json), so that is what is requested from the decoder.
``add_to_chat_ctx=False`` keeps
a filler ("One sec.") out of chat history so it can never be mistaken for a
real turn by ``tools_wiring._last_user_utterance``'s backward walk.

Barge-in ("pause") for how ``TurnController`` is fed from real session
events is documented on ``jarvis.pipeline.session_wiring.wire_session_events``
(the events cadence needs are the same ones that module already wires).
"""

from __future__ import annotations

import logging

from livekit.agents import AgentSession
from livekit.agents.utils.audio import audio_frames_from_file

from jarvis.cadence.fillers import FillerBank, FillerClip
from jarvis.cadence.gate import FillerGate

from .events import EventPublisher

logger = logging.getLogger(__name__)

# Real committed clip format (backend/src/jarvis/cadence/assets/fillers/manifest.json:
# "sample_rate": 24000, "channels": 1).
_FILLER_SAMPLE_RATE = 24000
_FILLER_NUM_CHANNELS = 1


def load_filler_bank() -> FillerBank | None:
    """The real T-6 ``FillerBank`` built from the committed manifest, or
    ``None`` when the assets are missing/broken.

    A missing clip must never take down the voice loop (T-15 brief): this
    degrades to "no fillers" -- logging a warning -- rather than raising or
    crashing session startup.
    """
    try:
        return FillerBank.from_manifest()
    except (FileNotFoundError, ValueError):
        logger.warning("filler assets unavailable; running without fillers", exc_info=True)
        return None


def make_filler_player(session: AgentSession):
    """Builds the ``cadence.gate.Player`` that speaks one ``FillerClip`` as
    pre-recorded audio (not re-synthesized TTS) through ``session.say()``.
    """

    async def _play(clip: FillerClip) -> None:
        session.say(
            clip.text,
            audio=audio_frames_from_file(
                str(clip.path),
                sample_rate=_FILLER_SAMPLE_RATE,
                num_channels=_FILLER_NUM_CHANNELS,
            ),
            allow_interruptions=True,
            add_to_chat_ctx=False,
        )

    return _play


def build_filler_gate(
    session: AgentSession, *, publisher: EventPublisher | None = None
) -> FillerGate | None:
    """One ``FillerGate`` per session, or ``None`` when the filler assets are
    unavailable -- callers must then run tool calls un-fillered rather than
    crash (see ``load_filler_bank``).
    """
    bank = load_filler_bank()
    if bank is None:
        return None
    return FillerGate(bank, make_filler_player(session), publisher=publisher)
