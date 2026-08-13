"""Acceptance #1: ToolResult, REFUSAL, and synthesize_grounded's call shape
exactly as pinned in docs/DESIGN.md §Interfaces.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.llm.types import REFUSAL, ToolResult


def test_refusal_is_the_pinned_literal():
    assert REFUSAL == "I don't know"


def test_tool_result_has_exactly_the_pinned_fields_and_types():
    hints = get_type_hints(ToolResult)
    assert set(hints) == {"source", "fetched_at", "data", "error"}
    assert hints["source"] is str
    assert hints["fetched_at"] is str
    assert hints["data"] is dict


def test_tool_result_is_constructible_exactly_as_pinned():
    result: ToolResult = {
        "source": "weather:open-meteo",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {"temp_c": 20},
        "error": None,
    }
    assert result["source"] == "weather:open-meteo"
    assert result["error"] is None


def test_synthesize_grounded_keeps_the_pinned_three_positional_arg_shape():
    """The pinned signature is `(question, results, prefs) -> str`. Any extra
    parameters this implementation adds (the model/client injection seam)
    must be optional and keyword-only, so `synthesize_grounded(q, r, p)`
    still means exactly what DESIGN.md pins.
    """
    sig = inspect.signature(synthesize_grounded)
    params = list(sig.parameters.values())

    positional = [
        p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert [p.name for p in positional] == ["question", "results", "prefs"]
    assert all(p.default is inspect.Parameter.empty for p in positional)

    extra = [p for p in params if p not in positional]
    assert all(p.kind == p.KEYWORD_ONLY for p in extra)
    assert all(p.default is not inspect.Parameter.empty for p in extra)

    assert sig.return_annotation in (str, "str")
