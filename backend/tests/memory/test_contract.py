"""Offline contract/unit tests for jarvis.memory (T-5). No network, no DB --
every test here runs unconditionally in CI.

What these prove: protocol conformance, the ``Turn`` shape, ``ToolResult``
shaping (source, ``fetched_at`` duplicated into ``data`` per the T-3/T-4
carry-over), user-namespacing (what ``Mem0MemoryStore`` sends *into* mem0),
and error handling (a mem0 failure becomes an errored ``ToolResult``, never
a guess). ``Mem0MemoryStore`` is built via ``bare_store()`` below, which
bypasses ``__init__`` (skipping its real Postgres/mem0 construction) and
substitutes an in-process fake for ``self._memory`` -- see memory_fakes.py.

What these do NOT prove: that mem0 + real pgvector actually perform a
correct round trip, or that user isolation holds against a real database.
Those are integration-only claims -- see test_integration.py.
"""

from __future__ import annotations

import inspect

from memory_fakes import ExplodingMem0Memory, FakeMem0Memory

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.llm.types import REFUSAL
from jarvis.memory import Mem0MemoryStore, MemoryStore, Turn
from jarvis.memory.store import _SOURCE


def bare_store(memory) -> Mem0MemoryStore:
    """A ``Mem0MemoryStore`` with ``self._memory`` swapped for a fake,
    bypassing ``__init__`` (which otherwise talks to a real Postgres and
    builds a real mem0 ``Memory``). Local to this test module by design --
    it is not, and should not become, part of the public contract.
    """
    store = object.__new__(Mem0MemoryStore)
    store._memory = memory
    store._database_url = "unused-in-offline-tests"
    return store


class RecordingModelClient:
    """Returns a canned reply and records exactly what it was asked
    (matches the convention in tests/tools/test_weather.py / tests/grounding).
    """

    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


class ExplodingModelClient:
    """Raises if ever called -- proves a code path never reaches the model."""

    def complete(self, *, model: str, system: str, user: str) -> str:
        raise AssertionError("model client should not be called on this path")


# ---------------------------------------------------------------------------
# Turn shape + protocol conformance
# ---------------------------------------------------------------------------


def test_turn_is_a_role_and_text_mapping():
    turn: Turn = {"role": "user", "text": "hello"}
    assert turn["role"] == "user"
    assert turn["text"] == "hello"


def test_mem0memorystore_implements_the_pinned_memorystore_signatures():
    """docs/DESIGN.md §Interfaces pins these four methods exactly."""
    assert list(inspect.signature(MemoryStore.add_turns).parameters) == [
        "self",
        "user_id",
        "session_id",
        "turns",
    ]
    assert list(inspect.signature(MemoryStore.search).parameters) == [
        "self",
        "user_id",
        "query",
        "k",
    ]
    assert list(inspect.signature(MemoryStore.get_preferences).parameters) == ["self", "user_id"]
    assert list(inspect.signature(MemoryStore.add_preference).parameters) == [
        "self",
        "user_id",
        "rule",
    ]

    for name in ("add_turns", "search", "get_preferences", "add_preference"):
        assert callable(getattr(Mem0MemoryStore, name))


# ---------------------------------------------------------------------------
# add_turns: user-namespacing + infer=False + message shape
# ---------------------------------------------------------------------------


def test_add_turns_maps_turns_to_mem0_messages_scoped_by_user_id():
    memory = FakeMem0Memory()
    store = bare_store(memory)

    store.add_turns(
        "alice",
        "session-a",
        [
            {"role": "user", "text": "My favorite language is Rust."},
            {"role": "assistant", "text": "Noted."},
        ],
    )

    assert len(memory.add_calls) == 1
    call = memory.add_calls[0]
    assert call["user_id"] == "alice"
    assert call["messages"] == [
        {"role": "user", "content": "My favorite language is Rust."},
        {"role": "assistant", "content": "Noted."},
    ]


def test_add_turns_uses_infer_false_so_mem0s_llm_is_never_needed():
    """mem0's LLM-based fact extraction (infer=True) needs a real LLM
    provider/API key; T-5 has neither (see null_llm.py). ``add_turns`` must
    always pass ``infer=False`` so mem0 stores turns directly instead.
    """
    memory = FakeMem0Memory()
    store = bare_store(memory)

    store.add_turns("alice", "session-a", [{"role": "user", "text": "hi"}])

    assert memory.add_calls[0]["infer"] is False


def test_add_turns_carries_session_id_as_metadata():
    memory = FakeMem0Memory()
    store = bare_store(memory)

    store.add_turns("alice", "session-xyz", [{"role": "user", "text": "hi"}])

    assert memory.add_calls[0]["metadata"] == {"session_id": "session-xyz"}


def test_add_turns_with_no_turns_does_not_call_mem0():
    memory = FakeMem0Memory()
    store = bare_store(memory)

    store.add_turns("alice", "session-a", [])

    assert memory.add_calls == []


# ---------------------------------------------------------------------------
# search: user-namespacing via filters (not a top-level kwarg) + shaping
# ---------------------------------------------------------------------------


def test_search_scopes_by_user_id_via_filters_not_a_top_level_kwarg():
    """mem0's ``Memory.search()`` rejects a top-level ``user_id=`` argument
    outright (raises ValueError) -- scoping must go through ``filters``.
    """
    memory = FakeMem0Memory()
    store = bare_store(memory)

    store.search("alice", "what language do I like?", k=3)

    assert len(memory.search_calls) == 1
    call = memory.search_calls[0]
    assert call["filters"] == {"user_id": "alice"}
    assert call["top_k"] == 3


def test_search_shapes_each_mem0_match_into_its_own_toolresult():
    memory = FakeMem0Memory(
        search_results=[
            {"memory": "My favorite language is Rust.", "score": 0.9, "created_at": "t1"},
            {"memory": "I live in Austin.", "score": 0.4, "created_at": "t2"},
        ]
    )
    store = bare_store(memory)

    results = store.search("alice", "what language do I like?")

    assert len(results) == 2
    for result in results:
        assert result["source"] == _SOURCE
        assert result["source"] == "memory:mem0"
        assert result["error"] is None
    assert results[0]["data"]["text"] == "My favorite language is Rust."
    assert results[1]["data"]["text"] == "I live in Austin."


def test_search_duplicates_fetched_at_into_data_like_t3_and_t4():
    """Carry-over from T-3/T-4: synthesize_grounded's prompt builder forwards
    only ``results[].source``/``results[].data`` -- fetched_at must be
    copied into ``data`` too, or a recency question is unanswerable.
    """
    memory = FakeMem0Memory(search_results=[{"memory": "fact", "score": 0.9, "created_at": "t1"}])
    store = bare_store(memory)

    [result] = store.search("alice", "query")

    assert result["fetched_at"] == result["data"]["fetched_at"]
    assert result["data"]["fetched_at"]  # non-empty


def test_search_returns_empty_list_when_mem0_finds_nothing():
    """No matches is not an error -- an empty list is exactly what feeds
    synthesize_grounded's structural no-evidence refusal gate, same as any
    other tool returning nothing.
    """
    memory = FakeMem0Memory(search_results=[])
    store = bare_store(memory)

    assert store.search("alice", "anything") == []


def test_search_returns_errored_toolresult_with_empty_data_on_mem0_failure():
    store = bare_store(ExplodingMem0Memory())

    [result] = store.search("alice", "anything")

    assert result["source"] == "memory:mem0"
    assert result["data"] == {}
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# Acceptance #2 (offline half): search() output really is a ToolResult that
# feeds the REAL synthesize_grounded. The DB-backed half (that mem0+pgvector
# actually retrieve the right memory) is proven in test_integration.py.
# ---------------------------------------------------------------------------


def test_search_error_path_feeds_the_refusal_path_not_a_guess():
    store = bare_store(ExplodingMem0Memory())

    results = store.search("alice", "anything")
    answer = synthesize_grounded("anything", results, [], client=ExplodingModelClient())

    assert answer == REFUSAL


def test_search_success_path_reaches_the_real_synthesize_grounded_prompt():
    memory = FakeMem0Memory(
        search_results=[
            {"memory": "My favorite language is Rust.", "score": 0.9, "created_at": "t1"}
        ]
    )
    store = bare_store(memory)

    results = store.search("alice", "what language do I like?")
    client = RecordingModelClient(reply="You like Rust.")
    answer = synthesize_grounded(
        "what language do I like?", results, [], model="stub-model", client=client
    )

    assert client.calls == 1
    assert "My favorite language is Rust." in client.last_user
    assert '"source": "memory:mem0"' in client.last_user
    assert answer == "You like Rust."


# ---------------------------------------------------------------------------
# preferences: Mem0MemoryStore delegates to jarvis.memory.preferences with
# the right user_id/rule. The real Postgres round-trip is integration-only.
# ---------------------------------------------------------------------------


def test_get_preferences_delegates_with_user_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jarvis.memory.store.preferences.get_preferences",
        lambda conninfo, user_id: calls.append((conninfo, user_id)) or ["rule-a"],
    )
    store = bare_store(FakeMem0Memory())
    store._database_url = "postgresql://example/db"

    result = store.get_preferences("alice")

    assert result == ["rule-a"]
    assert calls == [("postgresql://example/db", "alice")]


def test_add_preference_delegates_with_user_id_and_rule(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "jarvis.memory.store.preferences.add_preference",
        lambda conninfo, user_id, rule: calls.append((conninfo, user_id, rule)),
    )
    store = bare_store(FakeMem0Memory())
    store._database_url = "postgresql://example/db"

    store.add_preference("alice", "Always use metric units.")

    assert calls == [("postgresql://example/db", "alice", "Always use metric units.")]
