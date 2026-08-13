"""Acceptance #2: routing escalates to $TOOL_MODEL only for tools tagged
`heavy`. The real heavy tools (github_qa, agent_pr) land in T-4/T-11 -- T-2's
job is only the tagging mechanism, so these are test-only stub ToolSpecs.
"""

from __future__ import annotations

import pytest

from jarvis.llm.routing import ModelRoutingError, route_model
from jarvis.tools.registry import ToolSpec


def _stub(name: str, *, heavy: bool) -> ToolSpec:
    return ToolSpec(name=name, description="", parameters={}, handler=lambda **_: None, heavy=heavy)


STUB_LIGHT = _stub("stub_light", heavy=False)
STUB_HEAVY = _stub("stub_heavy", heavy=True)


@pytest.fixture(autouse=True)
def _model_env(monkeypatch):
    monkeypatch.setenv("CONVO_MODEL", "stub-convo-model")
    monkeypatch.setenv("TOOL_MODEL", "stub-tool-model")


def test_no_tool_fired_stays_on_convo_model():
    assert route_model([]) == "stub-convo-model"


def test_non_heavy_tool_stays_on_convo_model():
    assert route_model([STUB_LIGHT]) == "stub-convo-model"


def test_heavy_tool_escalates_to_tool_model():
    assert route_model([STUB_HEAVY]) == "stub-tool-model"


def test_mixed_light_and_heavy_tools_escalate():
    """One heavy tool among several fired tools is enough to escalate."""
    assert route_model([STUB_LIGHT, STUB_HEAVY]) == "stub-tool-model"


def test_missing_convo_model_env_var_raises(monkeypatch):
    monkeypatch.delenv("CONVO_MODEL", raising=False)

    with pytest.raises(ModelRoutingError):
        route_model([STUB_LIGHT])


def test_missing_tool_model_env_var_raises_only_when_escalating(monkeypatch):
    monkeypatch.delenv("TOOL_MODEL", raising=False)

    # a non-heavy turn never needs $TOOL_MODEL, so it must not raise
    assert route_model([STUB_LIGHT]) == "stub-convo-model"

    with pytest.raises(ModelRoutingError):
        route_model([STUB_HEAVY])
