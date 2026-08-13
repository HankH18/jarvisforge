"""self_manifest tool (SPEC R5; docs/DESIGN.md §Interfaces).

The only source Jarvis may cite when asked what it can do or what its
limits are. Reads capabilities.yaml fresh on every call (not cached/baked in
at import time), so an edit to the manifest is reflected on the very next
call -- never from model improvisation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from jarvis.llm.types import ToolResult

from .registry import ToolSpec

CAPABILITIES_PATH = Path(__file__).resolve().parent.parent / "capabilities.yaml"


def self_manifest(*, path: Path = CAPABILITIES_PATH) -> ToolResult:
    """Read ``path`` (default capabilities.yaml) and return it as a ToolResult.

    ``data`` is exactly the parsed YAML document -- nothing added, nothing
    summarized -- so a capability answer built from it can only ever contain
    manifest content.
    """
    try:
        raw = path.read_text(encoding="utf-8")
        manifest: dict[str, Any] = yaml.safe_load(raw) or {}
        error = None
    except OSError as exc:
        manifest = {}
        error = str(exc)

    return ToolResult(
        source="self:manifest",
        fetched_at=datetime.now(UTC).isoformat(),
        data=manifest,
        error=error,
    )


# Non-heavy per DESIGN.md §Interfaces (stays on $CONVO_MODEL).
SELF_MANIFEST_TOOL = ToolSpec(
    name="self_manifest",
    description=(
        "Report what Jarvis can and cannot do, from its versioned capabilities "
        "manifest. Use this for any question about Jarvis's own capabilities "
        "or limitations."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda **_: self_manifest(),
    heavy=False,
)
