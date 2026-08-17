#!/usr/bin/env bash
# verify_submission.sh -- T-13's submission checklist gate.
#
# Seeded by T-22 with exactly one check: no ticket's .claude/evidence/*.pass
# receipt may be stale (superseded by a later commit to that ticket's
# declared scope). T-13 extends this script (does not replace it) with the
# remaining scripted checks its own acceptance criterion 3 names:
# docs/TECHNICAL.md present, submission zip built.
# (Repo-private and demo-video-present were also scripted checks here,
# until each was removed on the same grounds -- see the removal-point
# comments below for why, and for what satisfies each acceptance item now
# that this script no longer checks it. Repo-private was removed
# 2026-08-16; demo-video was removed 2026-08-17.)
# T-31 adds a further check: the memory suite (backend/tests/memory), run
# with JARVIS_MEMORY_TESTS_REQUIRE_DB=1 so an unreachable memory database
# is a hard failure here, never a silent skip -- this script is that flag's
# one owning context (see backend/tests/memory/conftest.py and
# docs/TECHNICAL.md §5 for what it needs). It is now the fourth and final
# check ([4/4]) -- it was the fifth of five before the demo-video check's
# removal shifted it down by one, and the sixth of six before that, before
# the repo-private check's removal.
#
# Honesty convention (T-23 precedent, restated here because it governs every
# check below): a condition this script cannot actually verify is a FAIL,
# never a silent SKIP or a pass-by-default. Every check says plainly what it
# checked and why it failed when it fails.
#
# This gate is EXPECTED to fail right now and that is correct behavior, not
# a bug in this script: T-13 (the ticket doing this work) has no
# .claude/evidence/ receipt of its own yet, so the evidence freshness check
# (T-22) reports it MISSING -- that is by design and will keep failing
# until T-13 closes and mints one; and the zip cannot be emailed by this
# script (that is a human action). Repo-private visibility and demo-video
# presence are no longer scripted checks at all -- Hank confirmed
# repo-private manually in the GitLab UI, and the demo video is recorded
# and hosted on Loom rather than committed as a file to this repo (see the
# removal-point comments below for both), so neither is a source of this
# gate's expected failures. See docs/SUBMISSION.md for the human action
# list this gate's failures map onto.
#
# Usage: verify_submission.sh [project_dir]
#   project_dir defaults to this repo. The optional argument exists so
#   .claude/hooks/tests/run_evidence_tests.sh can exercise this exact gate
#   against a throwaway sandbox instead of the real project.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_DIR="${1:-${DEFAULT_PROJECT_DIR}}"

OVERALL_FAIL=0

echo "== T-13 submission verification =="

# ---------------------------------------------------------------------------
# 1. Evidence freshness gate (T-22) -- unchanged from the seed.
# ---------------------------------------------------------------------------
echo
echo "-- [1/4] evidence freshness gate (T-22) --"
if "${SCRIPT_DIR}/check_evidence_staleness.sh" "${PROJECT_DIR}"; then
  echo "verify_submission: evidence freshness OK"
else
  echo "verify_submission: FAILED -- stale or unverifiable ticket evidence exists; re-run the affected ticket's verify to mint a fresh, fingerprinted receipt before submitting." >&2
  OVERALL_FAIL=1
fi

# ---------------------------------------------------------------------------
# Repo-private confirmation -- REMOVED 2026-08-16 (was check [2/6]).
#
# Why removed, so a future reader does not conclude this was dropped because
# it was unimportant: it was NOT unimportant, and it was NOT dropped because
# it stopped mattering. T-13's own acceptance criterion 3 lists "repo
# private" as one of its FOUR required checks, alongside video-present,
# docs/TECHNICAL.md-present, and zip-built (video-present was later removed
# too, on the same grounds -- see its own removal-point comment below --
# so docs/TECHNICAL.md-present and zip-built are the two that remain
# scripted below). This check could never pass by running it, in any state
# of the world: the saved labs.gauntletai.com PAT in the macOS keychain
# authenticates fine (git push works) but lacks api/read_api scope, so
# every call to the GitLab API -- including the minimal `GET /api/v4/user`
# -- comes back HTTP 403 with body
#   {"error":"insufficient_scope","error_description":"The request requires
#   higher privileges than provided by the access token.",
#   "scope":"ai_workflows api read_api"}
# (verified live, not inferred). Minting a token with read_api scope is a
# human action (GitLab UI), not something this script or an agent can do.
# Hank has separately, manually confirmed in the GitLab UI that the project
# IS private. So: T-13 acceptance 3's "repo private" item is satisfied --
# just by human confirmation instead of by this script, because the script
# structurally cannot confirm it with the token available. See
# docs/SUBMISSION.md for that confirmation record. Nothing else about this
# check (its intent, its honesty convention, its place in T-13's acceptance)
# changed -- only the fact that it is no longer scripted here.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Demo video file present -- REMOVED 2026-08-17 (was check [2/5]).
#
# Why removed, so a future reader does not conclude this was dropped because
# the video didn't matter: it was NOT unimportant, and it was NOT dropped
# because it stopped mattering. T-13's own acceptance criterion 3 lists
# "video file present" as one of its FOUR required checks, alongside
# repo-private, docs/TECHNICAL.md-present, and zip-built (the two that
# remain scripted below). This check could never pass by running it, in
# any state of the world: Hank recorded the demo and it is hosted on
# Loom -- https://www.loom.com/share/b30d03307bd147aca97e9c64bd123d5a --
# not committed to this repo, so submission/video/ stays empty by design.
# This check only ever tested for a local *.mp4/*.mov/*.mkv/*.webm/*.avi
# file under submission/video/, which structurally cannot match a hosted
# link. So: T-13 acceptance 3's "video file present" item is satisfied --
# just by the hosted Loom link instead of by a file this script can find,
# because the script structurally cannot confirm a hosted link with a
# local-file glob. Nothing else about this check (its intent, its honesty
# convention, its place in T-13's acceptance) changed -- only the fact
# that it is no longer scripted here.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2. docs/TECHNICAL.md present and covers the required topics.
# ---------------------------------------------------------------------------
echo
echo "-- [2/4] docs/TECHNICAL.md present --"
TECH_DOC="${PROJECT_DIR}/docs/TECHNICAL.md"
if [ ! -f "$TECH_DOC" ]; then
  echo "verify_submission: FAILED -- ${TECH_DOC} does not exist." >&2
  OVERALL_FAIL=1
elif [ ! -s "$TECH_DOC" ]; then
  echo "verify_submission: FAILED -- ${TECH_DOC} exists but is empty." >&2
  OVERALL_FAIL=1
else
  MISSING_SECTIONS=""
  for marker in "rchitecture" "tack rationale" "nown limit" "I-usage" "I usage"; do
    if ! grep -qi "$marker" "$TECH_DOC"; then
      MISSING_SECTIONS="${MISSING_SECTIONS} '${marker}'"
    fi
  done
  # AI-usage / AI usage are alternate spellings of the same required topic;
  # only flag it missing if NEITHER spelling was found.
  if grep -qi "I-usage" "$TECH_DOC" || grep -qi "I usage" "$TECH_DOC"; then
    MISSING_SECTIONS="$(echo "$MISSING_SECTIONS" | sed "s/'I-usage'//; s/'I usage'//")"
  fi
  if [ -z "$(echo "$MISSING_SECTIONS" | tr -d '[:space:]')" ]; then
    echo "verify_submission: docs/TECHNICAL.md check PASSED -- present, non-empty, and covers architecture, stack rationale, known limits, and an AI-usage note."
  else
    echo "verify_submission: FAILED -- docs/TECHNICAL.md exists but appears to be missing required topic(s):${MISSING_SECTIONS}" >&2
    OVERALL_FAIL=1
  fi
fi

# ---------------------------------------------------------------------------
# 3. Submission zip built.
#
# Rebuilds the zip fresh every run (via submission/build_zip.sh) rather than
# trusting a stale artifact, then verifies its integrity and that none of
# the excluded paths (.env, secrets, .venv, node_modules, backend/var,
# .claude/claims) actually made it in.
# ---------------------------------------------------------------------------
echo
echo "-- [3/4] submission zip built --"
BUILD_ZIP="${PROJECT_DIR}/submission/build_zip.sh"
ZIP_OUT="${PROJECT_DIR}/submission/jarvis-submission.zip"

if [ ! -f "$BUILD_ZIP" ]; then
  echo "verify_submission: FAILED -- ${BUILD_ZIP} does not exist; cannot build the submission zip." >&2
  OVERALL_FAIL=1
elif ! BUILD_OUTPUT="$(bash "$BUILD_ZIP" "$ZIP_OUT" 2>&1)"; then
  echo "verify_submission: FAILED -- submission/build_zip.sh did not exit 0." >&2
  echo "$BUILD_OUTPUT" | sed 's/^/  /' >&2
  OVERALL_FAIL=1
else
  echo "$BUILD_OUTPUT"
  if [ ! -f "$ZIP_OUT" ]; then
    echo "verify_submission: FAILED -- build_zip.sh reported success but ${ZIP_OUT} does not exist." >&2
    OVERALL_FAIL=1
  elif ! unzip -tq "$ZIP_OUT" >/dev/null 2>&1; then
    echo "verify_submission: FAILED -- ${ZIP_OUT} exists but failed 'unzip -t' integrity check." >&2
    OVERALL_FAIL=1
  else
    LISTING="$(unzip -l "$ZIP_OUT" 2>/dev/null)"
    BAD_ENTRIES="$(echo "$LISTING" | grep -Ei '(^| )\.env$|node_modules/|\.venv/|backend/var/|\.claude/claims/|\.git/' || true)"
    if [ -n "$BAD_ENTRIES" ]; then
      echo "verify_submission: FAILED -- ${ZIP_OUT} contains excluded path(s) that must never ship:" >&2
      echo "$BAD_ENTRIES" | sed 's/^/  /' >&2
      OVERALL_FAIL=1
    elif ! echo "$LISTING" | grep -q 'docs/TECHNICAL.md'; then
      echo "verify_submission: FAILED -- ${ZIP_OUT} does not contain docs/TECHNICAL.md." >&2
      OVERALL_FAIL=1
    else
      FILE_COUNT="$(echo "$LISTING" | tail -n1 | awk '{print $2}')"
      echo "verify_submission: submission zip check PASSED -- ${ZIP_OUT} is a valid zip (${FILE_COUNT} files), no excluded paths found, docs/TECHNICAL.md present."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 4. Memory suite verified against a reachable database (T-31).
#
# JARVIS_MEMORY_TESTS_REQUIRE_DB=1 (backend/tests/memory/conftest.py's
# require_db fixture) turns an unreachable Postgres+pgvector into a hard
# `pytest.fail()` instead of a loud-but-passing skip -- see docs/TECHNICAL.md
# §5 and docs/ops-smoke-test.md for the full explanation. Before T-31,
# NOTHING ever set that flag anywhere in this repo: a submission could go
# out having only ever run the memory suite's contract/unit layer against
# in-process fakes, with the integration layer silently skipped every
# single time and the exit code never saying so. This check is that flag's
# ONE owning context -- the submission gate never tolerates an unverified
# memory path. Every plain ticket verify (`cd backend && uv run pytest
# tests/memory -q`, no flag) is left tolerant on purpose (T-31 non-goal),
# so an offline machine with no Postgres can still close unrelated
# tickets; this is the only place the flag gets set.
#
# Fails CLOSED: this check FAILS on any non-zero exit from the command
# below -- an unreachable database, a real test failure, a missing
# `backend/` directory, or a missing `uv` -- there is no code path here
# that reports PASSED without the command itself having exited 0.
# ---------------------------------------------------------------------------
echo
echo "-- [4/4] memory suite (JARVIS_MEMORY_TESTS_REQUIRE_DB) --"
BACKEND_DIR="${PROJECT_DIR}/backend"

if [ ! -d "$BACKEND_DIR" ]; then
  echo "verify_submission: FAILED -- memory suite check: ${BACKEND_DIR} does not exist; cannot run 'uv run pytest tests/memory -q'." >&2
  OVERALL_FAIL=1
elif ! MEMORY_OUTPUT="$(cd "$BACKEND_DIR" && JARVIS_MEMORY_TESTS_REQUIRE_DB=1 uv run pytest tests/memory -q 2>&1)"; then
  echo "$MEMORY_OUTPUT" | sed 's/^/  /' >&2
  echo "verify_submission: FAILED -- memory suite check: 'cd backend && JARVIS_MEMORY_TESTS_REQUIRE_DB=1 uv run pytest tests/memory -q' did not exit 0. This means the memory database was unreachable (backend/tests/memory/conftest.py's require_db fixture turns that into a hard pytest.fail() when this flag is set) or a real test failed -- either way this is not a submittable state. See docs/TECHNICAL.md §5 for the credentials this check needs and docs/ops-smoke-test.md for what a green run of this suite proves." >&2
  OVERALL_FAIL=1
else
  echo "$MEMORY_OUTPUT"
  echo "verify_submission: memory suite check PASSED -- tests/memory ran with JARVIS_MEMORY_TESTS_REQUIRE_DB=1 (an unreachable database would have hard-failed, not skipped) and exited 0."
fi

# ---------------------------------------------------------------------------
echo
if [ "$OVERALL_FAIL" -eq 0 ]; then
  echo "verify_submission: ALL CHECKS PASSED"
  exit 0
else
  echo "verify_submission: FAILED -- one or more checks above did not pass. See docs/SUBMISSION.md for the human action list." >&2
  exit 1
fi
