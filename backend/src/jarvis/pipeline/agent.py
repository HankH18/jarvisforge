"""The Jarvis agent definition.

T-14 wires the real tool registry (weather/github/self_manifest/memory) into
this Agent's ``tools=`` list, adapted to livekit ``RawFunctionTool``s by
``tools_wiring.livekit_tools``. ``INSTRUCTIONS`` below governs only the
CONVO_MODEL tool-selection/chit-chat pass; it is deliberately NOT
concatenated with ``jarvis.llm.policy.ANSWER_POLICY`` -- see
``tools_wiring``'s module docstring for why that would reopen the exact
fabrication risk (R6) grounding exists to close, and for how "persona holds,
grounding wins" is achieved instead (the tool dispatcher speaks the grounded
answer directly and suppresses the model's own post-tool continuation).
"""

from __future__ import annotations

from collections.abc import Callable

from livekit.agents import Agent

from jarvis.cadence.gate import FillerGate
from jarvis.llm.synthesis import ModelClient
from jarvis.tools.registry import ToolRegistry

from .tools_wiring import livekit_tools

INSTRUCTIONS = (
    "You are Jarvis, a fast, friendly voice assistant. Keep replies brief and "
    "conversational since they are spoken aloud. When a question needs live "
    "facts -- weather, GitHub repo activity, something from an earlier "
    "conversation, or your own capabilities/limitations -- call the matching "
    "tool instead of answering from your own knowledge. Never state a "
    "weather or GitHub fact yourself; only the tool's result may say it."
)


class JarvisAgent(Agent):
    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        prefs_getter: Callable[[], list[str]] | None = None,
        model_client: ModelClient | None = None,
        filler_gate: FillerGate | None = None,
    ) -> None:
        registry = registry if registry is not None else ToolRegistry()
        prefs_getter = prefs_getter if prefs_getter is not None else (lambda: [])
        tools = livekit_tools(
            registry, prefs_getter=prefs_getter, model_client=model_client, gate=filler_gate
        )
        super().__init__(instructions=INSTRUCTIONS, tools=tools)
