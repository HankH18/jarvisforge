"""Acceptance #1 (T-10): /api/token requires a Clerk JWT, verified against
its signing key -- not merely decoded -- and identity comes ONLY from that
verified token, never from anything client-supplied.

Every test here goes through the real FastAPI app (backend/src/jarvis/server
/app.py) and the real ``clerk_backend_api.security.verify_token`` signature
check (see conftest.py's module docstring for why "networkless" mode is
used and why that is not a mock). No test in this file needs Postgres.
"""

from __future__ import annotations

import base64
import json
import uuid

import jwt as pyjwt
import pytest
from clerk_backend_api.security import TokenVerificationError
from conftest import RSAKeyPair, mint_clerk_token

from jarvis.auth.clerk import verify_clerk_session_token


def _decode_livekit_identity(token: str, *, secret: str) -> str:
    """Decode the LiveKit access token /api/token returned (HS256, signed
    with LIVEKIT_API_SECRET) and return its ``sub`` -- LiveKit's own
    identity claim (confirmed against the installed livekit-api package:
    ``AccessToken.with_identity`` sets JWT claim ``sub``).
    """
    payload = pyjwt.decode(token, secret, algorithms=["HS256"])
    return payload["sub"]


# ---------------------------------------------------------------------------
# 401 on missing / malformed auth
# ---------------------------------------------------------------------------


def test_missing_authorization_header_returns_401(client):
    resp = client.post("/api/token", json={"identity": "whoever", "room": None})
    assert resp.status_code == 401


def test_malformed_authorization_header_returns_401(client):
    resp = client.post(
        "/api/token",
        json={"identity": "whoever", "room": None},
        headers={"Authorization": "NotBearer sometoken"},
    )
    assert resp.status_code == 401


def test_empty_bearer_token_returns_401(client):
    resp = client.post(
        "/api/token",
        json={"identity": "whoever", "room": None},
        headers={"Authorization": "Bearer "},
    )
    assert resp.status_code == 401


def test_client_cannot_force_anonymous_mode_via_request(client):
    """Conditional auth (human-authorized addition after initial T-10
    landing): whether /api/token requires a verified token is a
    SERVER-CONFIG-ONLY switch -- ``jarvis.auth.clerk.clerk_auth_enabled()``,
    read from CLERK_SECRET_KEY/CLERK_JWT_KEY only. This fixture's app is
    configured with CLERK_JWT_KEY (see conftest.py's ``app_env``), i.e. an
    "authenticated" deployment. Every plausible way a client might try to
    signal "treat me as anonymous" -- a made-up header, an extra body
    field, both at once -- must still 401 with no valid bearer token: none
    of them are ever consulted to pick the mode.
    """
    attempts = [
        # No override attempt at all -- baseline.
        {"json": {"identity": "whoever", "room": None}},
        # A made-up header hinting at legacy/anonymous mode.
        {
            "json": {"identity": "whoever", "room": None},
            "headers": {"X-Jarvis-Auth-Mode": "anonymous"},
        },
        {
            "json": {"identity": "whoever", "room": None},
            "headers": {"X-Auth-Required": "false"},
        },
        # Extra body fields hinting at the same -- TokenRequest doesn't
        # declare them (pydantic ignores unknown fields by default), and
        # resolve_identity never reads the body at all, but prove it
        # rather than assume it.
        {"json": {"identity": "whoever", "room": None, "anonymous": True, "mode": "anonymous"}},
        # A query-string attempt too.
        {"json": {"identity": "whoever", "room": None}, "params": {"mode": "anonymous"}},
    ]

    for kwargs in attempts:
        resp = client.post("/api/token", **kwargs)
        assert resp.status_code == 401, (
            f"expected 401 for attempt {kwargs!r}, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# A valid, correctly-signed token succeeds, and identity is server-derived
# ---------------------------------------------------------------------------


def test_valid_token_succeeds_and_identity_comes_from_the_token(
    client, app_env, clerk_signing_key: RSAKeyPair, clerk_user_id: str
):
    token = mint_clerk_token(signing_key=clerk_signing_key, sub=clerk_user_id)

    resp = client.post(
        "/api/token",
        json={"identity": clerk_user_id, "room": "test-room"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    identity = _decode_livekit_identity(body["token"], secret=app_env["livekit_api_secret"])
    assert identity == clerk_user_id


def test_client_supplied_identity_in_body_is_ignored(
    client, app_env, clerk_signing_key: RSAKeyPair, clerk_user_id: str
):
    """The request body claims to be a totally different user; the server
    must still mint a LiveKit token for the VERIFIED Clerk identity.
    """
    forged_body_identity = "user_someone_else_entirely"
    token = mint_clerk_token(signing_key=clerk_signing_key, sub=clerk_user_id)

    resp = client.post(
        "/api/token",
        json={"identity": forged_body_identity, "room": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    identity = _decode_livekit_identity(body["token"], secret=app_env["livekit_api_secret"])
    assert identity == clerk_user_id
    assert identity != forged_body_identity


# ---------------------------------------------------------------------------
# Forged / tampered / expired signatures are REJECTED -- proves this is
# real verification, not a bare decode.
# ---------------------------------------------------------------------------


def test_token_signed_by_a_different_key_is_rejected(
    client, clerk_user_id: str, attacker_signing_key: RSAKeyPair
):
    """An attacker who mints their own well-formed RS256 JWT (their own
    keypair, plausible claims, even the victim's ``sub``) cannot pass
    verification: the app only trusts signatures from ``clerk_signing_key``
    (wired into CLERK_JWT_KEY by the ``app_env`` fixture), not from an
    arbitrary key. If the server only decoded the token instead of
    verifying its signature, this would incorrectly succeed.
    """
    forged_token = mint_clerk_token(signing_key=attacker_signing_key, sub=clerk_user_id)

    resp = client.post(
        "/api/token",
        json={"identity": clerk_user_id, "room": None},
        headers={"Authorization": f"Bearer {forged_token}"},
    )

    assert resp.status_code == 401


def test_tampered_payload_with_original_signature_is_rejected(
    client, clerk_signing_key: RSAKeyPair, clerk_user_id: str
):
    """Classic cut-and-paste forgery: take a validly-signed token and
    change the payload's ``sub`` (to impersonate another user) WITHOUT
    re-signing. A bare decode would accept this; real verification must not.
    """
    token = mint_clerk_token(signing_key=clerk_signing_key, sub=clerk_user_id)
    header_b64, payload_b64, signature_b64 = token.split(".")

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = "user_attacker_takeover"
    tampered_payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )

    tampered_token = f"{header_b64}.{tampered_payload_b64}.{signature_b64}"

    resp = client.post(
        "/api/token",
        json={"identity": clerk_user_id, "room": None},
        headers={"Authorization": f"Bearer {tampered_token}"},
    )

    assert resp.status_code == 401


def test_unsigned_alg_none_token_is_rejected(client, clerk_user_id: str):
    """The classic "alg: none" JWT vulnerability: an unsigned token must
    not be accepted just because it decodes to plausible-looking claims.
    """
    unsigned_token = pyjwt.encode({"sub": clerk_user_id}, key="", algorithm="none")

    resp = client.post(
        "/api/token",
        json={"identity": clerk_user_id, "room": None},
        headers={"Authorization": f"Bearer {unsigned_token}"},
    )

    assert resp.status_code == 401


def test_expired_token_is_rejected(client, clerk_signing_key: RSAKeyPair, clerk_user_id: str):
    token = mint_clerk_token(signing_key=clerk_signing_key, sub=clerk_user_id, expired=True)

    resp = client.post(
        "/api/token",
        json={"identity": clerk_user_id, "room": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Unit-level: verify_clerk_session_token itself (not just via HTTP)
# ---------------------------------------------------------------------------


def test_verify_clerk_session_token_rejects_forged_signature_directly(
    clerk_signing_key: RSAKeyPair, attacker_signing_key: RSAKeyPair, clerk_user_id: str, monkeypatch
):
    from clerk_backend_api.security import VerifyTokenOptions

    forged_token = mint_clerk_token(signing_key=attacker_signing_key, sub=clerk_user_id)
    options = VerifyTokenOptions(jwt_key=clerk_signing_key.public_pem)

    with pytest.raises(TokenVerificationError):
        verify_clerk_session_token(forged_token, options=options)


def test_verify_clerk_session_token_accepts_a_correctly_signed_token(
    clerk_signing_key: RSAKeyPair, clerk_user_id: str
):
    from clerk_backend_api.security import VerifyTokenOptions

    token = mint_clerk_token(signing_key=clerk_signing_key, sub=clerk_user_id)
    options = VerifyTokenOptions(jwt_key=clerk_signing_key.public_pem)

    identity = verify_clerk_session_token(token, options=options)
    assert identity.user_id == clerk_user_id


# ---------------------------------------------------------------------------
# P0 SECURITY FIX: room MEMBERSHIP, not just token identity.
#
# Before this fix, /api/token verified WHO you are but issued room_join +
# publish + subscribe grants for WHATEVER `req.room` a caller supplied --
# any two authenticated users could name the same room string and both
# receive full grants for it, no matter who "owns" it. That is a
# cross-tenant transcript/audio leak and breaks T-10's whole
# membership/isolation story.
#
# Fix: in authenticated mode the server derives the room name itself from
# the VERIFIED identity (HMAC-keyed on LIVEKIT_API_SECRET) and never reads
# `req.room` at all -- symmetric with how `req.identity` was already
# ignored. These tests must fail against the pre-fix endpoint (proven via
# RED-before/GREEN-after in the fix's report) and must keep passing after.
# ---------------------------------------------------------------------------


def _mint_user(signing_key: RSAKeyPair) -> tuple[str, str]:
    """A fresh (user_id, valid bearer token) pair for a distinct user."""
    user_id = f"user_{uuid.uuid4().hex}"
    return user_id, mint_clerk_token(signing_key=signing_key, sub=user_id)


def test_authenticated_user_b_cannot_obtain_a_grant_for_user_as_room(
    client, app_env, clerk_signing_key: RSAKeyPair
):
    """User A joins/creates a room (server picks it -- see the room-
    derivation tests below for how). User B, a SEPARATE, legitimately
    authenticated user who merely knows or guesses A's room name, asks
    /api/token for that same room. B must NOT receive a token granting
    room_join/publish/subscribe for A's room -- the whole point of this
    fix. B still gets 200 (B is a valid user, just not a member of A's
    room) but the room in B's grant must differ from A's.
    """
    user_a, token_a = _mint_user(clerk_signing_key)
    user_b, token_b = _mint_user(clerk_signing_key)

    resp_a = client.post(
        "/api/token",
        json={"identity": user_a, "room": None},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_a.status_code == 200
    room_a = pyjwt.decode(
        resp_a.json()["token"], app_env["livekit_api_secret"], algorithms=["HS256"]
    )["video"]["room"]

    # B explicitly asks for A's room by name -- the attack this fix closes.
    resp_b = client.post(
        "/api/token",
        json={"identity": user_b, "room": room_a},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_b.status_code == 200
    room_b = pyjwt.decode(
        resp_b.json()["token"], app_env["livekit_api_secret"], algorithms=["HS256"]
    )["video"]["room"]

    assert room_b != room_a, (
        "user B obtained a LiveKit grant for user A's room just by naming it -- "
        "cross-tenant room membership is NOT enforced"
    )


def test_authenticated_room_ignores_client_supplied_room_entirely(
    client, clerk_signing_key: RSAKeyPair
):
    """Same identity, two different client-supplied `room` values (including
    None): the granted room must be identical both times -- proof the
    server never consults `req.room` once authenticated, it always derives
    the room itself.
    """
    user_id, token = _mint_user(clerk_signing_key)

    resp_1 = client.post(
        "/api/token",
        json={"identity": user_id, "room": "attacker-chosen-room"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp_2 = client.post(
        "/api/token",
        json={"identity": user_id, "room": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_1.status_code == 200
    assert resp_2.status_code == 200

    room_1 = pyjwt.decode(resp_1.json()["token"], options={"verify_signature": False})["video"][
        "room"
    ]
    room_2 = pyjwt.decode(resp_2.json()["token"], options={"verify_signature": False})["video"][
        "room"
    ]
    assert room_1 == room_2, "client-supplied `room` changed the grant -- it must be ignored"


def test_authenticated_room_is_stable_for_the_same_user_across_requests(
    client, clerk_signing_key: RSAKeyPair
):
    """Reconnecting (refresh, a second tab/device) must land the same user
    back in the SAME private room, not a fresh random one each time --
    otherwise a signed-in user could never rejoin their own in-progress
    session.
    """
    user_id = f"user_{uuid.uuid4().hex}"
    token_1 = mint_clerk_token(signing_key=clerk_signing_key, sub=user_id)
    token_2 = mint_clerk_token(signing_key=clerk_signing_key, sub=user_id)

    resp_1 = client.post(
        "/api/token",
        json={"identity": user_id, "room": None},
        headers={"Authorization": f"Bearer {token_1}"},
    )
    resp_2 = client.post(
        "/api/token",
        json={"identity": user_id, "room": None},
        headers={"Authorization": f"Bearer {token_2}"},
    )

    room_1 = pyjwt.decode(resp_1.json()["token"], options={"verify_signature": False})["video"][
        "room"
    ]
    room_2 = pyjwt.decode(resp_2.json()["token"], options={"verify_signature": False})["video"][
        "room"
    ]
    assert room_1 == room_2


def test_authenticated_room_survives_malformed_or_absent_client_room(
    client, clerk_signing_key: RSAKeyPair
):
    """Absent, empty, or bizarre `room` values must never error and must
    all resolve to the same server-derived room for a given identity --
    there is no client-controlled room path left to be malformed.
    """
    user_id, token = _mint_user(clerk_signing_key)
    headers = {"Authorization": f"Bearer {token}"}

    variants = [None, "", "   ", "../../etc/passwd", "a" * 5000, "🙂/weird unicode room"]
    rooms = set()
    for room in variants:
        resp = client.post(
            "/api/token", json={"identity": user_id, "room": room}, headers=headers
        )
        assert resp.status_code == 200, f"room={room!r} unexpectedly failed: {resp.text}"
        claims = pyjwt.decode(resp.json()["token"], options={"verify_signature": False})
        rooms.add(claims["video"]["room"])

    assert len(rooms) == 1, f"malformed/absent room values produced different grants: {rooms}"
