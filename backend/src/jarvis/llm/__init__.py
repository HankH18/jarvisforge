"""Model routing, ANSWER_POLICY, and grounded synthesis (T-2)."""

from __future__ import annotations

from .policy import ANSWER_POLICY
from .routing import ModelRoutingError, convo_model, route_model, tool_model
from .synthesis import ModelClient, has_supporting_evidence, synthesize_grounded, usable_results
from .types import REFUSAL, ToolResult

__all__ = [
    "ANSWER_POLICY",
    "REFUSAL",
    "ModelClient",
    "ModelRoutingError",
    "ToolResult",
    "convo_model",
    "has_supporting_evidence",
    "route_model",
    "synthesize_grounded",
    "tool_model",
    "usable_results",
]
