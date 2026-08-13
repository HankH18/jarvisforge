"""Wires jarvis's one offline mem0 provider into mem0's provider registry (T-5).

Originally (first pass at this ticket) this module also stood in for the
embedder and for ``psycopg_pool``, because neither ``fastembed`` nor the
separate ``psycopg-pool`` distribution were declared in
backend/pyproject.toml. Both gaps were real plan defects -- fastembed is
what mem0's own real embedder provider needs, and ``psycopg_pool`` is what
mem0's real ``PGVector`` vector store needs -- and were fixed by adding
both packages to pyproject.toml (an authorized, human-approved scope
exception; out of this ticket's normal file scope). With both installed,
mem0's OWN provider classes work unmodified:

* the vector store uses mem0's real ``mem0.vector_stores.pgvector.PGVector``
  directly (``provider="pgvector"`` in store.py) -- no shim needed.
* the embedder uses mem0's real ``mem0.embeddings.fastembed.FastEmbedEmbedding``
  directly (``provider="fastembed"`` in store.py) -- no registry override
  needed.

What's left to work around is the LLM slot. mem0's ``MemoryConfig``
validates ``llm.provider`` against a fixed allow-list
(``mem0.llms.configs.LlmConfig``) and every one of those providers expects
either a paid API key or a locally-served model (Ollama/LM Studio/vLLM) --
none of which this environment has, and T-5 doesn't need one:
``Mem0MemoryStore.add_turns`` always calls ``Memory.add(..., infer=False)``,
which -- per mem0's own ``_add_to_vector_store`` -- never touches
``self.llm`` at all. mem0 still unconditionally *constructs* an LLM
instance in ``Memory.__init__`` regardless, so something has to occupy an
allowed provider name. There is no "no-op"/"null" provider in mem0's
allow-list to ask for by name, so this module overwrites
``LlmFactory.provider_to_class["lmstudio"]`` (a plain dict, mem0's own
provider registry) to point at ``NullLLM`` (null_llm.py) instead of mem0's
real LM-Studio client. "lmstudio" is repurposed for its "local stand-in"
connotation; the real LM Studio provider is not being called.

This is process-global (``LlmFactory.provider_to_class`` is a module-level
dict), which is fine here: nothing else in this codebase constructs a mem0
``Memory`` instance. Call ``install()`` before constructing any
``mem0.Memory`` -- ``store.py`` does this at import time.
"""

from __future__ import annotations

import os

from mem0.configs.llms.base import BaseLlmConfig
from mem0.utils.factory import LlmFactory

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # mem0 pings PostHog telemetry on Memory() construction by default;
    # keep this fully offline unless a caller has already opted in/out.
    os.environ.setdefault("MEM0_TELEMETRY", "False")

    LlmFactory.provider_to_class["lmstudio"] = (
        "jarvis.memory.null_llm.NullLLM",
        BaseLlmConfig,
    )

    _INSTALLED = True
