#!/usr/bin/env bash
# Stop hook: block a session from stopping while ITS OWN claimed ticket has
# no passing verification receipt.
#
# CLAIM STORAGE (T-21): see scope_guard.sh for the full note. This
# session's ticket id is resolved from its own per-session claim record,
# .claude/claims/<session_id>, first -- immune to any other file's
# deletion or truncation -- falling back to .claude/active-ticket ONLY if
# that file's recorded owner (line 2) matches this session. An ownerless
# legacy claim, or no claim at all, or an unidentifiable caller (no
# session_id on the call): none of these give this hook anyone to block,
# so it doesn't block anyone -- this hook is a courtesy reminder, not the
# completeness gate (verify_gate.sh on TaskCompleted is that).
set -uo pipefail
INPUT=$(cat)
ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$ACTIVE" = "true" ] && exit 0        # REQUIRED guard: prevents infinite loop
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

TID=""
if [ -n "$SESSION_ID" ]; then
  CLAIM_FILE="${CLAUDE_PROJECT_DIR}/.claude/claims/${SESSION_ID}"
  # T-21 defect #1 parity: an empty/truncated claim file = no claim.
  [ -f "$CLAIM_FILE" ] && TID=$(sed -n '1p' "$CLAIM_FILE" | tr -d '[:space:]')
fi

if [ -z "$TID" ] && [ -n "$SESSION_ID" ]; then
  AT="${CLAUDE_PROJECT_DIR}/.claude/active-ticket"
  if [ -f "$AT" ]; then
    LEGACY_TID=$(sed -n '1p' "$AT" | tr -d '[:space:]')
    LEGACY_OWNER=$(sed -n '2p' "$AT" | tr -d '[:space:]')
    [ -n "$LEGACY_OWNER" ] && [ "$LEGACY_OWNER" = "$SESSION_ID" ] && TID="$LEGACY_TID"
  fi
fi

# No claim resolves for this session: nothing to block.
[ -z "$TID" ] && exit 0

[ -f "${CLAUDE_PROJECT_DIR}/.claude/evidence/${TID}.pass" ] && exit 0
echo "{\"decision\":\"block\",\"reason\":\"Ticket ${TID} is claimed by this session but has no passing verification. Finish it (verify must pass), revert and retry, or release the claim and explain why before stopping.\"}"
exit 0
