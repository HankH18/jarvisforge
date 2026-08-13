"""Stub LLM wired into mem0's LLM slot (T-5).

Every mem0 LLM provider needs either a paid API key or a locally-served
model (Ollama) not available in this environment. ``Mem0MemoryStore``
(store.py) always calls ``Memory.add(..., infer=False)``, which -- per
mem0's own ``_add_to_vector_store`` -- never touches ``self.llm`` at all;
the LLM-based fact-extraction pipeline (``infer=True``) is simply not used
by this ticket.

``mem0.memory.main.Memory.__init__`` unconditionally constructs an LLM
instance regardless of whether ``infer`` is ever True, so this class exists
only to satisfy that construction. It raises loudly if anything ever calls
it, so an accidental future switch to ``infer=True`` fails fast instead of
silently trying to reach a real provider with no key configured.
"""

from __future__ import annotations

from typing import Any

from mem0.llms.base import LLMBase


class NullLLM(LLMBase):
    def generate_response(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            "NullLLM.generate_response was called -- mem0's LLM-based fact "
            "extraction (infer=True) is not configured; T-5's Mem0MemoryStore "
            "only ever calls Memory.add(..., infer=False). Wiring anything to "
            "infer=True needs a real LLM provider, not NullLLM."
        )
