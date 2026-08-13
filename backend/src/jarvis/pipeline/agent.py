"""The Jarvis agent definition (T-1: transport + turn loop only).

No tools, no memory, no grounding/refusal policy -- those are T-2+. A single
fast model reply is the whole job here.
"""

from __future__ import annotations

from livekit.agents import Agent

INSTRUCTIONS = (
    "You are Jarvis, a fast, friendly voice assistant. Keep replies brief and "
    "conversational since they are spoken aloud."
)


class JarvisAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)
