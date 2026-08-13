"""LiveKit Agents worker entrypoint (T-1: transport + turn loop only).

Joins the room, streams Deepgram STT in, a single fast model (CONVO_MODEL)
replies, TTS (Cartesia default / Aura-2 fallback, env-selected) streams out.
Emits agent_state + transcript data-channel events and per-turn latency JSONL.
"""

from __future__ import annotations

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli

from .agent import JarvisAgent
from .events import DataChannelPublisher
from .latency import LatencyLogger
from .providers import build_llm, build_stt, build_tts
from .session_wiring import wire_session_events


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
    )

    publisher = DataChannelPublisher(ctx.room)
    latency = LatencyLogger(model=getattr(session.llm, "model", "unknown"))
    wire_session_events(session, publisher, latency)

    await session.start(agent=JarvisAgent(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
