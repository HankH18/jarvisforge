"""LiveKit Agents worker entrypoint.

Joins the room, streams Deepgram STT in, a dual-model pipeline (CONVO_MODEL
for tool selection/chit-chat, escalating to TOOL_MODEL for `heavy` tools --
see tools_wiring.py) replies grounded in real tool results, TTS (Cartesia
default / Aura-2 fallback, env-selected) streams out. Emits agent_state +
transcript data-channel events and per-turn latency JSONL.

T-15 assembles the real T-6 cadence package here too: a ``FillerGate`` (spoken
acknowledgment when a `heavy` tool call outlives ``FILLER_THRESHOLD_MS``, see
``cadence_wiring.build_filler_gate`` / ``tools_wiring._dispatch``) and a
``TurnController`` (barge-in pause + stop-phrase cancel, fed from the same
session events ``session_wiring.wire_session_events`` already wires).

T-16 (R1: time-to-first-audio on no-lookup turns <= 800ms p95) originally
built the ``AgentSession`` below with ``turn_handling=TurnHandlingOptions(
preemptive_generation={"preemptive_tts": True})`` unconditionally. The SDK's
own preemptive generation (``livekit.agents.voice.turn.
PreemptiveGenerationOptions``, installed livekit-agents 1.6.10) is already ON
by default (``enabled: True``), but with ``preemptive_tts: False`` (the SDK
default) it only starts the LLM call early, on a not-yet-final transcript --
TTS still waits for the turn to be officially confirmed before it starts
synthesizing. Setting ``preemptive_tts: True`` lets TTS *also* start
synthesizing the reply before confirmation, so by the time endpointing
actually confirms the turn is over (the moment ``latency.turn_ended()`` opens
the record this ticket measures from), synthesis is already underway or done
-- directly shrinking the turn_end -> first-audio gap R1 measures, for
exactly the no-tool-call chat turns that path covers (a fired tool still
goes through ``tools_wiring._dispatch``'s own ``session.say()``, unaffected
either way).

2026-08-15 PREEMPTIVE TTS IS NOW OFF BY DEFAULT (see ``_build_turn_handling``
below), after a Cartesia credit-burn incident traced directly to this
setting (full writeup: ``.claude/NEEDS_HUMAN.md``, "ANSWERED -- where the
free Cartesia credits went"). The mechanism: Cartesia bills by SECONDS OF
GENERATED AUDIO, not characters (their voice-changer is listed at "15
credits per second of audio", consistent with a 20,000-credit/month free
tier being ~27 minutes of audio). ``preemptive_tts: True`` makes TTS start
synthesizing on *every* not-yet-final STT transcript revision (see the
installed SDK's ``livekit/agents/voice/agent_activity.py``
``on_preemptive_generation`` / ``_cancel_preemptive_generation``), cancelling
and discarding the prior in-flight synthesis unless the final transcript
matches exactly. Because billing is per second of audio ALREADY GENERATED,
a cancelled/discarded synthesis is still very likely paid for -- worst case
up to ``max_retries`` (SDK default 3, see ``voice/turn.py``'s
``_PREEMPTIVE_GENERATION_DEFAULTS``) discarded syntheses plus the one real
one per spoken turn, and worse with live human speech (false starts, "um",
background noise -> many transcript revisions) than with
``drive_turns.py``'s clean pre-recorded WAV clips -- which is exactly what
burned a 20,000-credit/month free tier in ~13 minutes running a 55-turn
latency measurement (``drive_turns.py --turns 55`` against the deployed
worker).

This IS a genuine R1 latency lever (a real <800ms p95 time-to-first-audio
project requirement) and is not simply deleted -- it is env-driven so Hank
can A/B it later without a code change: set ``$JARVIS_PREEMPTIVE_TTS=1`` (see
``_PREEMPTIVE_TTS_ENV`` below) to opt back in. When opted in, ``max_retries``
is also capped to 1 (down from the SDK default of 3) so even the opted-in
path cannot cost the full 4x -- at most 1 discarded + 1 real synthesis per
turn instead of up to 3 discarded + 1 real.

This is a live SDK feature, not something jarvisforge builds; the "levers"
comment is code-level, evidence is the installed SDK source
(``livekit/agents/voice/turn.py``, ``voice/agent_activity.py``), not a live
measurement (blocked -- see docs/latency-report.md).
"""

from __future__ import annotations

import os

from livekit.agents import AgentSession, JobContext, TurnHandlingOptions, WorkerOptions, cli

from jarvis.auth.identity import stable_identity_key
from jarvis.cadence.interrupt import TurnController

from .agent import JarvisAgent
from .cadence_wiring import build_filler_gate
from .events import DataChannelPublisher
from .latency import LatencyLogger
from .providers import build_llm, build_stt, build_tts
from .session_wiring import wire_session_events
from .tools_wiring import LazyMemoryStore, build_registry

# $JARVIS_PREEMPTIVE_TTS opts back into T-16's preemptive-TTS latency lever
# (see module docstring for the full cost/latency tradeoff). Off by default.
_PREEMPTIVE_TTS_ENV = "JARVIS_PREEMPTIVE_TTS"
_PREEMPTIVE_TTS_TRUTHY = {"1", "true", "yes", "on"}
# SDK default is 3 (voice/turn.py's _PREEMPTIVE_GENERATION_DEFAULTS); capped
# lower here so the opted-in path still cannot pay for the full worst case.
_PREEMPTIVE_TTS_MAX_RETRIES_WHEN_ENABLED = 1


def _preemptive_tts_enabled() -> bool:
    return os.environ.get(_PREEMPTIVE_TTS_ENV, "").strip().lower() in _PREEMPTIVE_TTS_TRUTHY


def _build_turn_handling() -> TurnHandlingOptions:
    """Read $JARVIS_PREEMPTIVE_TTS fresh on every call (not cached at import
    time) so tests -- and a real process restart with a changed env var --
    both observe the current setting. See module docstring for why this
    defaults to OFF and what enabling it costs."""
    if not _preemptive_tts_enabled():
        return TurnHandlingOptions(preemptive_generation={"preemptive_tts": False})
    return TurnHandlingOptions(
        preemptive_generation={
            "preemptive_tts": True,
            "max_retries": _PREEMPTIVE_TTS_MAX_RETRIES_WHEN_ENABLED,
        }
    )


def _resolve_user_id(room: object) -> str:
    """Best-effort ``user_id`` for the memory tool: the identity of the
    first connected remote participant -- the same identity the token
    endpoint (T-1/T-10) issues, so memory recall is scoped to the real
    signed-in user.

    Resolved lazily on each memory-tool call (via the closure this feeds),
    not once at entrypoint startup: entrypoint must not block on a
    participant joining before ``session.start()``, and a caller that never
    drives a turn (e.g. the entrypoint tests, which pass a bare stub
    ``room``) never touches this at all.

    P1 FOLLOW-UP FIX: authenticated-mode ``/api/token`` (jarvis.server.app)
    now mints a SESSION-SCOPED LiveKit identity per tab/device
    (``"<user_id>::<random>"``, see ``jarvis.auth.identity``'s module
    docstring for why -- LiveKit's DUPLICATE_IDENTITY eviction). Read raw,
    that would feed mem0 a DIFFERENT namespace per session instead of a
    stable one per user -- cross-session memory (T-5's entire premise)
    would silently break for every authenticated user. Passing the raw
    identity through ``stable_identity_key`` recovers the stable
    ``user_id`` in authenticated mode, and is a total no-op in anonymous
    mode (no separator present, see that function's docstring) -- so
    anonymous-mode behaviour is unchanged.
    """
    try:
        participants = list(room.remote_participants.values())  # type: ignore[attr-defined]
    except AttributeError:
        return "unknown"
    if not participants:
        return "unknown"
    return stable_identity_key(participants[0].identity)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    memory = LazyMemoryStore(os.environ.get("DATABASE_URL"))
    registry = build_registry(memory=memory, user_id_getter=lambda: _resolve_user_id(ctx.room))

    session = AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        turn_handling=_build_turn_handling(),
    )

    publisher = DataChannelPublisher(ctx.room)
    latency = LatencyLogger(model=getattr(session.llm, "model", "unknown"))
    turn_controller = TurnController()
    wire_session_events(session, publisher, latency, turn_controller=turn_controller)

    filler_gate = build_filler_gate(session, publisher=publisher)

    agent = JarvisAgent(
        registry=registry,
        prefs_getter=lambda: memory.preferences(_resolve_user_id(ctx.room)),
        filler_gate=filler_gate,
        latency=latency,
    )
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
