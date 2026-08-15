#!/usr/bin/env bash
# run_claim_tests.sh (T-21) - tests that ticket claims are SESSION-SCOPED and
# carry an OWNER, against the CURRENT claim implementation: harness_lib.py's
# cmd_claim/cmd_close/cmd_release/session_claim/cmd_hook, driven through the
# real CLI wrapper (.claude/scripts/claim.sh) and the real hook wrappers
# (.claude/hooks/scope_guard.sh, .claude/hooks/stop_guard.sh).
#
# LINEAGE: this suite replaces .claude/hooks/tests/run_claim_tests.sh,
# deleted in commit dc23222 ("cc-factory: harness sync") along with its
# subject, .claude/hooks/claim_ticket.sh. That old suite drove a single
# shared file, .claude/active-ticket (ticket id on line 1, optional owner on
# line 2), through jq-based bash hooks, and asserted a three-way lineage
# (pre-T-21 OLD -> single-shared-file-with-owner ROUND1 -> per-session-file
# FINAL) via embedded snapshots of the first two. NONE of that machinery
# exists anymore: there is no .claude/active-ticket concept anywhere in
# harness_lib.py, no jq, no claim_ticket.sh, no ROUND1 to diff against. This
# suite is written fresh against T-21's literal acceptance criteria (read
# directly from docs/tickets.json, not imagined) and the CURRENT
# implementation:
#   - a claim is one file per session, .claude/claims/<session_id>.json,
#     written by harness_lib.py's cmd_claim() with `{"ticket":.., "session":
#     sid, ...}` -- the session id IS the filename AND a field in the body;
#   - ownership is derived per hook-call by reading .claude/claims/<sid>.json
#     for the session_id in THAT call's stdin JSON -- there is no shared
#     "active" file of any kind for either guard to consult, so there is
#     nothing left for a stray/deleted shared file to silently break;
#   - scope_guard.sh and stop_guard.sh are now one-line wrappers around
#     `harness_lib.py hook-scope` / `hook-stop`, which shell out to the same
#     `guard` subcommand / call session_claim() directly.
# Everything below drives those real files, copied into a throwaway sandbox
# CLAUDE_PROJECT_DIR with its own fabricated docs/tickets.json (house style
# per run_evidence_tests.sh and scripts/tests/run_scope_guard_tests.sh) --
# this suite NEVER claims, closes, releases, or reads/writes the real
# project's .claude/claims/, .claude/evidence/, or docs/tickets.json.
#
# WHAT THIS SUITE DOES NOT RE-COVER: scripts/tests/run_scope_guard_tests.sh
# (T-20) already thoroughly exercises scope_guard.sh's PROTECTED/META_ALLOW/
# anchoring/outside-project-dir behavior for a single owning session. This
# suite only lightly touches those (one PROTECTED, one META_ALLOW sanity
# case) and instead focuses on what is UNIQUELY T-21's territory: that a
# claim records an owning session, that ownership is exclusive (a second
# session cannot claim an already-claimed ticket, a session cannot hold two
# claims at once), that both guards constrain/block ONLY the owning session
# and leave a non-owning session alone -- concurrently, at the same moment
# -- and that release/status derivation reflect this per-session reality.
#
# A NOTED DISAGREEMENT WITH A SIBLING SUITE (see part (d) of the report to
# Hank): run_scope_guard_tests.sh's final section labels "a session with NO
# claim at all is unconstrained by scope_guard.sh" a "KNOWN, DOCUMENTED, OPEN
# DEFECT... attributed to jarvis T-21" and leaves two cases deliberately
# failing over it. This suite's SECTION 2 asserts the OPPOSITE as PASSING,
# straight from T-21's own acceptance list: "scope_guard.sh applies a
# ticket's scope only to the owning session" and "a session that owns no
# claim stops without being blocked" (acceptance 3 and 2, verbatim) -- and
# T-21's own objective text names the exact victim this protects: "a
# read-only observer session that claimed nothing." Both suites cannot be
# "correct" about the same behavior in opposite directions; this is flagged
# for Hank to resolve, not silently adjudicated here.
#
# Usage: bash scripts/tests/run_claim_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REAL_HARNESSLIB="${REPO_ROOT}/.claude/scripts/harness_lib.py"
REAL_CLAIMSH="${REPO_ROOT}/.claude/scripts/claim.sh"
REAL_SCOPEHOOK="${REPO_ROOT}/.claude/hooks/scope_guard.sh"
REAL_STOPHOOK="${REPO_ROOT}/.claude/hooks/stop_guard.sh"

for f in "$REAL_HARNESSLIB" "$REAL_CLAIMSH" "$REAL_SCOPEHOOK" "$REAL_STOPHOOK"; do
  if [ ! -f "$f" ]; then
    echo "run_claim_tests: BUG -- expected file missing: $f" >&2
    exit 2
  fi
done

FAILURES=0
CASES_RUN=0

pass() { CASES_RUN=$((CASES_RUN + 1)); printf 'PASS: %s\n' "$1"; }
fail() { CASES_RUN=$((CASES_RUN + 1)); FAILURES=$((FAILURES + 1)); printf 'FAIL: %s -- %s\n' "$1" "$2"; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

SANDBOX="${WORK_DIR}/sandbox"
HARNESSLIB="${SANDBOX}/.claude/scripts/harness_lib.py"
CLAIM_SH="${SANDBOX}/.claude/scripts/claim.sh"
SCOPE_HOOK="${SANDBOX}/.claude/hooks/scope_guard.sh"
STOP_HOOK="${SANDBOX}/.claude/hooks/stop_guard.sh"
CLAIMS="${SANDBOX}/.claude/claims"
EVID="${SANDBOX}/.claude/evidence"
TICKETS="${SANDBOX}/docs/tickets.json"

mkdir -p "${SANDBOX}/.claude/scripts" "${SANDBOX}/.claude/hooks" "${SANDBOX}/.claude/claims" \
  "${SANDBOX}/.claude/evidence" "${SANDBOX}/.claude/monitor" "${SANDBOX}/docs" \
  "${SANDBOX}/scopeA" "${SANDBOX}/scopeB"

# Real files only, copied read-only into the sandbox. scope_guard.sh and
# stop_guard.sh exec harness_lib.py by path, and claim.sh execs it too, so
# all four must travel together.
cp "$REAL_HARNESSLIB" "$HARNESSLIB"
cp "$REAL_CLAIMSH" "$CLAIM_SH"
cp "$REAL_SCOPEHOOK" "$SCOPE_HOOK"
cp "$REAL_STOPHOOK" "$STOP_HOOK"
chmod +x "$CLAIM_SH" "$SCOPE_HOOK" "$STOP_HOOK"

echo a1 >"${SANDBOX}/scopeA/f.txt"
echo b1 >"${SANDBOX}/scopeB/f.txt"

# Fabricated docs/tickets.json (house style per run_evidence_tests.sh /
# run_scope_guard_tests.sh): synthetic ids/scopes so this suite never
# breaks when the real plan's ticket set changes. `title` is required --
# harness_lib.py's status_board prints it.
python3 - "$TICKETS" <<'PYEOF'
import json, sys

def t(tid, title, scope):
    return {"id": tid, "title": title, "verify": "true", "scope": scope, "depends_on": []}

tickets = [
    t("T-9701", "Claim suite fixture A", ["scopeA/**"]),
    t("T-9702", "Claim suite fixture B", ["scopeB/**"]),
]
doc = {"full_verify": "true", "tickets": tickets}
with open(sys.argv[1], "w") as f:
    json.dump(doc, f, indent=1)
PYEOF

git -C "$SANDBOX" init -q -b main
git -C "$SANDBOX" config user.email "test@example.com"
git -C "$SANDBOX" config user.name "Claim Test"
git -C "$SANDBOX" add -A
git -C "$SANDBOX" commit -qm "sandbox init"

# ---------------------------------------------------------------------------
# do_claim / do_close / do_release / do_status: drive the REAL claim.sh
# wrapper with CLAUDE_PROJECT_DIR pinned at the sandbox and
# CLAUDE_CODE_SESSION_ID set to the calling session -- exactly the two env
# vars harness_lib.py's ROOT/_sid() resolution reads. Sets globals OUT/RC.
# ---------------------------------------------------------------------------
do_claim()   { OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$1" "$CLAIM_SH" claim "$2" "$3" 2>&1)"; RC=$?; }
do_close()   { OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$1" "$CLAIM_SH" close 2>&1)"; RC=$?; }
do_release() { OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" CLAUDE_CODE_SESSION_ID="$1" "$CLAIM_SH" release "$2" 2>&1)"; RC=$?; }
do_status()  { OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" "$CLAIM_SH" status_board 2>&1)"; RC=$?; }

# ---------------------------------------------------------------------------
# run_scope_hook <file-path> <session_id> / run_stop_hook <session_id> [active]
# Build the exact stdin JSON shape the real hook system sends and print the
# hook's raw stdout (stderr discarded -- that's what a crash would go to,
# and only stdout is the channel Claude Code's hook contract actually reads
# for a decision).
# ---------------------------------------------------------------------------
run_scope_hook() {
  local filepath="$1" sid="$2"
  printf '{"session_id":"%s","tool_name":"Edit","tool_input":{"file_path":"%s"}}' "$sid" "$filepath" |
    CLAUDE_PROJECT_DIR="$SANDBOX" "$SCOPE_HOOK" 2>/dev/null
}
run_stop_hook() {
  local sid="$1" active="${2:-false}"
  printf '{"stop_hook_active":%s,"session_id":"%s"}' "$active" "$sid" |
    CLAUDE_PROJECT_DIR="$SANDBOX" "$STOP_HOOK" 2>/dev/null
}

# NOTE the herestrings (<<<) below, and do NOT "simplify" them back to
# `echo "$x" | grep -q ...`. This file runs under `set -uo pipefail` (line
# above). `grep -q` exits the instant it finds a match; when that match is
# early in a large output, `echo` is still writing, takes SIGPIPE, and dies
# non-zero -- pipefail then reports the whole pipeline failed even though
# grep matched. In a positive assertion that's a false FAIL; in a negated
# one it's a false PASS. Found and fixed for this exact reason in
# scripts/tests/run_evidence_tests.sh; a herestring has no pipe, so the
# race can't occur.
is_deny()  { grep -qE '"permissionDecision":[[:space:]]*"deny"' <<<"$1"; }
is_block() { grep -qE '"decision":[[:space:]]*"block"'          <<<"$1"; }
assert_match() {
  # assert_match <case-name> <output> <regex>
  if grep -qE "$3" <<<"$2"; then pass "$1"; else fail "$1" "expected to match /$3/ -- got:\n$2"; fi
}
assert_no_match() {
  # assert_no_match <case-name> <output> <regex>
  if grep -qE "$3" <<<"$2"; then fail "$1" "expected NOT to match /$3/ -- got:\n$2"; else pass "$1"; fi
}

assert_scope_allow() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "$filepath" "$sid")"
  if is_deny "$out"; then fail "$label" "expected ALLOW, got DENY: $out"; else pass "$label"; fi
}
assert_scope_deny() {
  local label="$1" filepath="$2" sid="$3"
  local out; out="$(run_scope_hook "$filepath" "$sid")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected DENY, got ALLOW (hook stdout: '$out')"; fi
}
assert_stop_allow() {
  local label="$1" sid="$2" active="${3:-false}"
  local out; out="$(run_stop_hook "$sid" "$active")"
  if is_block "$out"; then fail "$label" "expected ALLOW (no block), got BLOCK: $out"; else pass "$label"; fi
}
assert_stop_block() {
  local label="$1" sid="$2"
  local out; out="$(run_stop_hook "$sid" "false")"
  if is_block "$out"; then pass "$label"; else fail "$label" "expected BLOCK, got ALLOW (hook stdout: '$out')"; fi
}

SESSION_A="session-owner-A"
SESSION_B="session-claimant-B"
SESSION_C="session-observer-C"

IN_SCOPE_A="${SANDBOX}/scopeA/newfile.py"
OUT_OF_SCOPE_A="${SANDBOX}/scopeB/newfile.py"

# ===========================================================================
# ACCEPTANCE 1: "A claim records an owner identifier alongside the ticket
# id, written when the ticket is claimed"
# ===========================================================================
do_claim "$SESSION_A" "T-9701" "acc1: first claim of T-9701 by session A"
if [ "$RC" -eq 0 ]; then pass "acc1: claiming an unclaimed ticket succeeds"; else fail "acc1: claiming an unclaimed ticket succeeds" "exit $RC: $OUT"; fi

CLAIM_FILE_A="${CLAIMS}/${SESSION_A}.json"
if [ -f "$CLAIM_FILE_A" ]; then pass "acc1: a per-session claim file is written, named after the session id"; else fail "acc1: a per-session claim file is written, named after the session id" "no file at $CLAIM_FILE_A"; fi

CLAIM_CONTENT_A="$(cat "$CLAIM_FILE_A" 2>/dev/null)"
assert_match "acc1: the claim record names the claimed ticket id" "$CLAIM_CONTENT_A" '"ticket":[[:space:]]*"T-9701"'
assert_match "acc1: the claim record names the owning session id" "$CLAIM_CONTENT_A" '"session":[[:space:]]*"session-owner-A"'

# Ownership is exclusive: a second session cannot claim the same ticket.
do_claim "$SESSION_B" "T-9701" "acc1: session B attempts the same ticket"
if [ "$RC" -ne 0 ]; then pass "acc1: a second session cannot claim an already-claimed ticket (non-zero exit)"; else fail "acc1: a second session cannot claim an already-claimed ticket (non-zero exit)" "exit 0: $OUT"; fi
assert_match "acc1: the rejection names the owning session, not a generic error" "$OUT" 'claimed by another session.*session-owner-A'
if [ ! -f "${CLAIMS}/${SESSION_B}.json" ]; then pass "acc1: the rejected claim writes no file for session B"; else fail "acc1: the rejected claim writes no file for session B" "a claim file was written anyway"; fi

# A session already holding a claim cannot also claim a second ticket.
do_claim "$SESSION_A" "T-9702" "acc1: session A, already owning T-9701, attempts a second ticket"
if [ "$RC" -ne 0 ]; then pass "acc1: a session already holding a claim cannot claim a second ticket"; else fail "acc1: a session already holding a claim cannot claim a second ticket" "exit 0: $OUT"; fi
assert_match "acc1: the rejection names the ticket already held" "$OUT" 'already owns T-9701'

# ===========================================================================
# ACCEPTANCE 2 + 3 (the core of T-21): both guards constrain/block ONLY the
# owning session, and leave a session that owns no claim alone -- proven
# CONCURRENTLY, i.e. session C's freedom and session A's constraint hold at
# the exact same moment, not as a global on/off switch.
# ===========================================================================
assert_scope_allow "acc3: the owning session A writing inside T-9701's own scope (scopeA) is ALLOWED" \
  "$IN_SCOPE_A" "$SESSION_A"
assert_scope_deny "acc3: the owning session A writing outside T-9701's scope (scopeB) is DENIED" \
  "$OUT_OF_SCOPE_A" "$SESSION_A"
assert_scope_allow "acc3: session C, who owns NO claim, writing that SAME out-of-scope path is ALLOWED (concurrently with A's active claim)" \
  "$OUT_OF_SCOPE_A" "$SESSION_C"

assert_stop_block "acc2: the owning session A (claimed, no receipt yet) IS blocked from stopping" \
  "$SESSION_A"
assert_stop_allow "acc2: session C, who owns NO claim, stops WITHOUT being blocked (concurrently with A's active claim)" \
  "$SESSION_C"

# The required loop-guard bypass must still short-circuit even for the owner.
assert_stop_allow "stop_hook_active=true always allows, even for the owner with no receipt (required anti-loop guard)" \
  "$SESSION_A" true

# ===========================================================================
# ACCEPTANCE 4: "Guards behave safely when no claim exists: a missing
# active-ticket does not silently disable the scope boundary for a session
# that does hold work." There is no shared "active-ticket" file left in the
# current implementation for anything to delete -- ownership is derived
# per-call from .claude/claims/<THIS session's id>.json alone. These cases
# prove that concretely: dropping and then deleting an unrelated/decoy
# artifact at the OLD shared path never touches session A's own boundary,
# because nothing ever reads that path.
# ===========================================================================
DECOY="${SANDBOX}/.claude/active-ticket"
printf 'T-9702\nsome-other-session\n' >"$DECOY"
assert_scope_deny "acc4: a decoy legacy 'active-ticket' file naming a DIFFERENT ticket/owner has NO effect -- A's out-of-scope write is STILL denied" \
  "$OUT_OF_SCOPE_A" "$SESSION_A"
assert_stop_block "acc4: the same decoy file has NO effect on stop_guard -- A is STILL blocked from stopping" \
  "$SESSION_A"
rm -f "$DECOY"
assert_scope_deny "acc4: deleting the decoy changes nothing (it was never read) -- A's out-of-scope write is STILL denied" \
  "$OUT_OF_SCOPE_A" "$SESSION_A"
assert_stop_block "acc4: deleting the decoy changes nothing -- A is STILL blocked from stopping" \
  "$SESSION_A"

# Baseline half of acceptance 4: with NO claim anywhere for anyone, both
# guards are safely inert (no false denies, no false blocks) -- this is not
# the same claim as "a claimless session should be scope-constrained" (see
# header's noted disagreement); it only says the guards fail toward "no
# claim -> no boundary to enforce", never toward a crash or a stuck block.
rm -f "${CLAIMS}/${SESSION_A}.json"
assert_scope_allow "acc4 baseline: with NO claim held by anyone, an arbitrary write is ALLOWED" \
  "$OUT_OF_SCOPE_A" "$SESSION_A"
assert_stop_allow "acc4 baseline: with NO claim held by anyone, stopping is ALLOWED" \
  "$SESSION_A"

# ===========================================================================
# SUPPLEMENTARY (beyond T-21's literal acceptance wording, in the spirit of
# acceptance 4's "behave safely"): what happens when a session's OWN claim
# record is present but corrupted (not missing)? scope_guard.sh's
# guard subcommand wraps its whole body in try/except and prints
# "deny:...internal error...failing closed" on any exception -- so a
# corrupted OWN claim fails closed for scope, the safe direction. Verified
# directly against the real hook below, not asserted from memory.
# ===========================================================================
do_claim "$SESSION_A" "T-9701" "supplementary: reclaim for the corruption probe"
printf 'not valid json {{{' >"${CLAIMS}/${SESSION_A}.json"
SCOPE_ON_CORRUPT="$(run_scope_hook "$OUT_OF_SCOPE_A" "$SESSION_A")"
assert_match "supplementary: scope_guard fails CLOSED (deny) when the session's OWN claim record is corrupted, not open" \
  "$SCOPE_ON_CORRUPT" '"permissionDecision":[[:space:]]*"deny"'

# stop_guard.sh's hook-stop path calls session_claim() with no equivalent
# try/except around it -- a corrupted OWN claim record crashes the Python
# process instead of emitting a block decision. Per the Stop hook contract,
# stdout with no valid JSON and a non-2 exit does not halt the Stop event,
# so this crash resolves to "stopping is allowed" for a session that, as
# far as its own (corrupted) record shows, still has unfinished work. This
# is a genuine, verified gap this suite surfaces rather than papering over
# -- left failing on purpose. Do not weaken it to force green.
STOP_ON_CORRUPT="$(run_stop_hook "$SESSION_A" false)"
assert_match "supplementary (KNOWN GAP, left failing): stop_guard should also fail closed (still BLOCK) when the session's OWN claim record is corrupted -- currently it crashes and silently allows stopping instead" \
  "$STOP_ON_CORRUPT" '"decision":[[:space:]]*"block"'

# Repair the record so later sections see a clean, valid claim again.
python3 - "${CLAIMS}/${SESSION_A}.json" <<'PYEOF'
import json, sys
json.dump({"ticket": "T-9701", "session": "session-owner-A", "note": "repaired",
           "start_commit": "0" * 40, "attempts": 0, "ts": 0}, open(sys.argv[1], "w"))
PYEOF

# ===========================================================================
# ACCEPTANCE 5, part 1: "A test exercises both guards for owner and
# non-owner sessions" -- this suite's ACCEPTANCE 2+3 section above does
# exactly that (scope_guard AND stop_guard, both an owning session and a
# non-owning session, concurrently). ACCEPTANCE 5, part 2: "...and runs in
# CI". That half is checked directly against the REAL repo's workflow
# files (read-only) -- not the sandbox -- since it's a fact about wiring,
# not about the claim implementation itself.
# ===========================================================================
CI_REFS="$(grep -rl 'run_claim_tests' "${REPO_ROOT}/.github/workflows/" 2>/dev/null || true)"
if [ -n "$CI_REFS" ]; then
  pass "acc5: a workflow under .github/workflows references run_claim_tests.sh"
else
  fail "acc5: a workflow under .github/workflows references run_claim_tests.sh" \
    "no workflow file does -- .github/workflows/ci.yml only runs the backend/frontend suites; this suite (and its siblings) is not wired into CI. Real, current gap, not fixed here (out of this task's permitted scope: write access is limited to scripts/tests/run_claim_tests.sh)."
fi

# ===========================================================================
# Release frees the ticket; status is DERIVED and reflects reality at every
# step (queue -> in_progress -> queue again -> claimable by someone else ->
# in_progress -> resolved after a real close).
# ===========================================================================
do_status
assert_match "status: T-9701 reads in_progress while session A's claim is active" "$OUT" 'T-9701[[:space:]]+in_progress'

do_release "$SESSION_A" "done with the claim test's use of T-9701"
if [ "$RC" -eq 0 ]; then pass "release: releasing an owned claim succeeds"; else fail "release: releasing an owned claim succeeds" "exit $RC: $OUT"; fi
if [ ! -f "$CLAIM_FILE_A" ]; then pass "release: the per-session claim file is removed"; else fail "release: the per-session claim file is removed" "still present at $CLAIM_FILE_A"; fi

do_status
assert_match "status: T-9701 reads queue again after release (status is derived, not stored)" "$OUT" 'T-9701[[:space:]]+queue'

assert_scope_allow "release: session A is no longer scope-constrained after releasing (owns no claim)" \
  "$OUT_OF_SCOPE_A" "$SESSION_A"
assert_stop_allow "release: session A is no longer stop-blocked after releasing (owns no claim)" \
  "$SESSION_A"

do_claim "$SESSION_B" "T-9701" "a different session claims the now-free ticket"
if [ "$RC" -eq 0 ]; then pass "release: a DIFFERENT session can claim T-9701 once it's free"; else fail "release: a DIFFERENT session can claim T-9701 once it's free" "exit $RC: $OUT"; fi
CLAIM_CONTENT_B="$(cat "${CLAIMS}/${SESSION_B}.json" 2>/dev/null)"
assert_match "release: the new claim's owner is session B, not the original session A" "$CLAIM_CONTENT_B" '"session":[[:space:]]*"session-claimant-B"'

do_close "$SESSION_B"
if [ "$RC" -eq 0 ]; then pass "close: closing T-9701 (trivial verify + full_verify) succeeds"; else fail "close: closing T-9701 (trivial verify + full_verify) succeeds" "exit $RC: $OUT"; fi
if [ -f "${EVID}/T-9701.json" ]; then pass "close: a receipt is written to .claude/evidence/T-9701.json"; else fail "close: a receipt is written to .claude/evidence/T-9701.json" "no receipt found"; fi
if [ ! -f "${CLAIMS}/${SESSION_B}.json" ]; then pass "close: the claim file is removed on close"; else fail "close: the claim file is removed on close" "still present"; fi

do_status
assert_match "status: T-9701 reads resolved after a real close (receipt present)" "$OUT" 'T-9701[[:space:]]+resolved'

# ===========================================================================
# Light PROTECTED / META_ALLOW sanity (T-20's territory in full; here only
# to confirm session-ownership interacts correctly with them, not to
# re-cover run_scope_guard_tests.sh).
# ===========================================================================
do_claim "$SESSION_A" "T-9702" "protected/meta sanity"
assert_scope_deny "PROTECTED overrides ownership: even the owning session cannot Edit/Write docs/tickets.json" \
  "${SANDBOX}/docs/tickets.json" "$SESSION_A"
assert_scope_allow "META_ALLOW overrides scope: .claude/NEEDS_HUMAN.md is allowed for the owning session regardless of its ticket's declared scope" \
  "${SANDBOX}/.claude/NEEDS_HUMAN.md" "$SESSION_A"
do_release "$SESSION_A" "sanity section cleanup"

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d claim tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d claim test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
