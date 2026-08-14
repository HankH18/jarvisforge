"""Acceptance #2 (T-10): user A's stored fact is absent from user B's
recall, where A/B are real Clerk-verified identities (the same ``sub`` a
verified JWT would hand ``/api/token``), namespacing mem0 by Clerk user id.

``Mem0MemoryStore.search``/``add_turns`` already take ``user_id`` as their
namespace key (backend/src/jarvis/memory/store.py) and mem0's own
``filters={"user_id": user_id}`` is what does the isolation -- this test
proves that primitive holds when the namespace key is specifically a
verified-JWT-derived Clerk id, going through ``verify_clerk_session_token``
first rather than a bare fixture string, so the whole
verify-JWT -> namespace-by-sub path is exercised end to end.

Both users are asked about the SAME topic (favorite programming language),
so a version of ``search`` that forgot to filter by ``user_id`` would find
BOTH facts (they are semantically close) and this test would catch it --
querying about unrelated topics would not exercise that failure mode.

Needs a real reachable Postgres+pgvector -- see conftest.py's ``require_db``
(loud-skip-unless-reachable, same pattern as backend/tests/memory, T-23).
"""

from __future__ import annotations

from clerk_backend_api.security import VerifyTokenOptions
from conftest import RSAKeyPair, mint_clerk_token

from jarvis.auth.clerk import verify_clerk_session_token
from jarvis.memory import Mem0MemoryStore

_COLLECTION = "jarvis_memory_multiuser_test"
_QUESTION = "What is my favorite programming language?"


def _clerk_identity(signing_key: RSAKeyPair, sub: str) -> str:
    """Mint a real signed token for ``sub`` and run it through the SAME
    verification the /api/token endpoint uses, returning the resulting
    namespace key -- not just the raw ``sub`` string, so this test proves
    the verified-identity -> memory-namespace wiring, not just mem0 itself.
    """
    token = mint_clerk_token(signing_key=signing_key, sub=sub)
    options = VerifyTokenOptions(jwt_key=signing_key.public_pem)
    return verify_clerk_session_token(token, options=options).user_id


def test_user_as_fact_is_absent_from_user_bs_recall(require_db, clerk_signing_key: RSAKeyPair):
    user_a = _clerk_identity(clerk_signing_key, "user_alice_isolation_test")
    user_b = _clerk_identity(clerk_signing_key, "user_bob_isolation_test")

    store = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)
    store.add_turns(
        user_a, "session-a", [{"role": "user", "text": "My favorite programming language is Rust."}]
    )
    store.add_turns(
        user_b,
        "session-b",
        [{"role": "user", "text": "My favorite programming language is Python."}],
    )

    # New store instance for the recall -- proves persistence via Postgres,
    # not an in-memory cache surviving within one object (same discipline
    # as tests/memory/test_integration.py).
    store_b = Mem0MemoryStore(database_url=require_db, collection_name=_COLLECTION)

    b_results = store_b.search(user_b, _QUESTION, k=10)
    assert any("Python" in r["data"].get("text", "") for r in b_results), (
        "user B must be able to recall their OWN fact"
    )
    assert not any("Rust" in r["data"].get("text", "") for r in b_results), (
        "user B's recall must never surface user A's stored fact"
    )

    a_results = store_b.search(user_a, _QUESTION, k=10)
    assert any("Rust" in r["data"].get("text", "") for r in a_results), (
        "user A must be able to recall their OWN fact"
    )
    assert not any("Python" in r["data"].get("text", "") for r in a_results), (
        "user A's recall must never surface user B's stored fact"
    )
