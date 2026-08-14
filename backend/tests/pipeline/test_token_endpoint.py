"""POST /api/token: pinned request/response shape (docs/DESIGN.md §Interfaces).

Acceptance #2: decode the JWT and assert the room/identity grants are
actually right -- not just that the response has a "token" key.

T-10 UPDATE (human-authorized cross-scope edit -- see jarvis.auth.clerk's
module docstring for the full reasoning): T-10 made /api/token's auth
CONDITIONAL on server configuration rather than unconditional, specifically
so this file's contract -- T-1's original, ANONYMOUS-mode behavior (trust
the client-supplied identity) -- keeps holding when Clerk is not
configured, which is the current Fly deployment's actual state (T-9's base
MVP demo, no Clerk credentials set). ``_client`` below now explicitly
clears CLERK_SECRET_KEY/CLERK_JWT_KEY so these three tests describe
anonymous mode deterministically regardless of the ambient environment
(e.g. a shell that has sourced repo-root .env, which does set
CLERK_SECRET_KEY, would otherwise flip these into authenticated mode and
break them for a reason unrelated to what they're testing). Their
assertions are otherwise UNCHANGED from before T-10 -- this is not a
weakening, it's pinning down the mode they were always implicitly written
against. The two new tests at the bottom bind the OTHER mode: the same
endpoint, same file, 401s when Clerk IS configured.
"""

from __future__ import annotations

import jwt
from fastapi.testclient import TestClient

from jarvis.server.app import app

API_KEY = "test-key"
API_SECRET = "test-secret-at-least-this-long"
LK_URL = "wss://example.livekit.cloud"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", API_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", LK_URL)
    # Force ANONYMOUS mode regardless of the ambient environment (see
    # module docstring) -- these three tests describe T-1's pre-T-10,
    # unauthenticated contract specifically.
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_JWT_KEY", raising=False)
    return TestClient(app)


def test_token_endpoint_returns_pinned_shape(monkeypatch):
    client = _client(monkeypatch)

    resp = client.post("/api/token", json={"identity": "hank", "room": "demo-room"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"token", "url"}
    assert isinstance(body["token"], str) and body["token"]
    assert body["url"] == LK_URL


def test_token_grants_match_the_request(monkeypatch):
    client = _client(monkeypatch)

    resp = client.post("/api/token", json={"identity": "hank", "room": "demo-room"})
    token = resp.json()["token"]

    claims = jwt.decode(token, API_SECRET, algorithms=["HS256"], issuer=API_KEY)
    assert claims["sub"] == "hank"
    assert claims["video"]["room"] == "demo-room"
    assert claims["video"]["roomJoin"] is True


def test_token_generates_a_room_when_none_given(monkeypatch):
    client = _client(monkeypatch)

    resp = client.post("/api/token", json={"identity": "hank", "room": None})
    token = resp.json()["token"]

    claims = jwt.decode(token, API_SECRET, algorithms=["HS256"], issuer=API_KEY)
    assert claims["video"]["room"]  # non-empty, server picked one
    assert claims["sub"] == "hank"


# ---------------------------------------------------------------------------
# T-10 conditional auth: the OTHER mode, bound in this same file/endpoint.
# Full authenticated-mode coverage (forged/tampered/expired tokens, the
# client-supplied-identity-is-ignored proof, the no-client-controlled-mode-
# switch proof) lives in backend/tests/multiuser/test_auth_jwt.py -- these
# two just confirm the endpoint this file otherwise tests unauthenticated
# actually enforces auth once Clerk is configured, so nobody reading only
# this file could conclude the gate never fires.
# ---------------------------------------------------------------------------


def test_token_endpoint_requires_auth_when_clerk_secret_key_is_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", API_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", LK_URL)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.delenv("CLERK_JWT_KEY", raising=False)
    client = TestClient(app)

    resp = client.post("/api/token", json={"identity": "hank", "room": "demo-room"})

    assert resp.status_code == 401


def test_token_endpoint_requires_auth_when_clerk_jwt_key_is_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", API_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", LK_URL)
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    # Only presence is checked here (no request ever presents a token in
    # this test) -- doesn't need to be a real PEM.
    monkeypatch.setenv("CLERK_JWT_KEY", "not-a-real-key-only-presence-matters")
    client = TestClient(app)

    resp = client.post("/api/token", json={"identity": "hank", "room": "demo-room"})

    assert resp.status_code == 401
