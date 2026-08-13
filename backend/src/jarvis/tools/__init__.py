"""Tool registry and ToolResult-producing tools (T-2 core, T-3 weather, T-4 github).

Deliberately does NOT import the ``self_manifest`` function by name here:
doing so would bind ``jarvis.tools.self_manifest`` (the function) over the
submodule Python auto-binds under that same identifier, breaking
``import jarvis.tools.self_manifest`` / ``jarvis.tools.self_manifest.self_manifest()``
for anyone relying on the submodule path. Import it explicitly instead:
``from jarvis.tools.self_manifest import self_manifest``.
"""

from __future__ import annotations

from .registry import ToolHandler, ToolRegistry, ToolSpec
from .self_manifest import SELF_MANIFEST_TOOL

__all__ = [
    "SELF_MANIFEST_TOOL",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
]
