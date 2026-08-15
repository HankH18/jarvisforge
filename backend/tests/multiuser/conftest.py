"""Shared fixtures for tests/multiuser (T-10).

Two layers, mirroring backend/tests/memory's split:

* JWT/auth tests (test_auth_jwt.py) run unconditionally, fully offline --
  no Postgres, no real network call to Clerk. They exercise the REAL
  ``clerk_backend_api.security.verify_token`` signature-verification code
  path (see jarvis/auth/clerk.py's module docstring) in Clerk's supported
  "networkless" mode: a real RSA keypair is minted per test session, its
  PEM public key is fed to the app via ``CLERK_JWT_KEY``, and tokens are
  signed with the matching private key (or, for the forged-signature test,
  a DIFFERENT, unrelated private key). Nothing about signature verification
  is mocked -- only the network hop to fetch Clerk's JWKS is skipped, which
  is itself a real, documented Clerk verification mode.

* Memory/preferences tests (test_memory_isolation.py,
  test_prefs_injection.py) need a real reachable Postgres+pgvector, exactly
  like backend/tests/memory's integration suite -- ``require_db`` below is
  the same loud-skip-unless-reachable pattern from that suite (T-23),
  duplicated here rather than imported cross-package since this ticket's
  file scope does not include backend/tests/memory/**.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

# Must run before any ``import mem0`` anywhere in this process -- same
# constraint as backend/tests/memory/conftest.py (module-level constant
# read once at mem0.memory.telemetry import time).
os.environ.setdefault("MEM0_TELEMETRY", "False")

_DEFAULT_TEST_DATABASE_URL = "postgresql://jarvis:jarvis_dev_local_only@localhost:55432/jarvis"
_REQUIRE_DB_ENV_VAR = "JARVIS_MEMORY_TESTS_REQUIRE_DB"
_SKIP_TAG = "JARVIS_MULTIUSER_DB_UNREACHABLE"


def _resolve_database_url() -> str:
    return (
        os.environ.get("JARVIS_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_TEST_DATABASE_URL
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def require_db() -> str:
    database_url = _resolve_database_url()
    try:
        with psycopg.connect(database_url, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 - any connection failure means "unreachable"
        reason = (
            "backend/tests/multiuser needs a reachable Postgres+pgvector. "
            f"Could not connect to {database_url!r} ({exc!r}). Start it with: "
            "`cd backend && docker compose up -d`, then re-run "
            "`cd backend && uv run pytest tests/multiuser -q`."
        )
        if _truthy(os.environ.get(_REQUIRE_DB_ENV_VAR)):
            pytest.fail(
                f"{reason} {_REQUIRE_DB_ENV_VAR} is set: unreachable DB is a hard failure.",
                pytrace=False,
            )
        pytest.skip(f"{_SKIP_TAG}: {reason}")
    return database_url


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001
    skipped = terminalreporter.stats.get("skipped", [])
    tagged = [r for r in skipped if _SKIP_TAG in str(getattr(r, "longrepr", ""))]
    if not tagged:
        return
    suites = sorted({r.nodeid.split("::", 1)[0] for r in tagged})
    terminalreporter.write_sep(
        "=", "MULTIUSER DB-DEPENDENT SUITE SKIPPED (T-10)", red=True, bold=True
    )
    terminalreporter.write_line(
        f"{len(tagged)} test(s) skipped because Postgres was unreachable -- "
        "this run did NOT exercise memory isolation / preference injection."
    )
    for suite in suites:
        terminalreporter.write_line(f"  - {suite}")


@pytest.fixture
def clerk_user_id() -> str:
    """A fresh Clerk-shaped user id (``user_...``) per test."""
    return f"user_{uuid.uuid4().hex}"


class RSAKeyPair:
    """A real RSA keypair minted once per test session -- used both as the
    "real" Clerk signing key (networkless ``CLERK_JWT_KEY`` verification
    mode) and, via a second unrelated instance, as an attacker's key for
    the forged-signature test.
    """

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def private_pem(self) -> str:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    @property
    def public_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )


@pytest.fixture(scope="session")
def clerk_signing_key() -> RSAKeyPair:
    """The keypair the app is configured to trust (``CLERK_JWT_KEY``)."""
    return RSAKeyPair()


@pytest.fixture(scope="session")
def attacker_signing_key() -> RSAKeyPair:
    """An unrelated keypair the app never trusts -- for forged-token tests."""
    return RSAKeyPair()


def mint_clerk_token(
    *,
    signing_key: RSAKeyPair,
    sub: str,
    kid: str = "test-kid-1",
    expired: bool = False,
    extra_claims: dict | None = None,
) -> str:
    """Build a real, RS256-signed, Clerk-session-token-shaped JWT.

    Not a fake/stub token: it is decoded by the exact same
    ``clerk_backend_api.security.verify_token`` -> ``jwt.decode(...,
    algorithms=["RS256"])`` path production traffic goes through.
    """
    now = datetime.now(UTC)
    iat = now - timedelta(minutes=1)
    exp = (now - timedelta(minutes=30)) if expired else (now + timedelta(minutes=30))
    claims = {
        "sub": sub,
        "iss": "https://test.clerk.accounts.dev",
        "iat": int(iat.timestamp()),
        "nbf": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
        "azp": "https://jarvis.test",
    }
    if extra_claims:
        claims.update(extra_claims)
    return pyjwt.encode(
        claims,
        signing_key.private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.fixture
def app_env(monkeypatch, clerk_signing_key: RSAKeyPair):
    """Configure the process env the app reads: networkless Clerk
    verification against ``clerk_signing_key``'s public PEM, plus fake
    LiveKit credentials (``AccessToken`` only needs *some* key/secret to
    build a JWT -- no real LiveKit network call happens in ``/api/token``).
    """
    monkeypatch.setenv("CLERK_JWT_KEY", clerk_signing_key.public_pem)
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-livekit-key")
    livekit_secret = "test-livekit-secret-0123456789-padding-to-32-bytes"
    monkeypatch.setenv("LIVEKIT_API_SECRET", livekit_secret)
    monkeypatch.setenv("LIVEKIT_URL", "wss://test.livekit.example")
    # Unset by default so tests get the documented fallback (room derived
    # from LIVEKIT_API_SECRET) unless a test explicitly sets JARVIS_ROOM_SALT
    # itself -- guards against an ambient value in the real environment
    # leaking into a test run and silently changing derived rooms.
    monkeypatch.delenv("JARVIS_ROOM_SALT", raising=False)
    return {
        "livekit_api_key": "test-livekit-key",
        "livekit_api_secret": livekit_secret,
    }


@pytest.fixture
def client(app_env) -> TestClient:
    from jarvis.server.app import app

    return TestClient(app)
