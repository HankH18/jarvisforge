#!/usr/bin/env bash
# run_require_db_wiring_tests.sh (T-31) - tests scripts/verify_submission.sh's
# memory-suite check: is JARVIS_MEMORY_TESTS_REQUIRE_DB actually SET by the
# one owning context T-31 names (the submission gate), and does the gate's
# own exit code actually depend on it?
#
# BACKGROUND: $JARVIS_MEMORY_TESTS_REQUIRE_DB=1 (backend/tests/memory/conftest.py's
# require_db fixture) turns an unreachable Postgres+pgvector into a hard
# `pytest.fail()` instead of a loud-but-passing skip. Before T-31, nothing in
# this repo ever set that flag, so the memory suite's integration layer
# could silently never run while the exit code stayed 0. T-31 wires
# scripts/verify_submission.sh's check `[4/4]` to set it. This harness
# proves that wiring actually binds -- not just that the string
# "JARVIS_MEMORY_TESTS_REQUIRE_DB=1" appears somewhere in the file.
#
# CONVENTION: follows scripts/tests/run_smoke_exit_code_tests.sh's existing
# pattern exactly -- a throwaway bin dir with stub external commands placed
# earlier on PATH than the real ones, driving the REAL
# scripts/verify_submission.sh via its own documented `[project_dir]`
# argument (see that script's usage comment) against a fixture "sandbox"
# project directory, rather than reimplementing verify_submission.sh's
# logic here.
#
# NOTE 2026-08-16: verify_submission.sh's repo-private check (formerly
# [2/6]) was removed from the script entirely at Hank's request (it could
# never pass with the scoped token available; visibility was confirmed
# manually in the GitLab UI instead -- see the removal-point comment in
# verify_submission.sh and docs/SUBMISSION.md). That check's git-credential
# and curl stubs below are gone with it, and every check after it shifted
# down by one: [3/6]->[2/5], [4/6]->[3/5], [5/6]->[4/5], [6/6]->[5/5].
#
# NOTE 2026-08-17: verify_submission.sh's demo-video check (formerly [2/5])
# was removed from the script entirely too, same grounds -- the video is
# recorded and hosted on Loom rather than committed to the repo, so the
# local-file check could never pass (see the removal-point comment in
# verify_submission.sh). That check's dummy-video fixture below is gone
# with it, and every check after it shifted down by one again:
# [3/5]->[2/4], [4/5]->[3/4], [5/5]->[4/4].
#
# WHAT IS STUBBED AND WHY:
#   - `uv`: check [4/4] shells out to `cd backend && ... uv run pytest
#     tests/memory -q`. This harness is about verify_submission.sh's OWN
#     flag-wiring and exit-code plumbing (T-31 acceptance #2), not live DB
#     connectivity or a real pytest run, so `uv` is stubbed to behave
#     exactly as a real `uv run pytest tests/memory -q` would in each
#     DB-reachability scenario:
#       * flag set + DB reachable   -> exit 0 (a real integration pass)
#       * flag set + DB unreachable -> exit 1 (require_db's pytest.fail())
#       * flag NOT set + DB unreachable -> exit 0 (the exact T-31 defect:
#         a loud-but-passing skip). This third branch is what makes the
#         break/restore experiment in T-31's report meaningful: comment
#         out the flag in verify_submission.sh and this same stub goes
#         back to reporting a silent pass even though the database is
#         unreachable -- i.e. this test's RED/GREEN genuinely depends on
#         verify_submission.sh setting the flag, not merely on the stub's
#         own scripted responses.
# Checks [1/4], [2/4], [3/4] are satisfied for real (no stubbing) by
# fixture files this harness writes into the sandbox project dir before
# each run -- an empty docs/tickets.json + empty .claude/evidence/ (check
# 1), a docs/TECHNICAL.md covering the four required topics (check 2), and
# a trivial submission/build_zip.sh fixture (check 3, mirroring the real
# script's shape but not its exclude list, which is out of T-31's scope)
# -- so the sandbox's overall exit code is driven ONLY by the memory check
# under test, in both directions.
#
# Two cases, each asserting an exact exit code AND the memory check's own
# PASSED/FAILED line:
#   1. db_unreachable -> [4/4] FAILS, verify_submission.sh exits non-zero
#      (guaranteed regardless of the other checks: OVERALL_FAIL is
#      monotonic in verify_submission.sh -- once any check sets it, the
#      final exit code is non-zero no matter what else passed)
#   2. db_reachable    -> [4/4] PASSES, and -- because every other check in
#      the sandbox is independently satisfied by the fixtures above --
#      verify_submission.sh exits 0
#
# Usage: bash scripts/tests/run_require_db_wiring_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERIFY_SCRIPT="${REPO_ROOT}/scripts/verify_submission.sh"

REAL_GIT="$(command -v git)"
if [ -z "$REAL_GIT" ]; then
    echo "run_require_db_wiring_tests: BUG -- no real 'git' found on PATH" >&2
    exit 2
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

FAILURES=0
CASES_RUN=0

# ---------------------------------------------------------------------------
# build_sandbox <project_dir>
# Writes a minimal-but-real project dir that satisfies checks [1/4], [2/4],
# and [3/4] deterministically, so the sandbox's overall pass/fail is
# driven only by the memory check under test. (Before 2026-08-16 this also
# needed a labs.gauntletai.com `origin` remote plus git/curl stubs, for the
# repo-private check that used to sit at [2/6] -- that check no longer
# exists in verify_submission.sh, so neither does the setup for it here.
# Before 2026-08-17 this also wrote a dummy video file under
# submission/video/, for the demo-video check that used to sit at [2/5] --
# that check no longer exists either, so neither does the fixture for it
# here.)
# ---------------------------------------------------------------------------
build_sandbox() {
    local project_dir="$1"
    mkdir -p "$project_dir"

    "$REAL_GIT" -C "$project_dir" init -q

    # -- check [1/4]: evidence freshness gate. A valid, empty tickets.json
    # and no receipts under .claude/evidence means nothing to check ->
    # the REAL, unmodified check_evidence_staleness.sh reports
    # "STALE RECEIPTS: none (0 receipt(s) checked)" and exits 0.
    mkdir -p "${project_dir}/docs" "${project_dir}/.claude/evidence"
    printf '{"tickets": []}\n' >"${project_dir}/docs/tickets.json"

    # -- check [2/4]: docs/TECHNICAL.md must cover architecture, stack
    # rationale, known limits, and an AI-usage note.
    cat >"${project_dir}/docs/TECHNICAL.md" <<'TECH'
# Sandbox TECHNICAL.md (fixture for run_require_db_wiring_tests.sh)

## Architecture
Fixture text only -- this file exists to satisfy verify_submission.sh's
docs/TECHNICAL.md content check inside an isolated sandbox project dir.

## Stack rationale
Fixture text.

## Known limits
Fixture text.

## AI-usage note
Fixture text.
TECH

    # -- check [3/4]: submission/build_zip.sh must exist and produce a
    # valid zip containing docs/TECHNICAL.md with no excluded paths. This
    # fixture is a trivial stand-in for the real submission/build_zip.sh
    # (out of T-31's scope) -- it only needs to satisfy verify_submission.sh's
    # own zip-integrity checks, not replicate the real exclude list.
    # (mkdir here is now load-bearing: before 2026-08-17 the demo-video
    # fixture's `mkdir -p .../submission/video` created this directory as a
    # side effect; with that fixture gone, submission/ needs its own mkdir.)
    mkdir -p "${project_dir}/submission"
    cat >"${project_dir}/submission/build_zip.sh" <<'BUILDZIP'
#!/usr/bin/env bash
set -euo pipefail
OUT="${1:?output path required}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"
rm -f "$OUT"
zip -X -q "$OUT" docs/TECHNICAL.md
echo "build_zip(sandbox fixture): wrote ${OUT}"
BUILDZIP
    chmod +x "${project_dir}/submission/build_zip.sh"

    # -- check [4/4] (the check under test): only needs backend/ to exist;
    # the actual `uv run pytest tests/memory -q` invocation is stubbed.
    mkdir -p "${project_dir}/backend"
}

# ---------------------------------------------------------------------------
# write_stub_uv <bin_dir>
# Stands in for `uv run pytest tests/memory -q`. Reads two env vars:
#   $STUB_DB_REACHABLE               -- this test case's simulated DB
#                                        state (1 or 0), set by run_case
#                                        below.
#   $JARVIS_MEMORY_TESTS_REQUIRE_DB  -- NOT set by this harness; this is
#                                        the thing under test. It is set
#                                        (or not) by verify_submission.sh's
#                                        own [4/4] check when it invokes
#                                        `uv run pytest tests/memory -q`.
# ---------------------------------------------------------------------------
write_stub_uv() {
    local bin_dir="$1"
    cat >"${bin_dir}/uv" <<'UV_STUB'
#!/usr/bin/env bash
if [ "$1" = "run" ] && [ "$2" = "pytest" ]; then
    if [ "${JARVIS_MEMORY_TESTS_REQUIRE_DB:-}" = "1" ]; then
        if [ "${STUB_DB_REACHABLE:-0}" = "1" ]; then
            echo "24 passed in 0.42s (stub: JARVIS_MEMORY_TESTS_REQUIRE_DB=1, DB reachable)"
            exit 0
        else
            echo "FAILED tests/memory/test_integration.py::test_x - stub: JARVIS_MEMORY_TESTS_REQUIRE_DB=1 and the database is unreachable -> require_db's pytest.fail(), not a skip" >&2
            exit 1
        fi
    else
        # The T-31 defect, reproduced: with the flag unset, an unreachable
        # database is a loud skip, not a failure -- exit 0 either way.
        if [ "${STUB_DB_REACHABLE:-0}" = "1" ]; then
            echo "24 passed in 0.42s (stub: flag unset, DB reachable)"
        else
            echo "15 passed, 9 skipped in 0.31s (stub: flag unset, DB unreachable -- loud skip, not a failure)"
        fi
        exit 0
    fi
fi
echo "stub uv: unexpected invocation: $*" >&2
exit 99
UV_STUB
    chmod +x "${bin_dir}/uv"
}

# ---------------------------------------------------------------------------
# run_case <name> <db_reachable: 1|0> <expect: zero|nonzero> <expect_pattern>
# Builds a fresh sandbox + stub bin dir, runs the REAL verify_submission.sh
# against it (bin dir first on PATH), and asserts both the exit code and
# that the memory check's own PASSED/FAILED line appears in the output.
# ---------------------------------------------------------------------------
run_case() {
    local name="$1"
    local db_reachable="$2"
    local expect="$3"
    local expect_pattern="$4"

    local case_dir="${WORK_DIR}/${name}"
    local project_dir="${case_dir}/project"
    local bin_dir="${case_dir}/bin"
    mkdir -p "$bin_dir"

    build_sandbox "$project_dir"
    write_stub_uv "$bin_dir"

    local out_file="${case_dir}/output.log"
    local rc=0
    PATH="${bin_dir}:${PATH}" STUB_DB_REACHABLE="$db_reachable" \
        bash "$VERIFY_SCRIPT" "$project_dir" >"$out_file" 2>&1 || rc=$?

    CASES_RUN=$((CASES_RUN + 1))
    local ok=1
    case "$expect" in
        zero)
            [ "$rc" -eq 0 ] || ok=0
            ;;
        nonzero)
            [ "$rc" -ne 0 ] || ok=0
            ;;
        *)
            printf 'BUG in test harness: unknown expectation %s\n' "$expect" >&2
            exit 2
            ;;
    esac
    if ! grep -qE "$expect_pattern" "$out_file"; then
        ok=0
    fi

    if [ "$ok" -eq 1 ]; then
        printf 'PASS: %-16s exit=%-3s expected=%-8s pattern-matched="%s"\n' "$name" "$rc" "$expect" "$expect_pattern"
    else
        FAILURES=$((FAILURES + 1))
        printf 'FAIL: %-16s exit=%-3s expected=%-8s pattern="%s"\n' "$name" "$rc" "$expect" "$expect_pattern"
        printf '  --- verify_submission.sh output (%s) ---\n' "$name"
        sed 's/^/  /' "$out_file"
        printf '  -------------------------------------------\n'
    fi
}

# ---------------------------------------------------------------------------
# Case 1: unreachable database, flag honestly wired (this drives the real,
# unmodified scripts/verify_submission.sh) -> [4/4] must FAIL and the
# overall gate must exit non-zero.
# ---------------------------------------------------------------------------
run_case "db_unreachable" "0" "nonzero" "FAILED -- memory suite check"

# ---------------------------------------------------------------------------
# Case 2: reachable database, flag honestly wired -> [4/4] must PASS, and
# because every other check in the sandbox is independently satisfied (see
# build_sandbox), the overall gate must exit 0.
# ---------------------------------------------------------------------------
run_case "db_reachable" "1" "zero" "memory suite check PASSED"

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
    printf 'SUMMARY: %d/%d verify_submission.sh memory-check wiring cases passed.\n' "$CASES_RUN" "$CASES_RUN"
    exit 0
else
    printf 'SUMMARY: %d/%d verify_submission.sh memory-check wiring case(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
    exit 1
fi
