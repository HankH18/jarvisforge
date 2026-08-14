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

T-16 (R1: time-to-first-audio on no-lookup turns <= 800ms p95): the
``AgentSession`` below is built with ``turn_handling=TurnHandlingOptions(
preemptive_generation={"preemptive_tts": True})``. The SDK's own preemptive
generation (``livekit.agents.voice.turn.PreemptiveGenerationOptions``,
installed livekit-agents 1.6.10) is already ON by default
(``enabled: True``), but with ``preemptive_tts: False`` it only starts the
LLM call early, on a not-yet-final transcript -- TTS still waits for the
turn to be officially confirmed before it starts synthesizing. Setting
``preemptive_tts: True`` lets TTS *also* start synthesizing the reply before
confirmation, so by the time endpointing actually confirms the turn is over
(the moment ``latency.turn_ended()`` opens the record this ticket measures
from), synthesis is already underway or done -- directly shrinking the
turn_end -> first-audio gap R1 measures, for exactly the no-tool-call chat
turns that path covers (a fired tool still goes through
``tools_wiring._dispatch``'s own ``session.say()``, unaffected either way).
This is a live SDK feature, not something jarvisforge builds; the "levers"
comment is code-level, evidence is the installed SDK source
(``livekit/agents/voice/turn.py``), not a live measurement (blocked --
see docs/latency-report.md).
"""

from __future__ import annotations

import os

from livekit.agents import AgentSession, JobContext, TurnHandlingOptions, WorkerOptions, cli

from jarvis.cadence.interrupt import TurnController

from .agent import JarvisAgent
from .cadence_wiring import build_filler_gate
from .events import DataChannelPublisher
from .latency import LatencyLogger
from .providers import build_llm, build_stt, build_tts
from .session_wiring import wire_session_events
from .tools_wiring import LazyMemoryStore, build_registry

# T-16: TTS already starts synthesizing before the turn is officially
# confirmed (see module docstring); LLM-side preemptive generation was
# already on by default.
_TURN_HANDLING = TurnHandlingOptions(preemptive_generation={"preemptive_tts": True})


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
    """
    try:
        participants = list(room.remote_participants.values())  # type: ignore[attr-defined]
    except AttributeError:
        return "unknown"
    return participants[0].identity if participants else "unknown"


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    memory = LazyMemoryStore(os.environ.get("DATABASE_URL"))
    registry = build_registry(memory=memory, user_id_getter=lambda: _resolve_user_id(ctx.room))

    session = AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        turn_handling=_TURN_HANDLING,
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
