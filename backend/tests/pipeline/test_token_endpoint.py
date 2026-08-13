"""POST /api/token: pinned request/response shape (docs/DESIGN.md §Interfaces).

Acceptance #2: decode the JWT and assert the room/identity grants are
actually right -- not just that the response has a "token" key.
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
