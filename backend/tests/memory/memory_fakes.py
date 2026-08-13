"""In-process test doubles for jarvis.memory (T-5) -- no network, no DB.

Mirrors the style of tests/grounding/fakes.py and tests/tools/test_weather.py's
in-file doubles: stand-ins for mem0's ``Memory`` object so
``Mem0MemoryStore``'s OWN wiring logic (what it passes to mem0, how it
shapes mem0's replies into ``ToolResult``s) can be proven without a
database. See test_contract.py for how these get substituted in via
``bare_store()``.
"""

from __future__ import annotations

from typing import Any


class FakeMem0Memory:
    """Records every ``add``/``search`` call it receives and returns a
    canned ``search`` reply -- proves what ``Mem0MemoryStore`` sends
    *into* mem0 (user-namespacing, infer=False, message shape) without any
    real mem0/pgvector behind it.
    """

    def __init__(self, search_results: list[dict[str, Any]] | None = None) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self._search_results = search_results if search_results is not None else []

    def add(self, messages, *, user_id=None, metadata=None, infer=True, **kwargs):
        self.add_calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "metadata": metadata,
                "infer": infer,
                **kwargs,
            }
        )
        return {"results": []}

    def search(self, query, *, filters=None, top_k=20, threshold=0.1, **kwargs):
        self.search_calls.append(
            {"query": query, "filters": filters, "top_k": top_k, "threshold": threshold, **kwargs}
        )
        return {"results": self._search_results}


class ExplodingMem0Memory:
    """Raises on search -- proves ``Mem0MemoryStore.search()``'s error path
    (never a guess: ``ToolResult`` with ``error`` set and ``data={}``).
    """

    def search(self, *args: Any, **kwargs: Any) -> dict:
        raise RuntimeError("simulated mem0/pgvector failure")
