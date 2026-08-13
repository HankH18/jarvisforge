"""self_manifest() (SPEC R5): reads capabilities.yaml and returns it as a
ToolResult with source "self:manifest", exactly as pinned in DESIGN.md.
"""

from __future__ import annotations

import yaml

from jarvis.tools.registry import ToolSpec
from jarvis.tools.self_manifest import SELF_MANIFEST_TOOL, self_manifest


def test_self_manifest_reads_the_real_capabilities_yaml():
    result = self_manifest()

    assert result["source"] == "self:manifest"
    assert result["error"] is None
    assert set(result["data"]) == {"capabilities", "limitations", "version"}
    assert isinstance(result["data"]["capabilities"], list) and result["data"]["capabilities"]
    assert isinstance(result["data"]["limitations"], list) and result["data"]["limitations"]
    assert isinstance(result["data"]["version"], str)


def test_self_manifest_content_matches_a_given_file_exactly(tmp_path):
    manifest_path = tmp_path / "capabilities.yaml"
    manifest_path.write_text(
        "version: '9.9.9'\n"
        "capabilities:\n  - only this one capability\n"
        "limitations:\n  - only this one limitation\n"
    )

    result = self_manifest(path=manifest_path)

    assert result["data"] == {
        "version": "9.9.9",
        "capabilities": ["only this one capability"],
        "limitations": ["only this one limitation"],
    }


def test_self_manifest_surfaces_a_read_error_instead_of_fabricating_content(tmp_path):
    missing_path = tmp_path / "does_not_exist.yaml"

    result = self_manifest(path=missing_path)

    assert result["source"] == "self:manifest"
    assert result["data"] == {}
    assert result["error"] is not None


def test_removing_a_manifest_entry_changes_the_answer(tmp_path):
    """Acceptance #4: removing an entry from the manifest changes the answer."""
    original = {"version": "1.0.0", "capabilities": ["alpha", "beta"], "limitations": ["gamma"]}
    path = tmp_path / "capabilities.yaml"
    path.write_text(yaml.safe_dump(original))

    before = self_manifest(path=path)
    assert "beta" in before["data"]["capabilities"]

    path.write_text(yaml.safe_dump(dict(original, capabilities=["alpha"])))
    after = self_manifest(path=path)

    assert "beta" not in after["data"]["capabilities"]
    assert before["data"] != after["data"]


def test_self_manifest_tool_is_registered_non_heavy():
    """DESIGN.md: self_manifest stays on $CONVO_MODEL (never escalates)."""
    assert isinstance(SELF_MANIFEST_TOOL, ToolSpec)
    assert SELF_MANIFEST_TOOL.name == "self_manifest"
    assert SELF_MANIFEST_TOOL.heavy is False

    # the registered handler delegates to the same self_manifest() -- compare
    # source/data/error (not fetched_at, which legitimately differs between
    # two separate calls a moment apart).
    via_handler = SELF_MANIFEST_TOOL.handler()
    via_direct = self_manifest()
    assert via_handler["source"] == via_direct["source"]
    assert via_handler["data"] == via_direct["data"]
    assert via_handler["error"] == via_direct["error"]
