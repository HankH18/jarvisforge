#!/usr/bin/env bash
# run_claim_tests.sh (T-21) - exercises PER-SESSION claim storage in BOTH
# scope_guard.sh (PreToolUse) and stop_guard.sh (Stop), against the REAL
# hook scripts, in a sandboxed CLAUDE_PROJECT_DIR. Never touches the real
# .claude/active-ticket, .claude/claims/, or docs/tickets.json.
#
# Before T-21, .claude/active-ticket held a bare ticket id and nothing
# else, so both guards keyed off repo-global state with no notion of WHO
# claimed the ticket. A first pass at T-21 added an OWNER line to that one
# shared file -- which fixed owner-vs-non-owner discrimination, but kept a
# single point of failure: deleting/truncating the ONE shared file could
# still silently strip a real owner's protection, and an "ownerless"
# shared claim still had to choose between under- and over-enforcing for
# everyone. Both are structural consequences of a single repo-global
# record, not something a smarter read of that one file can fix.
#
# This suite binds the LITERAL acceptance criteria against the fix: claim
# state moved to one file per session, .claude/claims/<session_id>. Because
# the filename IS the owner, no other session's write or deletion of any
# OTHER file (including .claude/active-ticket) can ever affect it. It proves:
#
#   1. acceptance 4, literally: a session that claims a ticket and writes
#      out-of-scope is denied; another actor then deletes
#      .claude/active-ticket (the shared/legacy file); the SAME session's
#      IDENTICAL write is STILL denied, because its own claim never lived
#      there.
#   2. acceptance 3, UNCONDITIONALLY: a session that owns no claim is never
#      scope-constrained and never stop-blocked -- including when a claim
#      exists but is ownerless (no fallback that enforces it for everyone
#      "just in case"), and this holds concurrently with a different
#      session that DOES own a claim being fully constrained/blocked at
#      the same moment (proving per-session isolation, not "one shared
#      on/off switch").
#   3. defect #1 (empty claim file) and defect #2 (malformed/missing
#      docs/tickets.json) still behave exactly as before, now checked
#      against both the per-session and legacy-bridge lookup paths.
#   4. the legacy .claude/active-ticket bridge still honours an OWNED
#      two-line claim for the matching session (so the claim already on
#      disk before this suite runs keeps working without being touched).
#   5. the one COMPAT exception: a hook call with no session_id AT ALL
#      (what run_scope_guard_tests.sh's frozen T-20 suite sends -- real
#      Claude Code hook events always include one) falls back to the
#      pre-ownership global behaviour, so that frozen suite stays
#      meaningful. This is exercised here directly, distinctly from the
#      "ownerless claim, known session_id" case above, so the two are
#      never conflated.
#
# Every case that represents a real behaviour change also runs against
# embedded byte-for-byte snapshots of TWO prior versions of these hooks,
# to prove it's a real fix and not a vacuous test:
#   OLD    = pre-T-21 (no owner concept at all; what T-20 left on disk)
#   ROUND1 = the single-shared-file-with-owner-line version (T-21's first
#            pass): fixes owner vs non-owner, but still fails the literal
#            acceptance-4 reproduction and over-enforces the ownerless case
#
# Usage: bash .claude/hooks/tests/run_claim_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCOPE_HOOK="${REPO_ROOT}/.claude/hooks/scope_guard.sh"
STOP_HOOK="${REPO_ROOT}/.claude/hooks/stop_guard.sh"
CLAIM_HELPER="${REPO_ROOT}/.claude/hooks/claim_ticket.sh"
TICKETS="${REPO_ROOT}/docs/tickets.json"

FAILURES=0
CASES_RUN=0

pass() { CASES_RUN=$((CASES_RUN + 1)); printf 'PASS: %s\n' "$1"; }
fail() { CASES_RUN=$((CASES_RUN + 1)); FAILURES=$((FAILURES + 1)); printf 'FAIL: %s -- %s\n' "$1" "$2"; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

SANDBOX="${WORK_DIR}/sandbox"
mkdir -p "${SANDBOX}/.claude/hooks" "${SANDBOX}/.claude/evidence" "${SANDBOX}/.claude/claims" "${SANDBOX}/docs" \
  "${SANDBOX}/backend/src/jarvis/tools/weather" "${SANDBOX}/backend/src/jarvis/cadence"

# Real hooks, real ticket file, real helper -- copied read-only into the
# sandbox. docs/tickets.json is restored to this exact copy after the
# defect-#2 section corrupts it.
cp "$SCOPE_HOOK" "${SANDBOX}/.claude/hooks/scope_guard.sh"
cp "$STOP_HOOK" "${SANDBOX}/.claude/hooks/stop_guard.sh"
cp "$CLAIM_HELPER" "${SANDBOX}/.claude/hooks/claim_ticket.sh"
chmod +x "${SANDBOX}/.claude/hooks/scope_guard.sh" "${SANDBOX}/.claude/hooks/stop_guard.sh" "${SANDBOX}/.claude/hooks/claim_ticket.sh"
cp "$TICKETS" "${SANDBOX}/docs/tickets.json"
cp "$TICKETS" "${WORK_DIR}/tickets.json.good"

# ===========================================================================
# Embedded snapshots, used ONLY to prove a case is a real fix (never used
# to drive the real project's hooks).
# ===========================================================================

# OLD: byte-for-byte as T-20 left scope_guard.sh -- no session_id
# awareness, no empty-claim handling, no malformed-tickets.json handling.
cat > "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" <<'OLDSCOPE'
#!/usr/bin/env bash
set -uo pipefail
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR%/}"

case "$FILE" in
  "$PROJECT_DIR"/*) REL="${FILE#"$PROJECT_DIR"/}" ;;
  "$PROJECT_DIR") REL="" ;;
  /*) exit 0 ;;
  *) REL="$FILE" ;;
esac

AT="${PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0
TID=$(head -n1 "$AT" | tr -d '[:space:]')

case "$REL" in
  .claude/active-ticket) exit 0 ;;
  .claude/evidence/*.pass) exit 0 ;;
esac

MATCH=$(jq -r --arg id "$TID" --arg f "$REL" '
  .tickets[] | select(.id==$id) | .scope[] as $g
  | select(($f | test("^" + ($g | gsub("\\.";"\\.") | gsub("\\*\\*";".∞") | gsub("\\*";"[^/]*") | gsub("∞";"*")) + "$"))) | "yes"' \
  "${PROJECT_DIR}/docs/tickets.json" | head -n1)
if [ "$MATCH" = "yes" ]; then exit 0; fi
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$REL is outside ticket $TID scope. If genuinely required, this is a plan defect — stop and ask the human."}}
EOF
exit 0
OLDSCOPE
chmod +x "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh"

# OLD: stop_guard.sh as it stood before T-21 -- no owner concept at all.
cat > "${SANDBOX}/.claude/hooks/stop_guard_OLD.sh" <<'OLDSTOP'
#!/usr/bin/env bash
set -uo pipefail
INPUT=$(cat)
ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$ACTIVE" = "true" ] && exit 0        # REQUIRED guard: prevents infinite loop
AT="${CLAUDE_PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0
TID=$(head -n1 "$AT" | tr -d '[:space:]')
[ -f "${CLAUDE_PROJECT_DIR}/.claude/evidence/${TID}.pass" ] && exit 0
echo "{\"decision\":\"block\",\"reason\":\"Ticket ${TID} is claimed but has no passing verification. Finish it (verify must pass), revert and retry, or release the claim and explain why before stopping.\"}"
exit 0
OLDSTOP
chmod +x "${SANDBOX}/.claude/hooks/stop_guard_OLD.sh"

# ROUND1: T-21's first pass -- single shared .claude/active-ticket file
# with an owner line. Fixes owner-vs-non-owner, but a deleted/truncated
# shared file still strips a real owner's protection, and an ownerless
# claim still had to pick one of "enforce for everyone" (scope) or "block
# no one" (stop) as a blanket default -- exactly what per-session storage
# removes the need for.
cat > "${SANDBOX}/.claude/hooks/scope_guard_ROUND1.sh" <<'R1SCOPE'
#!/usr/bin/env bash
set -uo pipefail
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$FILE" ] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR%/}"

case "$FILE" in
  "$PROJECT_DIR"/*) REL="${FILE#"$PROJECT_DIR"/}" ;;
  "$PROJECT_DIR") REL="" ;;
  /*) exit 0 ;;
  *) REL="$FILE" ;;
esac

AT="${PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0

TID=$(sed -n '1p' "$AT" | tr -d '[:space:]')
OWNER=$(sed -n '2p' "$AT" | tr -d '[:space:]')

[ -z "$TID" ] && exit 0

case "$REL" in
  .claude/active-ticket) exit 0 ;;
  .claude/evidence/*.pass) exit 0 ;;
esac

if [ -n "$OWNER" ] && { [ -z "$SESSION_ID" ] || [ "$SESSION_ID" != "$OWNER" ]; }; then
  exit 0
fi
# ownerless claim: ROUND1 enforces for EVERY session (global fallback).

TICKETS="${PROJECT_DIR}/docs/tickets.json"
if ! jq empty "$TICKETS" >/dev/null 2>&1; then
  if [ "$REL" = "docs/tickets.json" ]; then exit 0; fi
  cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Cannot verify scope for ticket $TID: docs/tickets.json is missing or not valid JSON, so no scope can be confirmed. Fix docs/tickets.json (writes to that path are exempted while it's broken) or restore it from git, then retry."}}
EOF
  exit 0
fi

MATCH=$(jq -r --arg id "$TID" --arg f "$REL" '
  .tickets[] | select(.id==$id) | .scope[] as $g
  | select(($f | test("^" + ($g | gsub("\\.";"\\.") | gsub("\\*\\*";".∞") | gsub("\\*";"[^/]*") | gsub("∞";"*")) + "$"))) | "yes"' \
  "$TICKETS" | head -n1)
if [ "$MATCH" = "yes" ]; then exit 0; fi
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$REL is outside ticket $TID scope. If genuinely required, this is a plan defect — stop and ask the human."}}
EOF
exit 0
R1SCOPE
chmod +x "${SANDBOX}/.claude/hooks/scope_guard_ROUND1.sh"

cat > "${SANDBOX}/.claude/hooks/stop_guard_ROUND1.sh" <<'R1STOP'
#!/usr/bin/env bash
set -uo pipefail
INPUT=$(cat)
ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$ACTIVE" = "true" ] && exit 0
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
AT="${CLAUDE_PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0
TID=$(sed -n '1p' "$AT" | tr -d '[:space:]')
[ -z "$TID" ] && exit 0
OWNER=$(sed -n '2p' "$AT" | tr -d '[:space:]')
if [ -z "$OWNER" ]; then
  exit 0   # ownerless: ROUND1 blocks no one
fi
[ -n "$SESSION_ID" ] && [ "$SESSION_ID" = "$OWNER" ] || exit 0
[ -f "${CLAUDE_PROJECT_DIR}/.claude/evidence/${TID}.pass" ] && exit 0
echo "{\"decision\":\"block\",\"reason\":\"Ticket ${TID} is claimed by this session but has no passing verification. Finish it (verify must pass), revert and retry, or release the claim and explain why before stopping.\"}"
exit 0
R1STOP
chmod +x "${SANDBOX}/.claude/hooks/stop_guard_ROUND1.sh"

# ===========================================================================
# Fixtures
# ===========================================================================
SESSION_A="11111111-1111-1111-1111-111111111111"   # owns T-3
SESSION_B="22222222-2222-2222-2222-222222222222"   # owns nothing (or T-9 later)
SESSION_C="33333333-3333-3333-3333-333333333333"   # owns nothing, for isolation proof

claim_dir_file() { printf '%s' "${SANDBOX}/.claude/claims/$1"; }

set_per_session_claim() { printf '%s\n' "$2" > "$(claim_dir_file "$1")"; }   # <session> <tid>
clear_per_session_claim() { rm -f "$(claim_dir_file "$1")"; }
set_per_session_claim_empty() { : > "$(claim_dir_file "$1")"; }

set_legacy_owned()     { printf '%s\n%s\n' "$1" "$2" > "${SANDBOX}/.claude/active-ticket"; }   # <tid> <owner>
set_legacy_ownerless() { printf '%s\n' "$1" > "${SANDBOX}/.claude/active-ticket"; }
set_legacy_empty()     { : > "${SANDBOX}/.claude/active-ticket"; }
clear_legacy()          { rm -f "${SANDBOX}/.claude/active-ticket"; }
clear_all_claims()      { rm -rf "${SANDBOX}/.claude/claims"; mkdir -p "${SANDBOX}/.claude/claims"; clear_legacy; }

write_pass()  { touch "${SANDBOX}/.claude/evidence/$1.pass"; }
clear_pass()  { rm -f "${SANDBOX}/.claude/evidence/$1.pass"; }

restore_tickets_json() { cp "${WORK_DIR}/tickets.json.good" "${SANDBOX}/docs/tickets.json"; }

# run_scope_hook <hookpath> <filepath> <session_id-or-empty>
run_scope_hook() {
  local hookpath="$1" filepath="$2" sid="$3"
  if [ -n "$sid" ]; then
    printf '{"tool_input":{"file_path":"%s"},"session_id":"%s"}' "$filepath" "$sid" |
      CLAUDE_PROJECT_DIR="$SANDBOX" "$hookpath"
  else
    printf '{"tool_input":{"file_path":"%s"}}' "$filepath" |
      CLAUDE_PROJECT_DIR="$SANDBOX" "$hookpath"
  fi
}
# run_stop_hook <hookpath> <session_id-or-empty> [<stop_hook_active>]
run_stop_hook() {
  local hookpath="$1" sid="$2" active="${3:-false}"
  if [ -n "$sid" ]; then
    printf '{"stop_hook_active":%s,"session_id":"%s"}' "$active" "$sid" |
      CLAUDE_PROJECT_DIR="$SANDBOX" "$hookpath"
  else
    printf '{"stop_hook_active":%s}' "$active" |
      CLAUDE_PROJECT_DIR="$SANDBOX" "$hookpath"
  fi
}

is_deny()  { echo "$1" | grep -q '"permissionDecision":"deny"'; }
is_block() { echo "$1" | grep -q '"decision":"block"'; }

assert_scope_allow() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "$SCOPE_HOOK" "$filepath" "$sid")"
  if is_deny "$out"; then fail "$label" "expected ALLOW, got DENY: $out"; else pass "$label"; fi
}
assert_scope_deny() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "$SCOPE_HOOK" "$filepath" "$sid")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected DENY, got ALLOW (empty output)"; fi
}
assert_scope_old_allowed() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" "$filepath" "$sid")"
  if is_deny "$out"; then fail "$label" "expected OLD hook to ALLOW (proving the bug), got DENY: $out"; else pass "$label"; fi
}
assert_scope_old_denied() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" "$filepath" "$sid")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected OLD hook to DENY (proving the bug), got ALLOW"; fi
}
assert_scope_round1_allowed() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "${SANDBOX}/.claude/hooks/scope_guard_ROUND1.sh" "$filepath" "$sid")"
  if is_deny "$out"; then fail "$label" "expected ROUND1 hook to ALLOW (proving the flaw), got DENY: $out"; else pass "$label"; fi
}
assert_scope_round1_denied() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "${SANDBOX}/.claude/hooks/scope_guard_ROUND1.sh" "$filepath" "$sid")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected ROUND1 hook to DENY (proving the flaw), got ALLOW"; fi
}

assert_stop_allow() {
  local label="$1" sid="$2" active="${3:-false}"
  local out; out="$(run_stop_hook "$STOP_HOOK" "$sid" "$active")"
  if is_block "$out"; then fail "$label" "expected ALLOW (no block), got BLOCK: $out"; else pass "$label"; fi
}
assert_stop_block() {
  local label="$1" sid="$2"
  local out; out="$(run_stop_hook "$STOP_HOOK" "$sid")"
  if is_block "$out"; then pass "$label"; else fail "$label" "expected BLOCK, got ALLOW (empty output)"; fi
}
assert_stop_old_blocked() {
  local label="$1" sid="$2"
  local out; out="$(run_stop_hook "${SANDBOX}/.claude/hooks/stop_guard_OLD.sh" "$sid")"
  if is_block "$out"; then pass "$label"; else fail "$label" "expected OLD hook to BLOCK (proving the bug), got ALLOW"; fi
}
assert_stop_round1_blocked() {
  local label="$1" sid="$2"
  local out; out="$(run_stop_hook "${SANDBOX}/.claude/hooks/stop_guard_ROUND1.sh" "$sid")"
  if is_block "$out"; then pass "$label"; else fail "$label" "expected ROUND1 hook to BLOCK (proving the flaw), got ALLOW"; fi
}
assert_stop_round1_allowed() {
  local label="$1" sid="$2"
  local out; out="$(run_stop_hook "${SANDBOX}/.claude/hooks/stop_guard_ROUND1.sh" "$sid")"
  if is_block "$out"; then fail "$label" "expected ROUND1 hook to ALLOW (proving the flaw), got BLOCK"; else pass "$label"; fi
}

IN_SCOPE="${SANDBOX}/backend/src/jarvis/tools/weather/geocode.py"
OUT_OF_SCOPE="${SANDBOX}/backend/src/jarvis/cadence/fillers.py"

# ===========================================================================
# PROOF A (verbatim reproduction): session A claims T-3 via the real
# claim_ticket.sh; an out-of-scope write is DENIED; another actor deletes
# .claude/active-ticket (crash/race/another actor); A's IDENTICAL write is
# STILL DENIED, because A's own claim never depended on that file.
# ===========================================================================
clear_all_claims
CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$SESSION_A" "${SANDBOX}/.claude/hooks/claim_ticket.sh" T-3 >/dev/null

assert_scope_deny "PROOF A step 1: session A (claimed T-3) writing out-of-scope is DENIED" \
  "$OUT_OF_SCOPE" "$SESSION_A"

rm -f "${SANDBOX}/.claude/active-ticket"   # "another actor deletes it"

assert_scope_deny "PROOF A step 3 (the literal reproduction): after .claude/active-ticket is DELETED, A's IDENTICAL out-of-scope write is STILL DENIED" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_round1_allowed "PROOF A regression proof: the ROUND1 (single shared-file) hook WRONGLY allowed A's write once the shared file was deleted -- this is the exact bug reported" \
  "$OUT_OF_SCOPE" "$SESSION_A"

# stop_guard side of the same proof: A is still blocked after the deletion,
# because A's per-session claim file is untouched by the deletion.
# ROUND1 kept the SAME ticket id/owner in .claude/active-ticket (it's the
# only place it ever recorded a claim) right up until the deletion, so
# deleting that one shared file wipes out ROUND1's blocking ability too --
# a second symptom of the same single-shared-file flaw.
clear_pass "T-3"
assert_stop_block "PROOF A (stop_guard): A is still BLOCKED from stopping after .claude/active-ticket is deleted (its own per-session claim record is untouched)" \
  "$SESSION_A"
assert_stop_round1_allowed "PROOF A regression proof (stop_guard): ROUND1 WRONGLY stopped blocking A once the one shared file it depended on was deleted" \
  "$SESSION_A"

# restore for later sections (this section only needed the deletion once)
CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$SESSION_A" "${SANDBOX}/.claude/hooks/claim_ticket.sh" T-3 >/dev/null

# ===========================================================================
# PROOF B: per-session isolation, both directions, at the SAME time.
# Session C owns no claim (never scope-constrained, never stop-blocked)
# while session A (claimed T-3, no evidence pass) IS constrained and IS
# blocked -- proving isolation, not one shared on/off switch.
# ===========================================================================
clear_pass "T-3"
assert_scope_allow "PROOF B: session C (owns nothing) writing the SAME out-of-scope path is ALLOWED" \
  "$OUT_OF_SCOPE" "$SESSION_C"
assert_stop_allow "PROOF B: session C (owns nothing) stops without being blocked" \
  "$SESSION_C"
assert_scope_deny "PROOF B (concurrently): session A (owns T-3) writing that SAME path is DENIED" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_stop_block "PROOF B (concurrently): session A (owns T-3, no pass) IS blocked from stopping" \
  "$SESSION_A"
assert_scope_allow "PROOF B: session A writing IN-scope (its own ticket) is still ALLOWED" \
  "$IN_SCOPE" "$SESSION_A"

# ===========================================================================
# Acceptance 3, UNCONDITIONAL: a claim exists but is OWNERLESS (no line 2,
# and no per-session file for anyone) -- must NOT constrain/block a session
# with a known identity. This is the behaviour ROUND1 got wrong on purpose
# (acceptance 4 traded off against acceptance 3); per-session storage
# removes the need for that trade entirely.
# ===========================================================================
clear_all_claims
set_legacy_ownerless "T-3"

assert_scope_allow "acc3 unconditional: ownerless claim does NOT constrain a session with a known id (out-of-scope write ALLOWED)" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_round1_denied "acc3 regression proof: ROUND1 WRONGLY enforced the ownerless claim's scope on a known, non-owning session" \
  "$OUT_OF_SCOPE" "$SESSION_A"

assert_stop_allow "acc3 unconditional (stop_guard): ownerless claim does not block a session with a known id" \
  "$SESSION_A"
assert_stop_old_blocked "regression proof: the OLD (pre-T-21) hook WRONGLY blocked stopping on an ownerless-looking claim (no owner concept existed at all)" \
  "$SESSION_A"

clear_legacy

# ===========================================================================
# Acceptance 4 / defect #1: a MISSING claim (nothing, anywhere) disables
# both guards for everyone (unchanged); an EMPTY claim record -- at either
# the per-session or legacy location -- must behave the same way, not deny
# every write / block every stop for a blank ticket id.
# ===========================================================================
clear_all_claims
assert_scope_allow "no claim anywhere: any write is ALLOWED for any session" "$OUT_OF_SCOPE" "$SESSION_A"
assert_stop_allow  "no claim anywhere: any session stops without being blocked" "$SESSION_A"

set_per_session_claim_empty "$SESSION_A"
assert_scope_allow "defect #1 (per-session file empty): treated as no claim -- write ALLOWED, not denied" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_stop_allow "defect #1 (per-session file empty): does not block stopping" "$SESSION_A"
clear_per_session_claim "$SESSION_A"

set_legacy_empty
assert_scope_allow "defect #1 (legacy file empty): treated as no claim -- write ALLOWED, not denied" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_old_denied "defect #1 regression proof: the OLD hook WRONGLY denied every write on an empty legacy claim file" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_stop_allow "defect #1 (legacy file empty): does not block stopping" "$SESSION_A"
assert_stop_old_blocked "defect #1 regression proof: the OLD hook WRONGLY blocked stopping on an empty legacy claim file" \
  "$SESSION_A"
clear_legacy

# ===========================================================================
# Legacy bridge: an OWNED two-line .claude/active-ticket (no per-session
# file at all) still works for the matching session -- this is what keeps
# a claim already on disk before an upgrade working with no manual step.
# ===========================================================================
clear_all_claims
set_legacy_owned "T-3" "$SESSION_A"
assert_scope_allow "legacy bridge: OWNED active-ticket (no per-session file) -- in-scope ALLOWED for the matching session" \
  "$IN_SCOPE" "$SESSION_A"
assert_scope_deny "legacy bridge: OWNED active-ticket -- out-of-scope DENIED for the matching session" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_allow "legacy bridge: OWNED active-ticket -- a DIFFERENT session is unaffected (acceptance 3)" \
  "$OUT_OF_SCOPE" "$SESSION_B"
clear_pass "T-3"
assert_stop_block "legacy bridge: OWNED active-ticket -- the matching session IS blocked (no pass yet)" "$SESSION_A"
assert_stop_allow "legacy bridge: OWNED active-ticket -- a DIFFERENT session is NOT blocked" "$SESSION_B"
clear_legacy

# ===========================================================================
# COMPAT: a hook call with NO session_id at all falls back to the
# pre-ownership global behaviour on .claude/active-ticket's line 1 -- this
# is what run_scope_guard_tests.sh (T-20, frozen) relies on, and is
# reachable ONLY when session_id is entirely absent from the call, distinct
# from "claim is ownerless but the caller's identity IS known" above (which
# must NOT enforce -- see acc3 unconditional section).
# ===========================================================================
clear_all_claims
set_legacy_ownerless "T-3"
assert_scope_deny "COMPAT (no session_id on the call at all): falls back to global enforcement on active-ticket's TID -- out-of-scope DENIED" \
  "$OUT_OF_SCOPE" ""
assert_scope_allow "COMPAT (no session_id on the call at all): in-scope still ALLOWED" \
  "$IN_SCOPE" ""
clear_legacy

# ===========================================================================
# stop_hook_active bypass must still short-circuit everything, unchanged.
# ===========================================================================
clear_all_claims
CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$SESSION_A" "${SANDBOX}/.claude/hooks/claim_ticket.sh" T-3 >/dev/null
clear_pass "T-3"
assert_stop_allow "REQUIRED guard preserved: stop_hook_active=true always allows, even for the owner with no pass" \
  "$SESSION_A" true
clear_all_claims

# ===========================================================================
# Defect #2: malformed/missing docs/tickets.json fails CLOSED, except for
# the one write that can repair it -- verified against the per-session
# claim path.
# ===========================================================================
CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$SESSION_A" "${SANDBOX}/.claude/hooks/claim_ticket.sh" T-3 >/dev/null

echo 'not valid json {' > "${SANDBOX}/docs/tickets.json"
assert_scope_deny "defect #2: malformed tickets.json -- unrelated write is DENIED (fails closed)" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_allow "defect #2: malformed tickets.json -- editing docs/tickets.json ITSELF is ALLOWED (the repair path)" \
  "${SANDBOX}/docs/tickets.json" "$SESSION_A"
assert_scope_old_denied "defect #2 regression proof: the OLD hook WRONGLY denied even the tickets.json repair write" \
  "${SANDBOX}/docs/tickets.json" "$SESSION_A"
assert_scope_deny "defect #2 sanity: the self-repair exemption does NOT leak to a different docs/ file" \
  "${SANDBOX}/docs/SPEC.md" "$SESSION_A"

rm -f "${SANDBOX}/docs/tickets.json"
assert_scope_deny "defect #2: MISSING tickets.json -- unrelated write is DENIED (fails closed)" \
  "$OUT_OF_SCOPE" "$SESSION_A"
assert_scope_allow "defect #2: MISSING tickets.json -- editing docs/tickets.json to recreate it is ALLOWED" \
  "${SANDBOX}/docs/tickets.json" "$SESSION_A"

restore_tickets_json
assert_scope_allow "defect #2: once tickets.json is valid again, normal in-scope writes resume" \
  "$IN_SCOPE" "$SESSION_A"
assert_scope_deny "defect #2: once tickets.json is valid again, normal scope-checking resumes (out-of-scope denied)" \
  "$OUT_OF_SCOPE" "$SESSION_A"

# ===========================================================================
# The claims-directory exemption itself: a session already constrained by
# an active claim must still be able to write ITS OWN new claim record
# (to re-claim a different ticket) without that write being scope-checked
# against the OLD ticket -- and must NOT be able to freely rewrite a
# DIFFERENT session's claim record (that stays scope-checked normally).
# ===========================================================================
OWN_CLAIM_PATH="${SANDBOX}/.claude/claims/${SESSION_A}"
OTHER_CLAIM_PATH="${SANDBOX}/.claude/claims/${SESSION_B}"
assert_scope_allow "claims exemption: session A (constrained by T-3) can write its OWN claim record to re-claim" \
  "$OWN_CLAIM_PATH" "$SESSION_A"
assert_scope_deny "claims exemption does not leak: session A cannot freely rewrite session B's claim record (not in T-3's scope)" \
  "$OTHER_CLAIM_PATH" "$SESSION_A"

clear_all_claims

# ===========================================================================
# PROOF D: a simulated T-24 claim allows its four declared scope paths and
# denies an out-of-scope one.
# ===========================================================================
CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$SESSION_A" "${SANDBOX}/.claude/hooks/claim_ticket.sh" T-24 >/dev/null
mkdir -p "${SANDBOX}/.github/workflows"
assert_scope_allow "PROOF D (T-24): .github/workflows/** is in scope" \
  "${SANDBOX}/.github/workflows/ci.yml" "$SESSION_A"
assert_scope_allow "PROOF D (T-24): docs/** is in scope" \
  "${SANDBOX}/docs/TASKS.md" "$SESSION_A"
assert_scope_allow "PROOF D (T-24): CLAUDE.md is in scope" \
  "${SANDBOX}/CLAUDE.md" "$SESSION_A"
assert_scope_allow "PROOF D (T-24): .claude/hooks/** is in scope" \
  "${SANDBOX}/.claude/hooks/scope_guard.sh" "$SESSION_A"
assert_scope_deny "PROOF D (T-24): backend/src is NOT in scope" \
  "$IN_SCOPE" "$SESSION_A"
clear_all_claims

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d claim tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d claim test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
