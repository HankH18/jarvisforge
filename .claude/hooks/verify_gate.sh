#!/usr/bin/env bash
# TaskCompleted hook: exit 2 prevents the task from being marked completed.
set -uo pipefail
INPUT=$(cat)
SUBJECT=$(echo "$INPUT" | jq -r '.task.subject // .subject // empty')
TID=$(echo "$SUBJECT" | grep -oE '^T-[0-9]+' || true)
[ -z "$TID" ] && exit 0   # not a ticket task; allow

# T-24: distinguish "this ticket legitimately has no verify command" (may
# allow) from "I could not read docs/tickets.json at all" (must FAIL
# LOUDLY, never silently allow). Before this guard, an unset or bogus
# CLAUDE_PROJECT_DIR made the jq call below fail inside the $(...)
# substitution; under `set -u` with no `set -e`, that failure never
# propagated, VERIFY ended up empty, and `[ -z "$VERIFY" ] && exit 0`
# silently let the ticket close with NEITHER its own verify NOR the
# regression suite ever having run -- defeating exactly the guarantee
# this ticket exists to add. Confirmed pre-existing (reproduces
# identically on the ticket-start baseline), not introduced by the
# regression-suite change above; fixed here because this is the one file
# that gates every ticket's close.
if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
  echo "verify_gate.sh: CLAUDE_PROJECT_DIR is unset -- cannot locate docs/tickets.json to verify ${TID}. Ticket cannot be closed." >&2
  exit 2
fi
TICKETS_JSON="${CLAUDE_PROJECT_DIR}/docs/tickets.json"
if [ ! -f "$TICKETS_JSON" ]; then
  echo "verify_gate.sh: ${TICKETS_JSON} does not exist -- CLAUDE_PROJECT_DIR='${CLAUDE_PROJECT_DIR}' looks wrong. Ticket cannot be closed." >&2
  exit 2
fi
if ! jq -e . "$TICKETS_JSON" >/dev/null 2>&1; then
  echo "verify_gate.sh: ${TICKETS_JSON} is not valid JSON -- cannot verify ${TID}. Ticket cannot be closed." >&2
  exit 2
fi
VERIFY=$(jq -r --arg id "$TID" '.tickets[] | select(.id==$id) | .verify' "$TICKETS_JSON")
[ -z "$VERIFY" ] || [ "$VERIFY" = "null" ] && exit 0   # no verify declared for this ticket; legitimate allow
cd "$CLAUDE_PROJECT_DIR"
if bash -c "$VERIFY"; then
  # T-24: a ticket's own $VERIFY is scoped to its own slice of the repo by
  # design from T-1 onward (see docs/TASKS.md "Verify scope, and what
  # covers the gap") -- passing it proves nothing about a sibling scope
  # the change may have broken. Every ticket boundary therefore ALSO runs
  # the full backend+frontend regression suite below, regardless of what
  # $VERIFY itself covers. Measured on this repo (354 backend tests + ruff
  # + 68 frontend tests): ~15s wall-clock total -- cheap enough to run at
  # every completion. This is not a SPEC C7 concern: C7 caps CI (GitHub
  # Actions) cost, and this never runs in CI, only in this local hook.
  # Guarded on backend/ and frontend/ actually existing so it is a no-op
  # inside run_evidence_tests.sh's fabricated sandbox repo (docs/tickets.json
  # + throwaway scope dirs only, no backend/frontend) -- that suite proves
  # the receipt/staleness contract, not this regression gate, and must not
  # start shelling out to uv/npm against directories that don't exist there.
  if [ -d "${CLAUDE_PROJECT_DIR}/backend" ] && [ -d "${CLAUDE_PROJECT_DIR}/frontend" ]; then
    REGRESSION_OK=1
    ( cd "${CLAUDE_PROJECT_DIR}/backend" && uv run pytest -q && uv run ruff check src ) || REGRESSION_OK=0
    if [ "$REGRESSION_OK" -eq 1 ]; then
      ( cd "${CLAUDE_PROJECT_DIR}/frontend" && npm run -s test -- --run ) || REGRESSION_OK=0
    fi
    if [ "$REGRESSION_OK" -ne 1 ]; then
      echo "regression suite failed after ${TID}'s own verify passed: ${TID}'s change broke a test outside its declared scope. Ticket cannot be closed -- revert to the ticket-start commit and retry." >&2
      exit 2
    fi
  fi
  mkdir -p .claude/evidence
  SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)
  TS=$(date -u +%s)
  # Content fingerprint over the ticket's declared scope, captured from the
  # LIVE working tree right now (i.e. exactly what $VERIFY just ran
  # against, including anything not yet committed). This is what binds the
  # receipt to WHAT was verified, not just when/where — see
  # scripts/evidence_fingerprint.sh and scripts/check_evidence_staleness.sh.
  # Default is an explicit FAILURE SENTINEL, not "EMPTY-SCOPE" and never
  # blank -- if sourcing the fingerprint lib fails, or fingerprint_live
  # errors/produces no output, this is what gets written. A blank fp=
  # would be indistinguishable downstream from "no fp field at all" and
  # silently fall back into weaker handling; a wrong-but-present-looking
  # value would be worse. This sentinel can never equal a real fingerprint
  # (fp_hash always emits a hex digest), so check_evidence_staleness.sh can
  # recognize it unambiguously and report the receipt UNVERIFIABLE rather
  # than CURRENT. This never affects the exit code below either way — a
  # fingerprinting problem must not turn a passing verify into a block.
  FP="FP-COMPUTE-FAILED"
  if [ -f "${CLAUDE_PROJECT_DIR}/scripts/evidence_fingerprint.sh" ]; then
    # shellcheck disable=SC1091
    . "${CLAUDE_PROJECT_DIR}/scripts/evidence_fingerprint.sh" 2>/dev/null
    SCOPE_LIST=$(jq -r --arg id "$TID" '.tickets[] | select(.id==$id) | .scope[]? // empty' \
      "${CLAUDE_PROJECT_DIR}/docs/tickets.json" 2>/dev/null)
    if [ -z "$SCOPE_LIST" ]; then
      FP="EMPTY-SCOPE"
    elif command -v fingerprint_live >/dev/null 2>&1; then
      SCOPE_ARR=()
      while IFS= read -r g; do
        [ -n "$g" ] && SCOPE_ARR+=("$g")
      done <<<"$SCOPE_LIST"
      COMPUTED_FP="$(fingerprint_live "${SCOPE_ARR[@]}" 2>/dev/null)"
      [ -n "$COMPUTED_FP" ] && FP="$COMPUTED_FP"
    fi
  fi
  { printf 'sha=%s\n' "$SHA"; printf 'ts=%s\n' "$TS"; printf 'fp=%s\n' "$FP"; } > ".claude/evidence/${TID}.pass"
  exit 0
else
  echo "verify failed for ${TID}: ${VERIFY} — ticket cannot be closed. Revert to the ticket-start commit and retry." >&2
  exit 2
fi
