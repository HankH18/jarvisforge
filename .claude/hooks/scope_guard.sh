#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): deny paths outside the active ticket's scope globs.
#
# COVERAGE NOTE (T-20): this hook is wired to the Edit and Write tools only.
# It does not intercept Bash-mediated writes (sed -i, heredocs, cp, mv, git
# checkout, etc). That gap is deliberate and disclosed, not silent -- see
# CLAUDE.md "Scope guard coverage" for why a heuristic Bash-command guard
# was rejected and what the compensating discipline is.
#
# CLAIM STORAGE (T-21): a claim is per-session, not repo-global. The
# calling session's ticket id is resolved from two places, in order:
#
#   1. .claude/claims/<session_id> -- the canonical, per-session claim
#      record (one small file per session; content is the bare ticket id,
#      nothing else). This is what claim_ticket.sh writes. Because the
#      FILENAME is the owner, no other session's write, deletion, or
#      truncation of any other file -- including .claude/active-ticket --
#      can ever affect it. This is what makes the scope boundary survive
#      "another actor deletes the shared claim file while I still hold
#      work": that scenario simply doesn't touch this session's own file.
#   2. .claude/active-ticket -- the pre-per-session shared file, kept ONLY
#      as a read-compatibility bridge for a claim already on disk in the
#      two-line "<ticket-id>\n<owner-session-id>\n" format, and only for
#      the session whose id matches the recorded owner. A legacy one-line
#      file with NO owner recorded binds no identified session -- it is
#      not applied to "everyone" as an earlier version of this hook did;
#      see "no session_id at all" below for the one narrow exception.
#
# If neither source resolves a ticket id for the calling session, that
# session owns no claim and is UNCONSTRAINED, unconditionally: owning no
# claim never triggers scope enforcement, and owning one constrains only
# that ticket's declared scope -- there is no "ownerless claim enforces
# for everyone" fallback left. A single shared file could not satisfy
# that alongside "a deleted/missing record doesn't strip protection from
# a session that holds work" at the same time; per-session storage is
# what removes the conflict, not a compromise between the two.
#
# COMPAT: if the incoming hook call carries no session_id AT ALL (every
# real Claude Code hook event does -- see CLAUDE.md "Claim ownership";
# this only happens from a hand-crafted caller predating per-session
# identity), per-session matching is impossible, so this hook falls back
# to treating .claude/active-ticket's line 1 as a plain global ticket id,
# exactly as it behaved before ownership existed. This is what keeps
# run_scope_guard_tests.sh (T-20, frozen, never sends session_id)
# meaningful and green; the real Claude Code harness never takes this path.
set -uo pipefail
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$FILE" ] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR%/}"

# Acceptance 3 (path outside the project entirely): unchanged from T-20.
case "$FILE" in
  "$PROJECT_DIR"/*) REL="${FILE#"$PROJECT_DIR"/}" ;;
  "$PROJECT_DIR") REL="" ;;
  /*) exit 0 ;;                      # absolute path outside the project dir: allow
  *) REL="$FILE" ;;                  # defensive: bare relative path, treat as project-relative
esac

# Narrow, mechanism-specific exemptions ONLY -- not a blanket .claude/* or
# docs/* pass. These are how the protocol claims a ticket, records a
# passing verify, and (new) writes its OWN per-session claim record; no
# ticket's scope lists any of them, so without this they'd be permanently
# unwritable once any ticket is active. Everything else under .claude/ and
# docs/ -- including docs/tickets.json, docs/SPEC.md, docs/DESIGN.md, and
# scope_guard.sh itself -- is scope-checked like any other file. The
# claims exemption is intentionally narrower than the other two: it only
# ever covers the CALLING session's own record, never another session's --
# writing someone else's claim file is scope-checked like any other write.
case "$REL" in
  .claude/active-ticket) exit 0 ;;
  .claude/evidence/*.pass) exit 0 ;;
esac
if [ -n "$SESSION_ID" ] && [ "$REL" = ".claude/claims/${SESSION_ID}" ]; then
  exit 0
fi

# --- Resolve TID for the calling session -------------------------------
TID=""
if [ -n "$SESSION_ID" ]; then
  CLAIM_FILE="${PROJECT_DIR}/.claude/claims/${SESSION_ID}"
  # T-21 defect #1 parity: a truncated-but-present claim file (TID=="")
  # is treated as no claim from this source, same as a missing one.
  [ -f "$CLAIM_FILE" ] && TID=$(sed -n '1p' "$CLAIM_FILE" | tr -d '[:space:]')
fi

if [ -z "$TID" ]; then
  AT="${PROJECT_DIR}/.claude/active-ticket"
  if [ -f "$AT" ]; then
    LEGACY_TID=$(sed -n '1p' "$AT" | tr -d '[:space:]')
    LEGACY_OWNER=$(sed -n '2p' "$AT" | tr -d '[:space:]')
    if [ -n "$SESSION_ID" ]; then
      # Identity known: bind ONLY on an exact owner match (acceptance 3,
      # unconditional -- an ownerless legacy file does not fall back to
      # constraining an identified session it was never recorded for).
      [ -n "$LEGACY_OWNER" ] && [ "$LEGACY_OWNER" = "$SESSION_ID" ] && TID="$LEGACY_TID"
    else
      # Identity NOT available on this call at all -- see COMPAT note.
      TID="$LEGACY_TID"
    fi
  fi
fi

# No claim resolves for this (or an unidentifiable) session: unconstrained.
[ -z "$TID" ] && exit 0

TICKETS="${PROJECT_DIR}/docs/tickets.json"
if ! jq empty "$TICKETS" >/dev/null 2>&1; then
  # T-21 defect #2: docs/tickets.json missing or malformed used to deny
  # every write, with no way to Edit/Write the very file that would fix
  # it. Deliberate choice: fail CLOSED for everything else (an unreadable
  # scope source is not proof anything goes) but carve out the one
  # self-limiting exception that lets you dig out -- writes to
  # docs/tickets.json itself. Once it's valid JSON again, normal
  # scope-checking resumes automatically; nothing else is ever exempted.
  if [ "$REL" = "docs/tickets.json" ]; then exit 0; fi
  cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Cannot verify scope for ticket $TID: docs/tickets.json is missing or not valid JSON, so no scope can be confirmed. Fix docs/tickets.json (writes to that path are exempted while it's broken) or restore it from git, then retry."}}
EOF
  exit 0
fi

# Acceptance 4: anchor at BOTH ends. The old pattern only appended "$", so
# jq's test() (a search, not a full match) let any path merely CONTAINING
# a scope prefix through, e.g. "vendor/backend/src/jarvis/cadence/x.py"
# satisfied "backend/src/jarvis/cadence/**".
MATCH=$(jq -r --arg id "$TID" --arg f "$REL" '
  .tickets[] | select(.id==$id) | .scope[] as $g
  | select(($f | test("^" + ($g | gsub("\\.";"\\.") | gsub("\\*\\*";".∞") | gsub("\\*";"[^/]*") | gsub("∞";"*")) + "$"))) | "yes"' \
  "$TICKETS" | head -n1)
if [ "$MATCH" = "yes" ]; then exit 0; fi
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$REL is outside ticket $TID scope. If genuinely required, this is a plan defect — stop and ask the human."}}
EOF
exit 0
