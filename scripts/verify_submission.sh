#!/usr/bin/env bash
# verify_submission.sh -- T-13's submission checklist gate.
#
# Seeded by T-22 with exactly one check: no ticket's .claude/evidence/*.pass
# receipt may be stale (superseded by a later commit to that ticket's
# declared scope). T-13 extends this script (does not replace it) with the
# four remaining checks its own acceptance criterion 3 names: repo private,
# video file present, docs/TECHNICAL.md present, submission zip built.
# T-31 adds a sixth check: the memory suite (backend/tests/memory), run
# with JARVIS_MEMORY_TESTS_REQUIRE_DB=1 so an unreachable memory database
# is a hard failure here, never a silent skip -- this script is that flag's
# one owning context (see backend/tests/memory/conftest.py and
# docs/TECHNICAL.md §5 for what it needs).
#
# Honesty convention (T-23 precedent, restated here because it governs every
# check below): a condition this script cannot actually verify is a FAIL,
# never a silent SKIP or a pass-by-default. Every check says plainly what it
# checked and why it failed when it fails.
#
# This gate is EXPECTED to fail right now and that is correct behavior, not
# a bug in this script: the repo has no git remote (so "private" cannot be
# confirmed), Cartesia TTS is returning HTTP 402 so no demo video can be
# recorded, and the zip cannot be emailed by this script (that is a human
# action). See docs/SUBMISSION.md for the human action list this gate's
# failures map onto.
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
echo "-- [1/6] evidence freshness gate (T-22) --"
if "${SCRIPT_DIR}/check_evidence_staleness.sh" "${PROJECT_DIR}"; then
  echo "verify_submission: evidence freshness OK"
else
  echo "verify_submission: FAILED -- stale or unverifiable ticket evidence exists; re-run the affected ticket's verify to mint a fresh, fingerprinted receipt before submitting." >&2
  OVERALL_FAIL=1
fi

# ---------------------------------------------------------------------------
# 2. Repo-private confirmation.
#
# The Gauntlet AI submission target is a SELF-HOSTED GITLAB at
# labs.gauntletai.com (user hankholcomb) -- NOT github.com (see the global
# Gauntlet AI setup instructions and CLAUDE.md). This repo currently has NO
# git remote at all, so this check cannot be performed today; it fails
# honestly and says exactly what is missing rather than checking the wrong
# host or assuming private. This script never pushes, never creates a
# remote, and never changes repo visibility -- it only ever reads.
# ---------------------------------------------------------------------------
echo
echo "-- [2/6] repo-private confirmation --"
REMOTES="$(cd "$PROJECT_DIR" && git remote -v 2>/dev/null | awk '{print $1, $2}' | sort -u)"

if [ -z "$REMOTES" ]; then
  echo "verify_submission: FAILED -- repo-private check: this repo has NO git remote configured ('git remote -v' is empty)." >&2
  echo "  The submission target is a self-hosted GitLab at labs.gauntletai.com (user hankholcomb), not github.com." >&2
  echo "  Cannot confirm the repo is private until a remote exists. Remedy (push-to-create makes the project private automatically):" >&2
  echo "    git push https://labs.gauntletai.com/hankholcomb/<repo>.git main" >&2
  echo "  Then re-run this check." >&2
  OVERALL_FAIL=1
else
  GITLAB_REMOTE_URL=""
  while IFS=$'\n' read -r line; do
    [ -z "$line" ] && continue
    url="$(echo "$line" | awk '{print $2}')"
    case "$url" in
      *labs.gauntletai.com*) GITLAB_REMOTE_URL="$url" ;;
    esac
  done <<<"$REMOTES"

  if [ -z "$GITLAB_REMOTE_URL" ]; then
    echo "verify_submission: FAILED -- repo-private check: a git remote exists, but none of it points at labs.gauntletai.com (the Gauntlet AI submission target). Found:" >&2
    echo "$REMOTES" | sed 's/^/    /' >&2
    echo "  Never assume gitlab.com or github.com for a Gauntlet project. Point a remote at https://labs.gauntletai.com/hankholcomb/<repo>.git and re-run." >&2
    OVERALL_FAIL=1
  else
    # Derive the GitLab project path (owner/repo) from the remote URL, both
    # https://host/owner/repo(.git) and git@host:owner/repo(.git) shapes.
    PROJECT_PATH="$(echo "$GITLAB_REMOTE_URL" | sed -E 's#^[a-zA-Z]+://[^/]+/##; s#^[^@]+@[^:]+:##; s#\.git$##')"
    echo "verify_submission: found a labs.gauntletai.com remote (${GITLAB_REMOTE_URL}); checking visibility via the GitLab API (read-only)..."

    GL_PASSWORD="$(printf 'protocol=https\nhost=labs.gauntletai.com\n\n' | git credential fill 2>/dev/null | awk -F= '/^password=/{print $2}')"

    if [ -z "$GL_PASSWORD" ]; then
      echo "verify_submission: FAILED -- repo-private check: no credential found in the macOS keychain for labs.gauntletai.com (git credential fill returned no password). Cannot query the GitLab API to confirm visibility." >&2
      OVERALL_FAIL=1
    else
      ENCODED_PATH="$(python3 -c "import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=''))" "$PROJECT_PATH" 2>/dev/null)"
      API_URL="https://labs.gauntletai.com/api/v4/projects/${ENCODED_PATH}"
      HTTP_BODY="$(curl -sS -w $'\n%{http_code}' --max-time 15 -H "PRIVATE-TOKEN: ${GL_PASSWORD}" "$API_URL" 2>/dev/null)"
      HTTP_STATUS="$(echo "$HTTP_BODY" | tail -n1)"
      HTTP_JSON="$(echo "$HTTP_BODY" | sed '$d')"
      GL_PASSWORD=""  # never keep the secret around longer than needed

      if [ "$HTTP_STATUS" = "200" ]; then
        VISIBILITY="$(echo "$HTTP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('visibility',''))" 2>/dev/null)"
        if [ "$VISIBILITY" = "private" ]; then
          echo "verify_submission: repo-private check PASSED -- GitLab reports visibility=private for ${PROJECT_PATH}."
        else
          echo "verify_submission: FAILED -- repo-private check: GitLab reports visibility='${VISIBILITY:-unknown}' for ${PROJECT_PATH}, not 'private'. Fix in GitLab project settings (Settings > General > Visibility) and re-run." >&2
          OVERALL_FAIL=1
        fi
      elif [ "$HTTP_STATUS" = "401" ] || [ "$HTTP_STATUS" = "403" ]; then
        echo "verify_submission: FAILED -- repo-private check: GitLab API returned HTTP ${HTTP_STATUS} for ${API_URL}. The saved token is likely write-only (write_repository scope) without api/read_api scope -- an API 401 while 'git push' works means the token is write-only, not invalid. Mint or use a token with read_api scope, or confirm visibility manually in the GitLab UI." >&2
        OVERALL_FAIL=1
      else
        echo "verify_submission: FAILED -- repo-private check: GitLab API call to ${API_URL} returned HTTP ${HTTP_STATUS:-<no response>}, expected 200. Cannot confirm visibility." >&2
        OVERALL_FAIL=1
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3. Demo video file present.
#
# The video cannot be recorded right now: Cartesia TTS returns HTTP 402
# ("Model credits limit reached", independently confirmed -- see
# docs/latency-report.md and docs/SUBMISSION.md), so the deployed app
# currently produces no audio at all, and a demo of a voice assistant that
# cannot speak is not possible. Separately, SPEC.md's Open question 1 means
# even the required *content* checklist for the video is not finalized yet.
# This check looks for a real video file and fails honestly when none
# exists, rather than accepting a placeholder.
# ---------------------------------------------------------------------------
echo
echo "-- [3/6] demo video file present --"
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
  echo "  BLOCKED: Cartesia TTS is returning HTTP 402 (Model credits limit reached) on the deployed app -- it currently cannot speak any response, so a voice-assistant demo cannot be recorded. Restore Cartesia billing/credits first." >&2
  echo "  ALSO BLOCKED: SPEC.md Open question 1 -- the required video content checklist (and the Attachments section) was cut off in the provided PRD screenshots and is not finalized. Supply that region before recording." >&2
  echo "  Once both are resolved: record the 5-10 min demo (incl. GitHub Q&A against the chosen active mid-size OSS repo) and place it at ${VIDEO_DIR}/<name>.<ext>, then re-run." >&2
  OVERALL_FAIL=1
fi

# ---------------------------------------------------------------------------
# 4. docs/TECHNICAL.md present and covers the required topics.
# ---------------------------------------------------------------------------
echo
echo "-- [4/6] docs/TECHNICAL.md present --"
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
# 5. Submission zip built.
#
# Rebuilds the zip fresh every run (via submission/build_zip.sh) rather than
# trusting a stale artifact, then verifies its integrity and that none of
# the excluded paths (.env, secrets, .venv, node_modules, backend/var,
# .claude/claims) actually made it in.
# ---------------------------------------------------------------------------
echo
echo "-- [5/6] submission zip built --"
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
# 6. Memory suite verified against a reachable database (T-31).
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
echo "-- [6/6] memory suite (JARVIS_MEMORY_TESTS_REQUIRE_DB) --"
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
