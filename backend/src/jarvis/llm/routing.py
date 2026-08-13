"""Dual-model routing (docs/DESIGN.md §Interfaces, SPEC C3).

The fast model ($CONVO_MODEL) runs every turn with tool schemas attached.
When a tool tagged `heavy` fires this turn (per jarvis.tools.registry), the
reasoning+answer step escalates to $TOOL_MODEL for that turn;
weather/memory/self_manifest stay non-heavy and the turn stays on
$CONVO_MODEL. The heavy tools themselves (github_qa, agent_pr) land in
T-4/T-11 -- this module only proves and exercises the escalation decision.

Model ids come from these two env vars ONLY (SPEC C3), mirroring the
convention in pipeline/providers.py's ``build_llm``. Unlike ``build_llm``
(which can defer to a livekit plugin's own default when unset), the raw
Anthropic SDK call in llm/synthesis.py needs a concrete model id string, so
an unset var is a hard error here rather than a silent default.

``route_model`` takes anything with a ``heavy`` attribute (structural typing,
via ``HeavyTagged`` below) rather than importing ``jarvis.tools.registry.
ToolSpec`` directly -- ``jarvis.tools.registry`` imports ``jarvis.llm.types``,
so an ``llm`` -> ``tools`` runtime import here would be circular. A real
``ToolSpec`` satisfies ``HeavyTagged`` structurally, so callers pass
``ToolSpec`` instances unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Protocol


class HeavyTagged(Protocol):
    heavy: bool


class ModelRoutingError(RuntimeError):
    """Raised when a required model-id env var is unset."""


def convo_model() -> str:
    """$CONVO_MODEL -- the always-on fast conversational model."""
    return _require_env("CONVO_MODEL")


def tool_model() -> str:
    """$TOOL_MODEL -- the escalation model for `heavy` tools."""
    return _require_env("TOOL_MODEL")


def route_model(fired_tools: Iterable[HeavyTagged]) -> str:
    """Pick the model id for this turn's reasoning+answer step.

    Escalates to ``tool_model()`` iff at least one of ``fired_tools`` is
    tagged ``heavy=True``; otherwise stays on ``convo_model()``. An empty
    iterable (no tool fired this turn) stays on ``convo_model()``.
    """
    if any(spec.heavy for spec in fired_tools):
        return tool_model()
    return convo_model()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ModelRoutingError(
            f"${name} is not set -- model ids come from env vars only (SPEC C3)"
        )
    return value
