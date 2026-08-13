"""Grounding contract pinned in docs/DESIGN.md §Interfaces -- implemented
exactly as specified there. Every tool (T-3 weather, T-4 github, T-5 memory,
T-2 self_manifest) returns a ``ToolResult``; ``synthesize_grounded`` (T-2,
llm/synthesis.py) is the only place an answer may be built from them.
"""

from __future__ import annotations

from typing import TypedDict


class ToolResult(TypedDict):
    """The grounding unit. ``data`` is the ONLY material an answer may draw on."""

    source: str  # "weather:open-meteo" | "github:graphql" | "github:gitingest"
    # | "memory:mem0" | "self:manifest"
    fetched_at: str  # ISO-8601 UTC
    data: dict
    error: str | None


# Canonical refusal prefix (SPEC R6). A returned answer is a refusal iff it
# starts with this exact string; variants ("I don't know yet, but...") are
# allowed after it.
REFUSAL = "I don't know"
