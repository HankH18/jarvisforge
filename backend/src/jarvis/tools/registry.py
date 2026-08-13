"""Tool registration + the `heavy` routing tag (docs/DESIGN.md §Interfaces).

T-2 owns the tagging *mechanism* only. No concrete external tool is
registered here -- ``get_weather``/``load_repo_digest``/``github_query``
land in T-3/T-4, ``MemoryStore.search`` in T-5, ``agent_pr`` in T-11.
``self_manifest`` (this ticket) is the one real tool wired through it.

A tool is `heavy` when firing it should escalate the reasoning+answer step
to $TOOL_MODEL for that turn (see llm/routing.py). Per DESIGN.md, the heavy
tools are ``github_qa`` and ``agent_pr``; ``weather``, ``memory``, and
``self_manifest`` stay non-heavy.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from jarvis.llm.types import ToolResult

ToolHandler = Callable[..., ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool: its CONVO_MODEL-facing schema, its handler, and
    whether firing it is `heavy` (escalates to $TOOL_MODEL, see routing.py).
    """

    name: str
    description: str
    parameters: Mapping[str, Any]  # JSON schema `input_schema`/`parameters` object
    handler: ToolHandler
    heavy: bool = False


class ToolRegistry:
    """A mutable collection of ``ToolSpec``s for one agent session/turn.

    Kept as an instantiable class (not a module-level singleton) so each
    ticket that adds real tools (T-3/T-4/T-5) builds its own registry rather
    than depending on import-time registration order.
    """

    def __init__(self, specs: Mapping[str, ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = dict(specs) if specs else {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Tool schemas to attach to the always-on CONVO_MODEL turn."""
        return [
            {"name": t.name, "description": t.description, "input_schema": dict(t.parameters)}
            for t in self._tools.values()
        ]

    def heavy_names(self) -> frozenset[str]:
        return frozenset(t.name for t in self._tools.values() if t.heavy)
