#!/usr/bin/env bash
# check_evidence_staleness.sh (T-22) - reports any ticket whose declared
# scope no longer contains exactly the bytes+mode its .pass receipt
# certified.
#
# WHY CONTENT, NOT COMMIT POSITION: an earlier version of this check used
# commit-graph position (a "GRACE" exclusion) to guess which post-receipt
# commit was a ticket's own harmless completion. That was unsound: it
# pardoned whatever commit happened to land next regardless of content, so
# an unprefixed commit that was the ONLY post-receipt change was silently
# pardoned, a foreign commit landing between a receipt and its own
# completion commit got pardoned INSTEAD of the real completion (false
# STALE on a clean close), and non-linear history (merges, CLAUDE.md rule
# 9 worktrees) broke the positional assumption outright.
#
# THE FIX: verify_gate.sh's TaskCompleted hook records a content
# fingerprint (see scripts/evidence_fingerprint.sh) -- a hash over the
# (path, mode, byte-hash) of every file matching the ticket's declared
# scope globs, captured from the LIVE WORKING TREE at verify time. This
# check's verdict is then pure content comparison: recompute that same
# fingerprint right now, compare to the receipt's.
#   - Unchanged -> CURRENT. No ordering, no grace period, no message
#     convention, no linear-history assumption.
#   - Different -> STALE, full stop -- one commit or ten, prefixed with
#     this ticket's id or not, committed or merely sitting uncommitted in
#     the working tree, byte content or just the executable bit.
#
# UNVERIFIABLE (a receipt with no recorded fingerprint): a receipt written
# before T-22 added fingerprinting (or one where fingerprinting itself
# failed at write time -- see FP-COMPUTE-FAILED below) never captured what
# it verified. An earlier version of this check tried to infer a baseline
# for these by picking the commit "closest in time" to the receipt's bare
# timestamp -- also unsound: a same-day reopen can land an unverified
# regression closer in time than the real completion, and the checker
# would report a genuinely stale receipt CURRENT. There is no sound
# retroactive fix, so this check does not attempt one: a receipt with no
# fp= gets its own terminal status, UNVERIFIABLE, which is NOT current and
# FAILS this gate (see exit code below) -- acceptance 3 says a stale
# receipt cannot report green, and an unverifiable one is strictly worse
# than a known-stale one, so it cannot report green either. The remedy is
# always the same and is stated in the message: re-run that ticket's
# verify to mint a real, fingerprinted receipt. This script never does
# that itself (re-running another ticket's verify is an explicit
# non-goal); it only ever reads.
#
# ATTRIBUTION (labeling a STALE finding only, NEVER decides STALE vs
# CURRENT vs UNVERIFIABLE): once a fingerprint mismatch is found on a
# receipt that DOES have one, `git log` is used on the ticket's scope
# paths to guess whether the ticket's OWN commits are responsible (a real
# reopen) or another ticket's commits are (a broad/overlapping glob
# legitimately touched by someone else's work) -- purely to help a human
# triage. Both are reported as STALE and both fail this gate: a byte/mode
# mismatch means the receipt no longer proves anything about the code that
# exists now, regardless of who caused it.
#
# GITIGNORED FILES IN SCOPE are deliberately excluded from every
# fingerprint (build artifacts, backend/var/ runtime output, etc must not
# be hashed) -- but that exclusion is disclosed per ticket, not left
# silent, via the [NOTE: ...] suffix on ANY ticket whose scope currently
# matches a gitignored path.
#
# Read-only: never re-runs a ticket's verify command, never writes/deletes
# a receipt.
#
# Usage: check_evidence_staleness.sh [project_dir]
#   project_dir defaults to $CLAUDE_PROJECT_DIR, then to this script's repo.
#
# Exit 0 unless at least one receipt is STALE or UNVERIFIABLE.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_DIR="${1:-${CLAUDE_PROJECT_DIR:-${DEFAULT_PROJECT_DIR}}}"

cd "$PROJECT_DIR" 2>/dev/null || {
  echo "check_evidence_staleness: cannot cd to project dir '${PROJECT_DIR}'" >&2
  exit 2
}

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/evidence_fingerprint.sh"

TICKETS_JSON="docs/tickets.json"
EVIDENCE_DIR=".claude/evidence"

if [ ! -f "$TICKETS_JSON" ]; then
  echo "check_evidence_staleness: ${TICKETS_JSON} not found under ${PROJECT_DIR}" >&2
  exit 2
fi

STALE_IDS=""
UNVERIFIABLE_IDS=""
CHECKED=0

# attribute_and_label <NUM> <receipt_sha> <pathspec...>
# Prints a short human-readable reason for a STALE finding on a receipt
# that DOES have a recorded fingerprint: whether the ticket's own commits
# touched the scope since the receipt's sha, or only other tickets' did.
attribute_and_label() {
  local num="$1" ref_sha="$2"
  shift 2
  local pathspecs=("$@")
  local scope_commits
  scope_commits="$(git log --format='%H%x09%s' -- "${pathspecs[@]}" 2>/dev/null)"

  local has_own=0 own_desc="" has_other=0 other_desc=""
  while IFS=$'\t' read -r sha subj; do
    [ -z "$sha" ] && continue
    if [ -n "$ref_sha" ] && [ "$ref_sha" != "unknown" ]; then
      [ "$sha" = "$ref_sha" ] && continue
      git merge-base --is-ancestor "$sha" "$ref_sha" 2>/dev/null && continue
    fi
    if echo "$subj" | grep -qE "^T-${num}:"; then
      has_own=1
      own_desc="${sha:0:12} ${subj}"
    elif [ "$has_other" -eq 0 ]; then
      has_other=1
      other_desc="${sha:0:12} ${subj}"
    fi
  done <<<"$scope_commits"

  if [ "$has_own" -eq 1 ]; then
    printf 'reopened -- %s' "$own_desc"
  elif [ "$has_other" -eq 1 ]; then
    printf 'content changed by a different ticket (not necessarily T-%s'"'"'s own regression) -- %s' "$num" "$other_desc"
  else
    printf 'content or mode differs from the receipt (uncommitted working-tree change, or mode-only change with no committing history found)'
  fi
}

shopt -s nullglob
for receipt in "$EVIDENCE_DIR"/T-*.pass; do
  TID="$(basename "$receipt" .pass)"
  NUM="${TID#T-}"
  CHECKED=$((CHECKED + 1))

  RECEIPT_SHA="$(grep -E '^sha=' "$receipt" 2>/dev/null | head -n1 | cut -d= -f2-)"
  RECEIPT_FP="$(grep -E '^fp=' "$receipt" 2>/dev/null | head -n1 | cut -d= -f2-)"

  SCOPE_GLOBS=""
  while IFS= read -r line; do
    [ -n "$line" ] && SCOPE_GLOBS="${SCOPE_GLOBS}${line}"$'\n'
  done < <(jq -r --arg id "$TID" '.tickets[]? | select(.id==$id) | .scope[]? // empty' "$TICKETS_JSON")

  if [ -z "$SCOPE_GLOBS" ]; then
    echo "SKIP        ${TID}: no scope declared for this ticket id in ${TICKETS_JSON}"
    continue
  fi

  PATHSPECS=()
  SCOPE_ARR=()
  while IFS= read -r glob; do
    if [ -n "$glob" ]; then
      PATHSPECS+=(":(glob)${glob}")
      SCOPE_ARR+=("$glob")
    fi
  done <<<"$SCOPE_GLOBS"

  # --- Gap 2: disclose (never silently skip) any gitignored path inside
  # this ticket's scope -- the fingerprint below cannot see it. ---
  IGNORED_NOTE=""
  IGNORED_LIST="$(scope_has_ignored_files "${SCOPE_ARR[@]}")"
  if [ -n "$IGNORED_LIST" ]; then
    IGNORED_COUNT="$(printf '%s\n' "$IGNORED_LIST" | grep -c .)"
    IGNORED_FIRST="$(printf '%s\n' "$IGNORED_LIST" | head -n1)"
    IGNORED_NOTE=" [NOTE: ${IGNORED_COUNT} gitignored path(s) in this scope are NOT covered by the fingerprint, e.g. '${IGNORED_FIRST}']"
  fi

  # --- Gap 3 + Gap 4: no usable fingerprint recorded -> UNVERIFIABLE,
  # never inferred, never CURRENT. ---
  if [ -z "$RECEIPT_FP" ]; then
    echo "UNVERIFIABLE ${TID}: no content fingerprint was ever recorded for this receipt (legacy format, predates T-22's fingerprint binding) -- cannot determine whether it is current. Remedy: re-run T-${NUM}'s verify to mint a fingerprinted receipt.${IGNORED_NOTE}"
    UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
    continue
  fi
  if [ "$RECEIPT_FP" = "FP-COMPUTE-FAILED" ]; then
    echo "UNVERIFIABLE ${TID}: fingerprint computation FAILED when this receipt was written -- it cannot be compared. Remedy: re-run T-${NUM}'s verify to mint a fingerprinted receipt.${IGNORED_NOTE}"
    UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
    continue
  fi

  # --- pure content comparison: no position, no ordering ---
  LIVE_FP="$(fingerprint_live "${SCOPE_ARR[@]}")"

  if [ "$LIVE_FP" = "$RECEIPT_FP" ]; then
    echo "CURRENT      ${TID}: scope content (and mode) matches its receipt's fingerprint${IGNORED_NOTE}"
    continue
  fi

  REASON="$(attribute_and_label "$NUM" "$RECEIPT_SHA" "${PATHSPECS[@]}")"
  echo "STALE        ${TID}: ${REASON}${IGNORED_NOTE}"
  STALE_IDS="${STALE_IDS} ${TID}"
done

echo
if [ -n "$STALE_IDS" ]; then
  echo "STALE:${STALE_IDS}"
fi
if [ -n "$UNVERIFIABLE_IDS" ]; then
  echo "UNVERIFIABLE:${UNVERIFIABLE_IDS}"
fi
if [ -n "$STALE_IDS" ] || [ -n "$UNVERIFIABLE_IDS" ]; then
  exit 1
else
  echo "STALE RECEIPTS: none (${CHECKED} receipt(s) checked)"
  exit 0
fi
