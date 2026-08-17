#!/usr/bin/env bash
# verify_submission.sh -- T-13's submission checklist gate.
#
# Seeded by T-22 with exactly one check: no ticket's .claude/evidence/*.pass
# receipt may be stale (superseded by a later commit to that ticket's
# declared scope). T-13 extends this script (does not replace it) with the
# three remaining scripted checks its own acceptance criterion 3 names:
# video file present, docs/TECHNICAL.md present, submission zip built.
# (Repo-private was a fourth scripted check here too, until it was removed
# 2026-08-16 -- see the removal-point comment below for why, and for what
# satisfies that acceptance item now that this script no longer checks it.)
# T-31 adds a further check: the memory suite (backend/tests/memory), run
# with JARVIS_MEMORY_TESTS_REQUIRE_DB=1 so an unreachable memory database
# is a hard failure here, never a silent skip -- this script is that flag's
# one owning context (see backend/tests/memory/conftest.py and
# docs/TECHNICAL.md §5 for what it needs). It is now the fifth and final
# check ([5/5]) -- it was the sixth of six before the repo-private check's
# removal shifted every check after it down by one.
#
# Honesty convention (T-23 precedent, restated here because it governs every
# check below): a condition this script cannot actually verify is a FAIL,
# never a silent SKIP or a pass-by-default. Every check says plainly what it
# checked and why it failed when it fails.
#
# This gate is EXPECTED to fail right now and that is correct behavior, not
# a bug in this script: Cartesia TTS was upgraded 2026-08-15 and the
# deployed assistant speaks (see docs/latency-report.md), but no demo video
# file has been placed under submission/video/ yet -- a human recording
# task, not a TTS outage; and the zip cannot be emailed by this script (that
# is a human action). Repo-private visibility is no longer a scripted check
# at all -- Hank confirmed it manually in the GitLab UI (see the
# removal-point comment below), so it is not a source of this gate's
# expected failures. See docs/SUBMISSION.md for the human action list this
# gate's failures map onto.
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
echo "-- [1/5] evidence freshness gate (T-22) --"
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
# docs/TECHNICAL.md-present, and zip-built (the three that remain scripted
# below). This check could never pass by running it, in any state of the
# world: the saved labs.gauntletai.com PAT in the macOS keychain
# authenticates fine (git push works) but lacks api/read_api scope, so every
# call to the GitLab API -- including the minimal `GET /api/v4/user` -- comes
# back HTTP 403 with body
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
# 2. Demo video file present.
#
# Cartesia TTS was upgraded 2026-08-15 and the deployed assistant
# demonstrably speaks (independently confirmed -- see docs/latency-report.md
# and docs/SUBMISSION.md), so recording is no longer blocked on that front.
# The only reason this check fails is that no video file has been placed
# under submission/video/ yet -- a human recording/upload task, nothing
# else. Separately, SPEC.md's Open question 1 means the required *content*
# checklist for the video is not finalized yet, which is a real but distinct
# blocker on finalizing that checklist, not on recording something now.
# This check looks for a real video file and fails honestly when none
# exists, rather than accepting a placeholder.
# ---------------------------------------------------------------------------
echo
echo "-- [2/5] demo video file present --"
VIDEO_DIR="${PROJECT_DIR}/submission/video"
VIDEO_FOUND=""
if [ -d "$VIDEO_DIR" ]; then
  while IFS= read -r -d '' f; do
    VIDEO_FOUND="$f"
    break
  done < <(find "$VIDEO_DIR" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.webm' -o -iname '*.avi' \) -print0 2>/dev/null)
fi

if [ -n "$VIDEO_FOUND" ]; then
  SIZE_H="$(du -h "$VIDEO_FOUND" 2>/dev/null | cut -f1)"
  echo "verify_submission: demo video check PASSED -- found ${VIDEO_FOUND} (${SIZE_H})."
else
  echo "verify_submission: FAILED -- demo video check: no video file found under ${VIDEO_DIR} (looked for *.mp4/*.mov/*.mkv/*.webm/*.avi)." >&2
  echo "  Cartesia TTS was upgraded 2026-08-15 and the deployed assistant speaks (see docs/latency-report.md) -- nothing about TTS blocks recording. This check fails only because no video file has been placed under ${VIDEO_DIR} yet." >&2
  echo "  NOTE: SPEC.md Open question 1 -- the required video content checklist (and the Attachments section) was cut off in the provided PRD screenshots and is not finalized. That blocks finalizing the checklist, not recording; get that region from Hank when convenient." >&2
  echo "  Record the 5-10 min demo (incl. GitHub Q&A against the chosen active mid-size OSS repo) and place it at ${VIDEO_DIR}/<name>.<ext>, then re-run." >&2
  OVERALL_FAIL=1
fi

# ---------------------------------------------------------------------------
# 3. docs/TECHNICAL.md present and covers the required topics.
# ---------------------------------------------------------------------------
echo
echo "-- [3/5] docs/TECHNICAL.md present --"
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
# 4. Submission zip built.
#
# Rebuilds the zip fresh every run (via submission/build_zip.sh) rather than
# trusting a stale artifact, then verifies its integrity and that none of
# the excluded paths (.env, secrets, .venv, node_modules, backend/var,
# .claude/claims) actually made it in.
# ---------------------------------------------------------------------------
echo
echo "-- [4/5] submission zip built --"
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
# 5. Memory suite verified against a reachable database (T-31).
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
echo "-- [5/5] memory suite (JARVIS_MEMORY_TESTS_REQUIRE_DB) --"
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
