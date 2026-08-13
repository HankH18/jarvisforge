"""``MemoryStore`` (docs/DESIGN.md §Interfaces), implemented on mem0 OSS +
Postgres/pgvector.

``Mem0MemoryStore`` is the only concrete implementation: it builds a real
``mem0.Memory`` orchestrator (dedup, hashing, id generation, user-scoped
filters -- all mem0, not reimplemented here) wired to:

* a real Postgres+pgvector vector store, via mem0's own, UNMODIFIED
  ``mem0.vector_stores.pgvector.PGVector`` (``provider="pgvector"``).
  Needs the separate ``psycopg-pool`` distribution installed
  (backend/pyproject.toml declares it); an earlier version of this file
  shimmed around its absence -- no longer needed now that it's a real
  dependency.
* a real local sentence embedder, via mem0's own, UNMODIFIED
  ``mem0.embeddings.fastembed.FastEmbedEmbedding``
  (``provider="fastembed"``), running ``BAAI/bge-small-en-v1.5`` -- a
  small (~130MB), CPU-only ONNX model. This actually captures semantic
  similarity (paraphrase recall), unlike this ticket's first pass, which
  used a hand-rolled hashed bag-of-words embedder that only matched on
  shared vocabulary and missed paraphrases entirely -- SPEC R3's own
  worked example ("yesterday I asked about X -- remind me") is exactly
  the paraphrase case that stand-in failed (see test_integration.py's
  paraphrase-recall tests). No API key: the model downloads once from
  Hugging Face on first use and is cached locally on disk after that
  (``~/.cache/fastembed`` by default) -- every embedding call after the
  first run is fully offline.
* ``NullLLM`` (null_llm.py) in mem0's LLM slot, never actually called:
  every write goes through ``Memory.add(..., infer=False)``, which skips
  mem0's LLM-based fact-extraction pipeline entirely and stores the given
  turns as raw memories. mem0 has no "no-op" LLM provider to select by
  name, so ``_mem0_providers.install()`` still repurposes one allowed
  provider slot ("lmstudio") for this stand-in -- see that module's
  docstring for why. No API key is read or required anywhere in this
  module.

``search()`` returns one ``ToolResult`` (source ``"memory:mem0"``) per
matched memory, or ``[]`` when nothing matches -- an empty list is not an
error, it is "no evidence", and feeds
``jarvis.llm.synthesis.has_supporting_evidence``'s structural refusal gate
like any other empty/no-result tool call. A real failure (e.g. the database
is unreachable) instead returns a single ``ToolResult`` with ``error`` set
and ``data={}``, matching the T-3/T-4 convention: never a guess, always a
shape the no-evidence gate can catch. Per the T-5 ticket's carry-over from
T-3/T-4, ``fetched_at`` is duplicated into ``data`` on the success path,
since ``synthesize_grounded``'s prompt builder forwards only
``results[].source``/``results[].data``, not the top-level field.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Protocol

# Must run before any ``import mem0`` anywhere in this process: mem0 reads
# MEM0_TELEMETRY once, at import time, in mem0.memory.telemetry (a
# module-level constant) -- setting the env var after that module has
# already been imported has no effect for the rest of the process.
os.environ.setdefault("MEM0_TELEMETRY", "False")

from mem0 import Memory  # noqa: E402
from mem0.configs.base import MemoryConfig  # noqa: E402
from mem0.embeddings.configs import EmbedderConfig  # noqa: E402
from mem0.llms.configs import LlmConfig  # noqa: E402
from mem0.vector_stores.configs import VectorStoreConfig  # noqa: E402

from jarvis.llm.types import ToolResult  # noqa: E402

from . import _mem0_providers, preferences  # noqa: E402
from .turns import Turn  # noqa: E402

_SOURCE = "memory:mem0"
_DEFAULT_COLLECTION = "jarvis_memory"
_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
_EMBEDDING_DIMS = 384  # BAAI/bge-small-en-v1.5's real output size


class MemoryStore(Protocol):
    """Pinned in docs/DESIGN.md §Interfaces -- implemented exactly."""

    def add_turns(self, user_id: str, session_id: str, turns: list[Turn]) -> None: ...

    def search(self, user_id: str, query: str, k: int = 5) -> list[ToolResult]: ...

    def get_preferences(self, user_id: str) -> list[str]: ...

    def add_preference(self, user_id: str, rule: str) -> None: ...


class Mem0MemoryStore:
    """``MemoryStore`` backed by mem0 OSS's local ``Memory`` orchestration
    on a real Postgres+pgvector database. See module docstring for how the
    embedder/vector-store/LLM slots are wired.
    """

    def __init__(
        self,
        *,
        database_url: str,
        collection_name: str = _DEFAULT_COLLECTION,
    ) -> None:
        _mem0_providers.install()
        preferences.ensure_preferences_table(database_url)

        self._database_url = database_url
        config = MemoryConfig(
            vector_store=VectorStoreConfig(
                provider="pgvector",
                config={
                    "connection_string": database_url,
                    "collection_name": collection_name,
                    "embedding_model_dims": _EMBEDDING_DIMS,
                    # No index needed at this project's scale; keeps
                    # create_col() simple and avoids any pgvector-version
                    # dependent index-type behavior.
                    "hnsw": False,
                    "diskann": False,
                },
            ),
            embedder=EmbedderConfig(
                provider="fastembed",
                config={"model": _EMBEDDING_MODEL, "embedding_dims": _EMBEDDING_DIMS},
            ),
            llm=LlmConfig(provider="lmstudio", config={}),
        )
        self._memory = Memory(config)

    def add_turns(self, user_id: str, session_id: str, turns: list[Turn]) -> None:
        """Persist ``turns`` as raw memories scoped to ``user_id``.

        ``infer=False`` -- mem0 stores each turn's text directly instead of
        running its LLM-based fact-extraction pipeline (see module
        docstring / null_llm.py). ``session_id`` rides along as metadata for
        traceability; recall is scoped by ``user_id`` only, per the pinned
        ``search`` signature (full multi-session/user semantics land in
        T-10 -- this ticket proves the user-isolation primitive).
        """
        if not turns:
            return
        messages = [{"role": t["role"], "content": t["text"]} for t in turns]
        self._memory.add(
            messages,
            user_id=user_id,
            metadata={"session_id": session_id},
            infer=False,
        )

    def search(self, user_id: str, query: str, k: int = 5) -> list[ToolResult]:
        fetched_at = datetime.now(UTC).isoformat()
        try:
            # mem0's own default threshold (0.1) does essentially nothing
            # for a real sentence embedder: BAAI/bge-small-en-v1.5 (like
            # most short-text sentence embeddings) has a high baseline
            # cosine similarity between totally unrelated sentences --
            # empirically 0.3-0.45 for this model on short/generic
            # inputs, occasionally higher -- so 0.1 lets everything
            # through. 0.5 was picked empirically (see
            # test_integration.py's paraphrase-recall tests): genuine
            # paraphrase matches scored 0.6-0.75, unrelated
            # reasonably-specific queries scored well under 0.5. This is
            # not a perfect filter -- very short/generic stored text
            # (e.g. "I'm allergic to X") can still score close to the
            # boundary against an unrelated query, a known limitation of
            # small local embedding models, not something this threshold
            # can fully fix.
            raw = self._memory.search(
                query, filters={"user_id": user_id}, top_k=k, threshold=0.5
            )
        except Exception as exc:  # pragma: no cover - exercised via integration test
            return [ToolResult(source=_SOURCE, fetched_at=fetched_at, data={}, error=str(exc))]

        results = []
        for item in raw.get("results", []):
            data = {
                "text": item.get("memory", ""),
                "score": item.get("score"),
                "created_at": item.get("created_at"),
                "fetched_at": fetched_at,
            }
            results.append(ToolResult(source=_SOURCE, fetched_at=fetched_at, data=data, error=None))
        return results

    def get_preferences(self, user_id: str) -> list[str]:
        return preferences.get_preferences(self._database_url, user_id)

    def add_preference(self, user_id: str, rule: str) -> None:
        preferences.add_preference(self._database_url, user_id, rule)
