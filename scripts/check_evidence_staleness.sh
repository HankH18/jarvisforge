#!/usr/bin/env bash
# check_evidence_staleness.sh (T-22, extended T-34) - reports any ticket
# whose declared scope no longer contains exactly what its evidence receipt
# certified, AND any ticket that has no receipt at all.
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
# THE FIX: a receipt records a content fingerprint (see
# scripts/evidence_fingerprint.sh) -- a hash over every file matching the
# ticket's declared scope globs, captured at verify/close time. This
# check's verdict is then pure content comparison: recompute that same
# fingerprint right now, compare to the receipt's.
#   - Unchanged -> CURRENT. No ordering, no grace period, no message
#     convention, no linear-history assumption.
#   - Different -> STALE, full stop -- one commit or ten, prefixed with
#     this ticket's id or not, committed or merely sitting uncommitted in
#     the working tree, byte content or just the executable bit.
#
# UNVERIFIABLE (a receipt with no recorded/recomputable fingerprint): a
# receipt written before fingerprinting existed (or one where fingerprint
# computation itself failed) never captured what it verified. There is no
# sound retroactive fix, so this check does not attempt one: a receipt
# with no usable fingerprint gets its own terminal status, UNVERIFIABLE,
# which is NOT current and FAILS this gate -- an unverifiable receipt is
# strictly worse than a known-stale one, so it cannot report green either.
# The remedy is always the same: re-run that ticket's verify to mint a
# real, fingerprinted receipt. This script never does that itself; it only
# ever reads.
#
# --------------------------------------------------------------------------
# T-34: TWO RECEIPT FORMATS, TWO DIFFERENT FINGERPRINT ALGORITHMS.
#
# The OLD harness (.claude/hooks/verify_gate.sh, deleted in commit dc23222)
# wrote .claude/evidence/<T-id>.pass, a key=value file (sha=/ts=/fp=) whose
# fp= is computed by fingerprint_live() in evidence_fingerprint.sh: for
# every path `git ls-files --cached --others --exclude-standard` finds
# under the ticket's scope globs (tracked files AND untracked-but-not-
# ignored files), a "path<TAB>mode<TAB>git-hash-object-hash" line is
# built, and the whole listing is piped through sha256.
#
# The NEW harness (.claude/scripts/harness_lib.py) writes
# .claude/evidence/<T-id>.json, whose "fingerprint" field is computed by a
# DIFFERENT function (harness_lib.py's own `fingerprint()`): only files
# `git ls-files -- <glob>` finds (TRACKED ONLY -- no --others, so untracked
# files are invisible to it) are hashed as sha256(path bytes ++ sha256(raw
# file bytes)), chained into one running sha256 digest; the executable
# mode bit is never captured at all.
#
# These were confirmed to diverge by reading both implementations:
# different file enumeration (tracked-only vs tracked+untracked-not-
# ignored), different per-file hash primitive (raw sha256 of bytes vs
# git's own blob hash), different combination into the final digest, and
# mode is part of one and completely absent from the other. Recomputing a
# .json receipt's fingerprint with fingerprint_live() (the .pass
# algorithm) would therefore call EVERY .json receipt STALE regardless of
# whether anything actually changed -- a false alarm on the submission
# gate, strictly worse than not checking .json receipts at all.
#
# So a .json receipt's fingerprint is recomputed by shelling out to the
# SAME function that minted it: `python3 .claude/scripts/harness_lib.py
# fingerprint <T-id>` (harness_lib.py's own documented CLI subcommand).
# Each receipt format is judged only by the algorithm that produced it;
# neither format's fingerprint is ever compared against the other
# algorithm's output.
#
# BOTH FORMATS FOR ONE TICKET: if <T-id>.json and <T-id>.pass both exist
# for the same ticket id, the .json receipt wins -- it is the live
# harness's current format. The .pass file is treated as if absent.
#
# MISSING (T-34): every ticket id declared in docs/tickets.json is now
# enumerated -- not just whatever files happen to sit in
# .claude/evidence/. A ticket id with NEITHER a .pass NOR a .json receipt
# is MISSING: its own terminal status, counted and reported separately
# from STALE and UNVERIFIABLE, and it fails this gate. Before this
# extension, the loop walked .claude/evidence/*.pass directly, so deleting
# every .pass file made it loop over zero receipts and exit 0 -- green,
# while attesting to nothing. MISSING closes that hole: the ticket list
# (docs/tickets.json), not the evidence directory's contents, is now the
# source of what must be accounted for.
# --------------------------------------------------------------------------
#
# GITIGNORED FILES IN SCOPE are deliberately excluded from the .pass
# fingerprint (build artifacts, backend/var/ runtime output, etc must not
# be hashed) -- but that exclusion is disclosed per ticket, not left
# silent, via the [NOTE: ...] suffix on ANY ticket whose scope currently
# matches a gitignored path.
#
# ATTRIBUTION (labeling a STALE finding only, NEVER decides STALE vs
# CURRENT vs UNVERIFIABLE): once a fingerprint mismatch is found on a
# receipt that DOES have one, `git log` is used on the ticket's scope
# paths to guess whether the ticket's OWN commits are responsible (a real
# reopen) or another ticket's commits are (a broad/overlapping glob
# legitimately touched by someone else's work) -- purely to help a human
# triage. Both are reported as STALE and both fail this gate: a mismatch
# means the receipt no longer proves anything about the code that exists
# now, regardless of who caused it.
#
# Read-only: never re-runs a ticket's verify command, never writes/deletes
# a receipt.
#
# Usage: check_evidence_staleness.sh [project_dir]
#   project_dir defaults to $CLAUDE_PROJECT_DIR, then to this script's repo.
#
# Exit 0 unless at least one receipt is STALE, UNVERIFIABLE, or a ticket id
# is MISSING.

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
HARNESS_LIB="${PROJECT_DIR}/.claude/scripts/harness_lib.py"

if [ ! -f "$TICKETS_JSON" ]; then
  echo "check_evidence_staleness: ${TICKETS_JSON} not found under ${PROJECT_DIR}" >&2
  exit 2
fi

STALE_IDS=""
UNVERIFIABLE_IDS=""
MISSING_IDS=""
CHECKED=0
TOTAL_TICKETS=0

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

ALL_TICKET_IDS="$(jq -r '.tickets[]?.id // empty' "$TICKETS_JSON")"

while IFS= read -r TID; do
  [ -z "$TID" ] && continue
  TOTAL_TICKETS=$((TOTAL_TICKETS + 1))
  NUM="${TID#T-}"

  JSON_RECEIPT="${EVIDENCE_DIR}/${TID}.json"
  PASS_RECEIPT="${EVIDENCE_DIR}/${TID}.pass"

  if [ -f "$JSON_RECEIPT" ]; then
    FORMAT="json"
  elif [ -f "$PASS_RECEIPT" ]; then
    FORMAT="pass"
  else
    FORMAT="missing"
  fi

  if [ "$FORMAT" = "missing" ]; then
    echo "MISSING      ${TID}: no evidence receipt (.pass or .json) exists in ${EVIDENCE_DIR} for this ticket id declared in ${TICKETS_JSON}. Remedy: claim and close T-${NUM} to mint one."
    MISSING_IDS="${MISSING_IDS} ${TID}"
    continue
  fi

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

  # --- disclose (never silently skip) any gitignored path inside this
  # ticket's scope -- the .pass fingerprint cannot see it (and, tracked-
  # only, neither can the .json algorithm, though it doesn't claim to). ---
  IGNORED_NOTE=""
  IGNORED_LIST="$(scope_has_ignored_files "${SCOPE_ARR[@]}")"
  if [ -n "$IGNORED_LIST" ]; then
    IGNORED_COUNT="$(printf '%s\n' "$IGNORED_LIST" | grep -c .)"
    IGNORED_FIRST="$(printf '%s\n' "$IGNORED_LIST" | head -n1)"
    IGNORED_NOTE=" [NOTE: ${IGNORED_COUNT} gitignored path(s) in this scope are NOT covered by the fingerprint, e.g. '${IGNORED_FIRST}']"
  fi

  CHECKED=$((CHECKED + 1))

  if [ "$FORMAT" = "json" ]; then
    RECEIPT_SHA="$(jq -r '.commit // empty' "$JSON_RECEIPT" 2>/dev/null)"
    RECEIPT_FP="$(jq -r '.fingerprint // empty' "$JSON_RECEIPT" 2>/dev/null)"

    if [ -z "$RECEIPT_FP" ]; then
      echo "UNVERIFIABLE ${TID}: this .json receipt has no 'fingerprint' field -- cannot determine whether it is current. Remedy: re-run T-${NUM}'s verify to mint a fresh receipt.${IGNORED_NOTE}"
      UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
      continue
    fi

    if [ ! -f "$HARNESS_LIB" ]; then
      echo "UNVERIFIABLE ${TID}: cannot recompute this .json receipt's fingerprint -- ${HARNESS_LIB} does not exist, so the algorithm that minted it is unavailable. Remedy: restore the harness, then re-run T-${NUM}'s verify.${IGNORED_NOTE}"
      UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
      continue
    fi

    LIVE_FP="$(CLAUDE_PROJECT_DIR="$PROJECT_DIR" python3 "$HARNESS_LIB" fingerprint "$TID" 2>/dev/null)"
    LIVE_RC=$?
    if [ "$LIVE_RC" -ne 0 ] || [ -z "$LIVE_FP" ]; then
      echo "UNVERIFIABLE ${TID}: fingerprint recompute FAILED -- 'python3 .claude/scripts/harness_lib.py fingerprint ${TID}' exited ${LIVE_RC} / produced no output. Remedy: re-run T-${NUM}'s verify to mint a fresh receipt.${IGNORED_NOTE}"
      UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
      continue
    fi
  else
    RECEIPT_SHA="$(grep -E '^sha=' "$PASS_RECEIPT" 2>/dev/null | head -n1 | cut -d= -f2-)"
    RECEIPT_FP="$(grep -E '^fp=' "$PASS_RECEIPT" 2>/dev/null | head -n1 | cut -d= -f2-)"

    if [ -z "$RECEIPT_FP" ]; then
      echo "UNVERIFIABLE ${TID}: no content fingerprint was ever recorded for this receipt (legacy format, predates fingerprint binding) -- cannot determine whether it is current. Remedy: re-run T-${NUM}'s verify to mint a fingerprinted receipt.${IGNORED_NOTE}"
      UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
      continue
    fi
    if [ "$RECEIPT_FP" = "FP-COMPUTE-FAILED" ]; then
      echo "UNVERIFIABLE ${TID}: fingerprint computation FAILED when this receipt was written -- it cannot be compared. Remedy: re-run T-${NUM}'s verify to mint a fingerprinted receipt.${IGNORED_NOTE}"
      UNVERIFIABLE_IDS="${UNVERIFIABLE_IDS} ${TID}"
      continue
    fi

    LIVE_FP="$(fingerprint_live "${SCOPE_ARR[@]}")"
  fi

  # --- pure content comparison: no position, no ordering ---
  if [ "$LIVE_FP" = "$RECEIPT_FP" ]; then
    echo "CURRENT      ${TID}: scope content matches its receipt's fingerprint (${FORMAT})${IGNORED_NOTE}"
    continue
  fi

  REASON="$(attribute_and_label "$NUM" "$RECEIPT_SHA" "${PATHSPECS[@]}")"
  echo "STALE        ${TID}: ${REASON} (${FORMAT})${IGNORED_NOTE}"
  STALE_IDS="${STALE_IDS} ${TID}"
done <<<"$ALL_TICKET_IDS"

echo
if [ -n "$STALE_IDS" ]; then
  echo "STALE:${STALE_IDS}"
fi
if [ -n "$UNVERIFIABLE_IDS" ]; then
  echo "UNVERIFIABLE:${UNVERIFIABLE_IDS}"
fi
if [ -n "$MISSING_IDS" ]; then
  echo "MISSING:${MISSING_IDS}"
fi
if [ -n "$STALE_IDS" ] || [ -n "$UNVERIFIABLE_IDS" ] || [ -n "$MISSING_IDS" ]; then
  exit 1
else
  echo "STALE RECEIPTS: none (${CHECKED} receipt(s) checked, ${TOTAL_TICKETS} ticket id(s) in ${TICKETS_JSON} all accounted for)"
  exit 0
fi
