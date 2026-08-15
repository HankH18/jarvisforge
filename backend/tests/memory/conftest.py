"""Shared fixtures for tests/memory (T-5, loud-skip behavior added T-23).

Two layers live in this directory (see test_contract.py / test_integration.py
module docstrings for what each proves):

* Contract/unit tests run unconditionally, offline, against in-process fakes
  (fakes.py) -- no database involved.
* Integration tests need a real reachable Postgres+pgvector (this project's
  backend/docker-compose.yml service). ``require_db`` below resolves the
  connection string and, by default, SKIPS (never fails) the integration
  tests with an explicit reason if that database isn't reachable right now
  -- CI runs the whole suite with no Postgres present (SPEC C7 forbids
  adding CI services), so a hard failure there would break the build. If the
  database IS reachable and a test genuinely fails, it fails for real;
  nothing here catches or downgrades that.

T-23: a plain skip here used to make `pytest tests/memory -q` report a
"clean" run that looks identical whether or not the integration suite (the
part that actually proves mem0/pgvector work -- see test_integration.py's
own docstring) ran at all. Two changes fix that without making this
ticket's own verify command unclosable on a machine with no Postgres:

1. The skip is LOUD: `pytest_terminal_summary` below always prints, in the
   terminal summary (regardless of -q/-v), which suite(s) were skipped and
   why, and states plainly that a green exit code did NOT exercise the
   integration behavior. A reader cannot mistake this for the same "clean
   pass" a fully-run suite would produce.
2. There is an explicit, named opt-in --
   $JARVIS_MEMORY_TESTS_REQUIRE_DB=1 -- that turns an unreachable database
   into a hard `pytest.fail()` instead of a skip. scripts/verify_submission.sh
   is the one context that actually sets this (T-31, its memory-suite
   check); a local/offline dev machine, and every plain ticket verify, does
   not have to.
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

# Explicit, discoverable opt-in (T-23 acceptance #4): when truthy, an
# unreachable database is a hard failure, not a skip. Documented in
# docs/ops-smoke-test.md -- scripts/verify_submission.sh's memory-suite
# check is the one context that sets this (T-31); local/offline dev should
# not need to.
_REQUIRE_DB_ENV_VAR = "JARVIS_MEMORY_TESTS_REQUIRE_DB"

# Marker embedded in the skip reason so pytest_terminal_summary can find
# every test skipped for THIS reason (as opposed to any other skip a test
# might use) via terminalreporter.stats, independent of how many test
# modules end up depending on the require_db fixture.
_SKIP_TAG = "JARVIS_MEMORY_DB_UNREACHABLE"


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
    """The test database URL, iff it's actually reachable right now.

    Skips -- loudly, see ``pytest_terminal_summary`` below -- rather than
    failing when it isn't, UNLESS $JARVIS_MEMORY_TESTS_REQUIRE_DB is set
    truthy, in which case an unreachable database is a hard failure. See
    module docstring for why both behaviors exist.
    """
    database_url = _resolve_database_url()
    try:
        with psycopg.connect(database_url, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any connection failure means "unreachable"
        reason = (
            "backend/tests/memory integration tests need a reachable Postgres+pgvector. "
            f"Could not connect to {database_url!r} ({exc!r}). Start it with: "
            "`cd backend && docker compose up -d` (see backend/docker-compose.yml), then "
            "re-run `cd backend && uv run pytest tests/memory -q`. Override the URL with "
            "$JARVIS_TEST_DATABASE_URL or $DATABASE_URL if your Postgres lives elsewhere."
        )
        if _truthy(os.environ.get(_REQUIRE_DB_ENV_VAR)):
            pytest.fail(
                f"{reason} {_REQUIRE_DB_ENV_VAR} is set, so an unreachable database is a "
                "hard FAILURE in this run, not a skip (see docs/ops-smoke-test.md).",
                pytrace=False,
            )
        pytest.skip(f"{_SKIP_TAG}: {reason}")
    return database_url


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001 - pytest hook signature
    """T-23: make an unreachable-Postgres skip loud.

    Without this, `pytest tests/memory -q` on a machine with no Postgres
    and on a machine WITH Postgres produce output that differs only in a
    "skipped" vs not count buried in the final tally line -- easy to miss,
    and nothing names which suite silently didn't run. This hook always
    fires (independent of -q/-v) and, only when at least one test was
    skipped for the require_db reason specifically (identified via
    ``_SKIP_TAG``, not skips in general), prints which suite(s) were
    skipped and states plainly that the run did not exercise them.
    """
    skipped = terminalreporter.stats.get("skipped", [])
    tagged = [r for r in skipped if _SKIP_TAG in str(getattr(r, "longrepr", ""))]
    if not tagged:
        return

    suites = sorted({r.nodeid.split("::", 1)[0] for r in tagged})
    terminalreporter.write_sep("=", "MEMORY INTEGRATION SUITE SKIPPED (T-23)", red=True, bold=True)
    terminalreporter.write_line(
        f"{len(tagged)} test(s) skipped because Postgres was unreachable -- "
        "this run did NOT exercise mem0/pgvector."
    )
    terminalreporter.write_line("Skipped suite(s):")
    for suite in suites:
        terminalreporter.write_line(f"  - {suite}")
    terminalreporter.write_line(
        "A green exit code above proves only the contract/unit tests (in-process fakes) "
        f"passed -- see docs/ops-smoke-test.md. Set {_REQUIRE_DB_ENV_VAR}=1 to make an "
        "unreachable database a hard failure instead (CI/deploy gating should set this)."
    )


@pytest.fixture
def unique_user_id() -> str:
    """A fresh random user id per test -- avoids cross-test bleed in a
    Postgres volume that persists across test runs, without needing to
    reset/truncate any table between tests.
    """
    return f"test-user-{uuid.uuid4().hex}"
