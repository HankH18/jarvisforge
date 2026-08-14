#!/usr/bin/env bash
# claim_ticket.sh (T-21): record a ticket claim for THIS session.
#
# Writes the canonical per-session claim record,
# .claude/claims/<session_id> -- content is just the bare ticket id. This
# is what scope_guard.sh and stop_guard.sh check first. Because it's keyed
# by session_id, no other session's write, and no deletion or truncation
# of any OTHER file (including .claude/active-ticket), can ever affect it.
#
# Also refreshes .claude/active-ticket in the legacy two-line format
# ("<ticket-id>\n<owner-session-id>\n") purely as a human-/git-visible
# "what's currently claimed" marker and a read-compatibility bridge for
# anything still looking at that one file. It is NOT required for
# enforcement any more -- the per-session record is authoritative; see
# CLAUDE.md "Claim ownership".
#
# Usage:
#   bash .claude/hooks/claim_ticket.sh T-21
#
# Relies on $CLAUDE_CODE_SESSION_ID, which Claude Code sets in the
# environment of every Bash tool call for the running session -- the same
# value hooks receive as .session_id on their stdin JSON. If it's unset
# (e.g. run outside a Claude Code session), only the legacy one-line
# active-ticket is written (no per-session record can be created without a
# session id) and a warning is printed: both guards then treat the claim
# as unidentifiable and, per their documented safe default, do not
# constrain or block anyone on it until re-claimed with an id available.
set -uo pipefail

TID="${1:-}"
if [ -z "$TID" ]; then
  echo "usage: claim_ticket.sh <ticket-id>" >&2
  exit 1
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
AT="${PROJECT_DIR}/.claude/active-ticket"
OWNER="${CLAUDE_CODE_SESSION_ID:-}"

mkdir -p "${PROJECT_DIR}/.claude/claims" "$(dirname "$AT")"
if [ -n "$OWNER" ]; then
  printf '%s\n' "$TID" > "${PROJECT_DIR}/.claude/claims/${OWNER}"
  printf '%s\n%s\n' "$TID" "$OWNER" > "$AT"
else
  printf '%s\n' "$TID" > "$AT"
  echo "warning: CLAUDE_CODE_SESSION_ID is unset; wrote an ownerless claim for $TID (no per-session record was created -- see CLAUDE.md)" >&2
fi
