"""Grounded synthesis (docs/DESIGN.md §Interfaces): the only place an answer
may be assembled from ``ToolResult``s.

Signature is pinned exactly: ``synthesize_grounded(question, results,
prefs) -> str``. The extra ``model``/``client`` parameters below are
keyword-only with defaults, so the pinned three-positional-argument call
shape is unchanged -- they exist purely as the injection seam so tests can
drive this without a network call (see backend/tests/grounding/fakes.py).

Where the refusal decision lives -- two different places, deliberately:

1. STRUCTURAL no-evidence refusal lives entirely in ``has_supporting_evidence``
   / ``usable_results`` below, in this module, before any model client is
   ever touched. "No evidence" means: no results at all, every result has
   ``error`` set, or every result's ``data`` is empty. That is a fact about
   the shape of the results, deterministic and unit-testable on its own and
   via ``synthesize_grounded`` with a model client that raises if called
   (proving that path never reaches it).

2. SEMANTIC relevance -- whether data that *is* present actually answers
   *this* question -- is NOT decided here. An earlier version of this module
   tried to approximate that with a keyword-overlap heuristic between the
   question and the data; that produced wrong refusals on plainly answerable
   questions whose data just didn't share literal words with the question
   (e.g. "Will I need an umbrella?" vs. ``{"precipitation_mm": 5}``).
   Semantic relevance is a model judgment, not a string-matching problem, so
   any result that clears the structural gate is handed to the model under
   ANSWER_POLICY, which pins exactly this: "answer strictly from
   results[].data; if insufficient, refuse with REFUSAL." SPEC R6a names the
   T-7 golden eval set as the enforceable proxy for that judgment being
   correct -- this module does not re-decide it and does not invent a second
   relevance mechanism.
"""

from __future__ import annotations

import json
from typing import Protocol

from .policy import ANSWER_POLICY
from .routing import convo_model
from .types import REFUSAL, ToolResult

_MAX_TOKENS = 1024


class ModelClient(Protocol):
    """The injection seam ``synthesize_grounded`` calls through.

    Production default wraps the raw ``anthropic`` SDK (see
    ``AnthropicModelClient`` below); tests inject an in-memory double.
    """

    def complete(self, *, model: str, system: str, user: str) -> str: ...


class AnthropicModelClient:
    """Production ``ModelClient``: one non-streaming call to the Anthropic API."""

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic()

    def complete(self, *, model: str, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


def usable_results(results: list[ToolResult]) -> list[ToolResult]:
    """Results structurally capable of carrying evidence: no ``error`` set and
    non-empty ``data``. Purely structural -- this does NOT judge whether the
    data is relevant to any particular question; see module docstring.
    """
    return [r for r in results if not r.get("error") and r.get("data")]


def has_supporting_evidence(results: list[ToolResult]) -> bool:
    """True iff at least one result clears the structural no-evidence gate."""
    return bool(usable_results(results))


def synthesize_grounded(
    question: str,
    results: list[ToolResult],
    prefs: list[str],
    *,
    model: str | None = None,
    client: ModelClient | None = None,
) -> str:
    """Answer ``question`` strictly from ``results[].data``, honoring ``prefs``
    verbatim, or refuse with REFUSAL when there is no usable evidence at all.

    ``model``/``client`` are optional escalation/injection hooks: callers
    that already know this turn's routed model (llm/routing.route_model)
    pass it via ``model``; tests pass a fake ``client`` to avoid the network.
    Both default to the production path (CONVO_MODEL + the real Anthropic
    client) when omitted, so the pinned 3-argument call shape still works
    unchanged.
    """
    usable = usable_results(results)
    if not usable:
        return REFUSAL

    active_model = model if model is not None else convo_model()
    active_client = client if client is not None else AnthropicModelClient()

    user_prompt = _build_user_prompt(question, usable, prefs)
    reply = active_client.complete(model=active_model, system=ANSWER_POLICY, user=user_prompt)
    reply = reply.strip()
    return reply if reply else REFUSAL


def _build_user_prompt(question: str, results: list[ToolResult], prefs: list[str]) -> str:
    lines = [f"Question: {question}", "", "Tool results (JSON, this turn only):"]
    for r in results:
        lines.append(json.dumps({"source": r["source"], "data": r["data"]}, default=str))
    lines.append("")
    lines.append("User preferences (honor verbatim):")
    if prefs:
        lines.extend(f"- {p}" for p in prefs)
    else:
        lines.append("(none)")
    return "\n".join(lines)
