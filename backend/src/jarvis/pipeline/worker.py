"""LiveKit Agents worker entrypoint.

Joins the room, streams Deepgram STT in, a dual-model pipeline (CONVO_MODEL
for tool selection/chit-chat, escalating to TOOL_MODEL for `heavy` tools --
see tools_wiring.py) replies grounded in real tool results, TTS (Cartesia
default / Aura-2 fallback, env-selected) streams out. Emits agent_state +
transcript data-channel events and per-turn latency JSONL.
"""

from __future__ import annotations

import os

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli

from .agent import JarvisAgent
from .events import DataChannelPublisher
from .latency import LatencyLogger
from .providers import build_llm, build_stt, build_tts
from .session_wiring import wire_session_events
from .tools_wiring import LazyMemoryStore, build_registry


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
    )

    publisher = DataChannelPublisher(ctx.room)
    latency = LatencyLogger(model=getattr(session.llm, "model", "unknown"))
    wire_session_events(session, publisher, latency)

    agent = JarvisAgent(
        registry=registry,
        prefs_getter=lambda: memory.preferences(_resolve_user_id(ctx.room)),
    )
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
