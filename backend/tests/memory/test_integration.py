"""Integration tests for jarvis.memory (T-5) against a REAL Postgres+pgvector
(this project's backend/docker-compose.yml service). These are what
actually prove acceptance items 1, 3, and 4 -- an in-memory fake proves
nothing about mem0 or pgvector really working.

Every test here depends on the ``require_db`` fixture (conftest.py), which
SKIPS with an explicit reason (naming ``docker compose up -d``) if the
database isn't reachable right now, rather than failing -- CI runs
``uv run pytest -q`` with no Postgres present (SPEC C7 forbids adding CI
services). If the database IS reachable and a test genuinely fails, it
fails for real.
"""

from __future__ import annotations

import uuid

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.memory import Mem0MemoryStore

_COLLECTION = "jarvis_memory_integration_test"

_SHELLFISH_TEXT = "I'm allergic to shellfish"
_FLIGHT_TEXT = "My flight to Tokyo departs from gate 14 on the 14th of next month."


class RecordingModelClient:
    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


# ---------------------------------------------------------------------------
# Acceptance #1: fact stored under user U in session A is returned by
# search(U, ...) in a NEW session B.
# ---------------------------------------------------------------------------


def test_round_trip_fact_stored_in_one_session_is_found_in_a_new_session(
    require_db, unique_user_id
):
    # Session A: one Mem0MemoryStore instance, one add_turns call.
    store_session_a = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store_session_a.add_turns(
        unique_user_id,
        "session-a",
        [{"role": "user", "text": "My favorite programming language is Rust."}],
    )

    # Session B: a brand-new store instance (nothing shared with session A
    # in-process except the same Postgres database) -- proves persistence,
    # not just an in-memory cache surviving within one object.
    store_session_b = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    results = store_session_b.search(unique_user_id, "What is my favorite programming language?")

    assert len(results) >= 1
    assert any("Rust" in r["data"]["text"] for r in results)
    assert all(r["source"] == "memory:mem0" for r in results)
    assert all(r["error"] is None for r in results)


# ---------------------------------------------------------------------------
# Paraphrase recall -- the defect an earlier version of this test suite
# missed. That version's embedder was a hand-rolled hashed bag-of-words
# stand-in (no real ML model, stdlib only), which only matched on shared
# vocabulary between query and stored text. It made the round-trip test
# above pass, but for the wrong reason: live against this same database, it
# matched "I'm allergic to shellfish" against "what foods should I avoid?"
# only via a hash collision between "i'm" and "what", and scored 0.0 (no
# match at all) for "My flight to Tokyo is on the 14th" against "when am I
# traveling?" -- a real paraphrase with almost no shared vocabulary. SPEC
# R3's own worked example ("yesterday I asked about X -- remind me") is
# exactly this paraphrase shape, so that embedder did not satisfy R3.
#
# The embedder is now mem0's real ``fastembed`` provider running
# BAAI/bge-small-en-v1.5 (see store.py). These tests are what actually
# catch a regression back to keyword-only matching: they deliberately
# share little to no vocabulary between the stored text and the query, and
# assert the RIGHT memory ranks first -- not merely that something came
# back.
# ---------------------------------------------------------------------------


def test_round_trip_recalls_a_paraphrase_sharing_almost_no_vocabulary(require_db, unique_user_id):
    """The exact case that broke the hashed bag-of-words stand-in: no shared
    content words between "I'm allergic to shellfish" and "what foods
    should I avoid?" other than function words.
    """
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store.add_turns(unique_user_id, "session-a", [{"role": "user", "text": _SHELLFISH_TEXT}])
    store.add_turns(unique_user_id, "session-a", [{"role": "user", "text": _FLIGHT_TEXT}])

    results = store.search(unique_user_id, "What foods should I avoid?", k=5)

    assert results, "expected the paraphrased query to recall the shellfish memory"
    assert "shellfish" in results[0]["data"]["text"].lower(), (
        f"expected the shellfish memory to rank first, got: {[r['data']['text'] for r in results]}"
    )
    assert results[0]["data"]["score"] > 0.5


def test_round_trip_recalls_a_second_paraphrase_about_travel(require_db, unique_user_id):
    """The second case from the same adversarial review: "when am I
    traveling?" shares almost no vocabulary with "My flight to Tokyo...".
    The hashed embedder scored this pair 0.0 (no match at all).
    """
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store.add_turns(unique_user_id, "session-a", [{"role": "user", "text": _SHELLFISH_TEXT}])
    store.add_turns(unique_user_id, "session-a", [{"role": "user", "text": _FLIGHT_TEXT}])

    query = "When am I traveling, and which gate do I leave from?"
    results = store.search(unique_user_id, query, k=5)

    assert results, "expected the paraphrased query to recall the travel memory"
    assert "tokyo" in results[0]["data"]["text"].lower(), (
        f"expected the travel memory to rank first, got: {[r['data']['text'] for r in results]}"
    )
    assert results[0]["data"]["score"] > 0.5


def test_search_does_not_surface_a_stored_memory_for_an_unrelated_query(require_db, unique_user_id):
    """Negative case: a query unrelated to anything stored should not
    surface a stored memory above search()'s threshold.

    Uses a reasonably specific stored fact and an unambiguously unrelated
    query -- see store.py's search() docstring for the honest caveat that
    very short/generic stored text can still score close to the boundary
    against an unrelated query with this size of local embedding model.
    """
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store.add_turns(unique_user_id, "session-a", [{"role": "user", "text": _FLIGHT_TEXT}])

    query = "What is the boiling point of water in Celsius?"
    results = store.search(unique_user_id, query, k=5)

    assert results == []


# ---------------------------------------------------------------------------
# Acceptance #2 (integration half): the real round-tripped search() output
# feeds the REAL synthesize_grounded -- proves mem0/pgvector data (not just
# a hand-built fixture) reaches the model prompt. The offline half (that our
# shaping code wires correctly) is in test_contract.py.
# ---------------------------------------------------------------------------


def test_real_search_results_feed_the_real_synthesize_grounded(require_db, unique_user_id):
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store.add_turns(
        unique_user_id,
        "session-a",
        [{"role": "user", "text": "My favorite programming language is Rust."}],
    )

    results = store.search(unique_user_id, "What is my favorite programming language?")
    assert results, "expected at least one real memory match"

    client = RecordingModelClient(reply="You like Rust.")
    answer = synthesize_grounded(
        "What is my favorite programming language?",
        results,
        [],
        model="stub-model",
        client=client,
    )

    assert client.calls == 1
    assert "Rust" in client.last_user
    assert '"source": "memory:mem0"' in client.last_user
    assert answer == "You like Rust."


# ---------------------------------------------------------------------------
# Acceptance #3: isolation primitive -- search(U2) never returns U1's rows.
# ---------------------------------------------------------------------------


def test_search_never_returns_another_users_rows(require_db):
    user_1 = f"test-user-{uuid.uuid4().hex}"
    user_2 = f"test-user-{uuid.uuid4().hex}"
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)

    store.add_turns(
        user_1, "session-1", [{"role": "user", "text": "My favorite programming language is Rust."}]
    )
    store.add_turns(
        user_2,
        "session-2",
        [{"role": "user", "text": "My favorite programming language is Python."}],
    )

    user_2_results = store.search(user_2, "What is my favorite programming language?", k=10)

    assert user_2_results, "expected user_2 to find their own memory"
    for result in user_2_results:
        assert "Rust" not in result["data"].get("text", "")
    assert any("Python" in r["data"].get("text", "") for r in user_2_results)

    # And the reverse, for good measure.
    user_1_results = store.search(user_1, "What is my favorite programming language?", k=10)
    for result in user_1_results:
        assert "Python" not in result["data"].get("text", "")
    assert any("Rust" in r["data"].get("text", "") for r in user_1_results)


# ---------------------------------------------------------------------------
# Acceptance #4: preferences table migrated; get/add_preference round-trip.
# ---------------------------------------------------------------------------


def test_preferences_table_is_migrated_and_round_trips(require_db, unique_user_id):
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)

    assert store.get_preferences(unique_user_id) == []

    store.add_preference(unique_user_id, "Always use metric units.")
    store.add_preference(unique_user_id, "Prefer concise answers.")

    assert store.get_preferences(unique_user_id) == [
        "Always use metric units.",
        "Prefer concise answers.",
    ]


def test_preferences_table_has_the_pinned_columns(require_db):
    """docs/DESIGN.md §Data models pins the shape exactly:
    ``preferences(user_id text, rule text, created_at timestamptz)``.
    """
    import psycopg

    # Constructing a store ensures the migration has run.
    Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)

    with psycopg.connect(require_db) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'preferences'
            ORDER BY ordinal_position
            """
        )
        columns = {name: dtype for name, dtype in cur.fetchall()}

    assert columns["user_id"] == "text"
    assert columns["rule"] == "text"
    assert columns["created_at"] == "timestamp with time zone"


def test_preferences_are_isolated_per_user(require_db):
    user_1 = f"test-user-{uuid.uuid4().hex}"
    user_2 = f"test-user-{uuid.uuid4().hex}"
    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)

    store.add_preference(user_1, "user-1 only rule")

    assert store.get_preferences(user_2) == []
    assert store.get_preferences(user_1) == ["user-1 only rule"]
