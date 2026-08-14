"""Acceptance #3 (T-10): a preference stated by voice in session 1 is
honored in session 2, exercised via the prefs INJECTION PATH directly --
not through STT/TTS (Cartesia is down; T-10's non-goals explicitly say this
acceptance item must not depend on working TTS).

"Stated by voice" is stood in for by the same call the pipeline's tool
layer would make once it decides to persist a standing preference:
``MemoryStore.add_preference(user_id, rule)`` (backend/src/jarvis/memory/
preferences.py, T-5). What THIS ticket adds and proves is everything
downstream of that write actually reaching a new session:

    Postgres `preferences` table
      -> LazyMemoryStore.preferences(user_id)   (jarvis.pipeline.tools_wiring,
                                                   the real prefs_getter wired
                                                   into livekit_tools -- T-16)
      -> synthesize_grounded(..., prefs=...)     (jarvis.llm.synthesis)
      -> the actual prompt sent to the model

"Session 1" / "session 2" are two independent ``LazyMemoryStore`` instances
against the same Postgres, exactly like tests/memory/test_integration.py's
new-instance-per-session discipline -- nothing here is a same-process cache
carrying the preference forward for the wrong reason.

Needs a real reachable Postgres+pgvector -- see conftest.py's ``require_db``.
"""

from __future__ import annotations

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.llm.types import ToolResult
from jarvis.pipeline.tools_wiring import LazyMemoryStore

_PREFERENCE = "Always give answers in metric units."


class RecordingModelClient:
    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


def _fact_result(text: str) -> ToolResult:
    return ToolResult(
        source="memory:mem0",
        fetched_at="2026-01-01T00:00:00Z",
        data={"text": text},
        error=None,
    )


def test_preference_stated_in_session_1_is_honored_in_session_2(require_db, clerk_user_id: str):
    # --- "Session 1": the standing preference gets captured/persisted. ---
    session_1 = LazyMemoryStore(require_db)
    store = session_1.get()
    assert store is not None, "memory store must build successfully against a reachable Postgres"
    store.add_preference(clerk_user_id, _PREFERENCE)

    # --- "Session 2": a BRAND NEW LazyMemoryStore -- nothing shared
    # in-process with session 1 except the same Postgres database. ---
    session_2 = LazyMemoryStore(require_db)
    prefs = session_2.preferences(clerk_user_id)

    assert _PREFERENCE in prefs, "the prefs injection path must recall session 1's preference"

    # Feed it through the REAL synthesize_grounded, exactly as
    # jarvis.pipeline.tools_wiring wires prefs_getter -> synthesize_grounded
    # for a real turn -- proves it reaches the actual model prompt, not just
    # that the DB round-trips.
    client = RecordingModelClient(reply="It is 21 degrees Celsius outside.")
    answer = synthesize_grounded(
        "What's the temperature outside?",
        [_fact_result("The temperature outside is 70 degrees Fahrenheit.")],
        prefs,
        model="stub-model",
        client=client,
    )

    assert client.calls == 1
    assert _PREFERENCE in client.last_user, "the preference must reach the model's prompt verbatim"
    assert answer == "It is 21 degrees Celsius outside."


def test_preferences_are_isolated_per_clerk_user(require_db):
    user_1 = "user_prefs_isolation_1"
    user_2 = "user_prefs_isolation_2"

    session_1 = LazyMemoryStore(require_db)
    session_1.get().add_preference(user_1, "user-1-only rule")

    session_2 = LazyMemoryStore(require_db)
    assert session_2.preferences(user_2) == []
    assert "user-1-only rule" in session_2.preferences(user_1)


def test_a_user_with_no_stored_preference_gets_no_injected_prefs(require_db, clerk_user_id: str):
    """Negative case: an unrelated user's session must not see prefs they
    never stated -- the injection path degrades to "(none)", not a guess.
    """
    session = LazyMemoryStore(require_db)
    assert session.preferences(clerk_user_id) == []

    client = RecordingModelClient(reply="It is sunny.")
    synthesize_grounded(
        "What's the weather?",
        [_fact_result("It is sunny and 70F.")],
        session.preferences(clerk_user_id),
        model="stub-model",
        client=client,
    )
    assert "(none)" in client.last_user
