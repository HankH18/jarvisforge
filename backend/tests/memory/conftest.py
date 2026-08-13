"""Shared fixtures for tests/memory (T-5).

Two layers live in this directory (see test_contract.py / test_integration.py
module docstrings for what each proves):

* Contract/unit tests run unconditionally, offline, against in-process fakes
  (fakes.py) -- no database involved.
* Integration tests need a real reachable Postgres+pgvector (this project's
  backend/docker-compose.yml service). ``require_db`` below resolves the
  connection string and SKIPS (never fails) the integration tests with an
  explicit reason if that database isn't reachable right now -- CI runs the
  whole suite with no Postgres present (SPEC C7 forbids adding CI services),
  so a hard failure there would break the build. If the database IS
  reachable and a test genuinely fails, it fails for real; nothing here
  catches or downgrades that.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

# MEM0_TELEMETRY must be set before anything imports mem0 in this process
# (see jarvis/memory/store.py's own module-level setdefault -- this belongs
# here too since conftest.py is imported before any test module).
os.environ.setdefault("MEM0_TELEMETRY", "False")

_DEFAULT_TEST_DATABASE_URL = "postgresql://jarvis:jarvis_dev_local_only@localhost:55432/jarvis"


def _resolve_database_url() -> str:
    return (
        os.environ.get("JARVIS_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_TEST_DATABASE_URL
    )


@pytest.fixture(scope="session")
def require_db() -> str:
    """The test database URL, iff it's actually reachable right now.

    Skips (with an explicit, loud reason) rather than failing when it isn't
    -- see module docstring.
    """
    database_url = _resolve_database_url()
    try:
        with psycopg.connect(database_url, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any connection failure means "skip"
        pytest.skip(
            "backend/tests/memory integration tests need a reachable Postgres+pgvector. "
            f"Could not connect to {database_url!r} ({exc!r}). Start it with: "
            "`cd backend && docker compose up -d` (see backend/docker-compose.yml), then "
            "re-run `cd backend && uv run pytest tests/memory -q`. Override the URL with "
            "$JARVIS_TEST_DATABASE_URL or $DATABASE_URL if your Postgres lives elsewhere."
        )
    return database_url


@pytest.fixture
def unique_user_id() -> str:
    """A fresh random user id per test -- avoids cross-test bleed in a
    Postgres volume that persists across test runs, without needing to
    reset/truncate any table between tests.
    """
    return f"test-user-{uuid.uuid4().hex}"
