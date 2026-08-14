#!/usr/bin/env bash
# TaskCompleted hook: exit 2 prevents the task from being marked completed.
set -uo pipefail
INPUT=$(cat)
SUBJECT=$(echo "$INPUT" | jq -r '.task.subject // .subject // empty')
TID=$(echo "$SUBJECT" | grep -oE '^T-[0-9]+' || true)
[ -z "$TID" ] && exit 0   # not a ticket task; allow
VERIFY=$(jq -r --arg id "$TID" '.tickets[] | select(.id==$id) | .verify' \
  "${CLAUDE_PROJECT_DIR}/docs/tickets.json")
[ -z "$VERIFY" ] || [ "$VERIFY" = "null" ] && exit 0
cd "$CLAUDE_PROJECT_DIR"
if bash -c "$VERIFY"; then
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
