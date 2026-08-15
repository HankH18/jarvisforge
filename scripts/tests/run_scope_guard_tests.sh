#!/usr/bin/env bash
# run_scope_guard_tests.sh (T-20) - tests .claude/hooks/scope_guard.sh, the
# PreToolUse hook every ticket depends on to keep Edit/Write calls inside
# the claiming session's declared scope.
#
# LINEAGE: this suite replaces .claude/hooks/tests/run_scope_guard_tests.sh,
# deleted in commit dc23222 ("cc-factory: harness sync") along with the
# machinery it drove directly. Unlike that machinery, scope_guard.sh itself
# SURVIVED dc23222 -- but its insides did not: it used to be a self-contained
# bash script with its own jq-based glob matcher and a single global
# .claude/active-ticket file naming "the" claimed ticket. As of dc23222 it is
# a one-line wrapper:
#     exec python3 "${CLAUDE_PROJECT_DIR}/.claude/scripts/harness_lib.py" hook-scope
# which shells out to harness_lib.py's `guard` subcommand. The claim model
# changed too: there is no longer one global active ticket -- each Claude
# Code SESSION (session_id, arriving in the hook's stdin JSON) may hold its
# own claim, recorded at .claude/claims/<session_id>.json. So this suite
# drives the REAL hook exactly as the real hook system does -- JSON on
# stdin, decision read from stdout -- against a sandboxed CLAUDE_PROJECT_DIR
# with sandbox-local copies of scope_guard.sh and harness_lib.py (scope_guard.sh
# execs harness_lib.py by path, so both must be present together) and a
# FABRICATED docs/tickets.json (synthetic ticket ids/scopes, matching the
# house style set by run_evidence_tests.sh, so this suite is never coupled
# to the real plan's ticket set).
#
# WHAT WAS DROPPED FROM THE OLD SUITE, AND WHY:
#   - Every "assert_old_hook_*" comparison against an embedded pre-T-20 bash
#     snapshot (commit 73d5976). Those existed to prove T-20's ORIGINAL bash
#     implementation fixed 3 specific bugs relative to ITS OWN prior version.
#     The system under test today is a different implementation in a
#     different language with a different claim model; diffing today's
#     Python guard against a frozen bash snapshot of a since-replaced
#     implementation proves nothing about current behavior, so it is not
#     ported -- this suite instead asserts the CURRENT guard's behavior
#     directly, which is the actual T-20 verify target now.
#   - The .claude/active-ticket-based "claiming a ticket must still work"
#     checklist case. That file is no longer read by anything: claim/close/
#     release (harness_lib.py cmd_claim/cmd_close/cmd_release) write
#     .claude/claims/<session_id>.json directly via Python's open(), never
#     through the Edit/Write tool this hook guards -- so there is nothing
#     for a PreToolUse hook to allow or deny there anymore. This is
#     replaced below by a same-spirit, CURRENTLY-real case: an attempt to
#     Edit/Write .claude/claims/<sid>.json THROUGH the tool path is itself
#     now DENIED (it's in harness_lib.py's PROTECTED list) -- the code
#     comment there says why: "only scripts write it".
#   - The anchored-regex acceptance case is KEPT (glob_re() in harness_lib.py
#     still anchors both ends: "^" + out + "$"), but re-expressed against
#     fabricated scope paths instead of T-6's real backend/src/jarvis/cadence
#     path, per the house-style note above.
#
# WHAT CHANGED BEHAVIOR (not dropped -- inverted, because the old suite's
# expectation no longer matches the current guard):
#   - The old suite asserted "docs/** in scope -> docs/tickets.json is
#     ALLOWED" (its T-13 case) and "a ticket's own .claude/hooks/** scope ->
#     editing scope_guard.sh is ALLOWED" (its T-20 checklist-e case), and
#     ".claude/evidence/<id>.pass ALLOWED regardless of scope" (checklist b).
#     Under the current guard, harness_lib.py's PROTECTED list (docs/tickets.json,
#     docs/SPEC.md, docs/DESIGN.md, .claude/hooks/**, .claude/scripts/**,
#     .claude/settings.json, .claude/claims/**, .claude/evidence/**) is
#     checked BEFORE any claim/scope lookup and denies unconditionally --
#     even for the one ticket whose own declared scope names that exact
#     path. This is a real, intentional-looking hardening (it matches
#     harness-protocol.md rule 1: those paths are plan/harness files that
#     change only through sanctioned scripts or human review, "not with
#     Edit, not with Bash") -- so these cases are ported with INVERTED
#     (deny, not allow) expectations. See section "PROTECTED overrides an
#     in-scope declaration" below.
#
# KNOWN, DOCUMENTED, OPEN DEFECT -- guard is inert with no claim:
# harness_lib.py's `guard` subcommand: `c = session_claim(sid); if not c:
# print("allow")` (comment: "non-owning sessions are unconstrained (jarvis
# T-21)"). A session that holds NO claim at all is not merely permitted to
# touch its own ticket's files -- it is permitted to touch ANYTHING (short
# of the hardcoded PROTECTED list). The case below labeled "KNOWN DEFECT"
# asserts the CORRECT behavior (an out-of-scope write with no claim active
# should be denied, or at minimum not silently allowed) and is EXPECTED TO
# FAIL against the current guard. It is left failing on purpose -- do not
# weaken it to get a green suite. See T-20's report to Hank for detail.
#
# Usage: bash scripts/tests/run_scope_guard_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REAL_HOOK="${REPO_ROOT}/.claude/hooks/scope_guard.sh"
REAL_HARNESSLIB="${REPO_ROOT}/.claude/scripts/harness_lib.py"

for f in "$REAL_HOOK" "$REAL_HARNESSLIB"; do
  if [ ! -f "$f" ]; then
    echo "run_scope_guard_tests: BUG -- expected file missing: $f" >&2
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
HOOK="${SANDBOX}/.claude/hooks/scope_guard.sh"
HARNESSLIB="${SANDBOX}/.claude/scripts/harness_lib.py"
CLAIMS="${SANDBOX}/.claude/claims"
TICKETS="${SANDBOX}/docs/tickets.json"

mkdir -p "${SANDBOX}/.claude/hooks" "${SANDBOX}/.claude/scripts" "${SANDBOX}/.claude/claims" \
  "${SANDBOX}/.claude/evidence" "${SANDBOX}/.claude/monitor" "${SANDBOX}/docs" \
  "${SANDBOX}/areaA" "${SANDBOX}/areaB/nested" "${SANDBOX}/vendor/areaB/nested"

# Real hook AND real harness_lib.py, copied read-only into the sandbox --
# scope_guard.sh execs harness_lib.py by path, so the pair must travel
# together. Neither the real project's .claude/ nor docs/tickets.json is
# ever touched by this script.
cp "$REAL_HOOK" "$HOOK"
chmod +x "$HOOK"
cp "$REAL_HARNESSLIB" "$HARNESSLIB"

# Fabricated docs/tickets.json (house style per run_evidence_tests.sh):
# synthetic ids/scopes so this suite never breaks when the real plan's
# ticket set changes.
python3 - "$TICKETS" <<'PYEOF'
import json, sys

def t(tid, scope):
    return {"id": tid, "verify": "true", "scope": scope}

tickets = [
    t("T-9601", ["areaA/**"]),                                    # plain in/out-of-scope
    t("T-9602", ["areaB/nested/**"]),                              # anchoring regression
    t("T-9603", ["docs/onefile.md"]),                              # literal, non-glob scope entry
    t("T-9604", ["areaA/**"]),                                     # scope excludes protected paths
    t("T-9605", ["docs/**", ".claude/hooks/**", ".claude/settings.json"]),  # scope NAMES protected paths
]
doc = {"full_verify": "true", "tickets": tickets}
with open(sys.argv[1], "w") as f:
    json.dump(doc, f, indent=1)
PYEOF

echo a1 >"${SANDBOX}/areaA/f.txt"
echo b1 >"${SANDBOX}/areaB/nested/f.txt"
echo v1 >"${SANDBOX}/vendor/areaB/nested/f.txt"
echo d1 >"${SANDBOX}/docs/onefile.md"

# ---------------------------------------------------------------------------
# set_claim <sid> <ticket-id> / clear_claim <sid>
# Writes/removes .claude/claims/<sid>.json directly (the shape cmd_claim
# itself writes) -- this is fixture construction for the SUT's state, not a
# call into any deleted claim_ticket.sh-style script.
# ---------------------------------------------------------------------------
set_claim() {
  local sid="$1" tid="$2"
  mkdir -p "$CLAIMS"
  printf '{"ticket":"%s","session":"%s","note":"test fixture","start_commit":"deadbeef","attempts":0,"ts":0}\n' \
    "$tid" "$sid" >"${CLAIMS}/${sid}.json"
}
clear_claim() {
  rm -f "${CLAIMS}/$1.json"
}

# ---------------------------------------------------------------------------
# run_hook <sid> <file-path>: builds the exact stdin JSON shape the real
# PreToolUse hook system sends (session_id, tool_name, tool_input.file_path)
# and prints the hook's raw stdout.
# ---------------------------------------------------------------------------
run_hook() {
  local sid="$1" filepath="$2"
  printf '{"session_id":"%s","tool_name":"Edit","tool_input":{"file_path":"%s"}}' "$sid" "$filepath" |
    CLAUDE_PROJECT_DIR="$SANDBOX" "$HOOK"
}

# NOTE: use herestrings (<<<), never `echo "$x" | grep -q ...`. This file
# runs under `set -uo pipefail` (line above). `grep -q` exits the instant it
# finds a match; if that match is early and `echo` is still writing, `echo`
# takes SIGPIPE and dies non-zero, and pipefail reports the whole pipeline
# failed even though grep matched -- an intermittent false FAIL here (or a
# false PASS in a negated check). This was found and fixed for exactly this
# reason in scripts/tests/run_evidence_tests.sh; see its assert_match/
# assert_no_match comment. A herestring has no pipe, so the race can't occur.
is_deny() {
  grep -qE '"permissionDecision":[[:space:]]*"deny"' <<<"$1"
}

assert_allow() {
  # assert_allow <label> <sid> <file-path>
  local label="$1" sid="$2" filepath="$3"
  local out; out="$(run_hook "$sid" "$filepath")"
  if is_deny "$out"; then fail "$label" "expected ALLOW, got DENY: $out"; else pass "$label"; fi
}
assert_deny() {
  # assert_deny <label> <sid> <file-path>
  local label="$1" sid="$2" filepath="$3"
  local out; out="$(run_hook "$sid" "$filepath")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected DENY, got ALLOW (hook stdout: '${out}')"; fi
}

SID_A="test-session-A"
SID_NONE="test-session-none-claimed"

# ===========================================================================
# Baseline: in-scope and out-of-scope writes for a claimed session (T-9601)
# ===========================================================================
set_claim "$SID_A" "T-9601"
assert_allow "T-9601: write inside its own claimed scope (areaA) is ALLOWED" \
  "$SID_A" "${SANDBOX}/areaA/newfile.py"
assert_deny "T-9601: write to a different ticket's scope (areaB) is DENIED" \
  "$SID_A" "${SANDBOX}/areaB/nested/x.py"

# ===========================================================================
# Anchored regex, both ends (glob_re() in harness_lib.py: "^" + out + "$").
# A path that merely CONTAINS a scope prefix must NOT match; a path that
# actually IS under that prefix still must.
# ===========================================================================
set_claim "$SID_A" "T-9602"
assert_deny "anchoring: 'vendor/areaB/nested/x.py' does NOT satisfy scope 'areaB/nested/**' (contains the prefix, not anchored at repo root)" \
  "$SID_A" "${SANDBOX}/vendor/areaB/nested/x.py"
assert_allow "anchoring: a real in-scope path 'areaB/nested/real.py' still matches after anchoring" \
  "$SID_A" "${SANDBOX}/areaB/nested/real.py"

# ===========================================================================
# A literal, non-glob scope entry matches itself exactly and nothing else.
# ===========================================================================
set_claim "$SID_A" "T-9603"
assert_allow "T-9603: its one literal (non-glob) scope entry docs/onefile.md matches exactly" \
  "$SID_A" "${SANDBOX}/docs/onefile.md"
assert_deny "T-9603: a sibling docs/ file not named in scope is DENIED" \
  "$SID_A" "${SANDBOX}/docs/othernotes.md"

# ===========================================================================
# PROTECTED overrides an in-scope declaration. harness_lib.py's PROTECTED
# list is checked before any claim/scope lookup and denies unconditionally
# -- INVERTED from the deleted suite's expectation (see header). T-9605's
# scope literally names docs/** and .claude/hooks/**; PROTECTED still wins.
# ===========================================================================
set_claim "$SID_A" "T-9605"
assert_deny "PROTECTED beats an in-scope declaration: T-9605's scope nominally includes docs/**, but docs/tickets.json is still DENIED (plan file)" \
  "$SID_A" "${SANDBOX}/docs/tickets.json"
assert_deny "PROTECTED beats an in-scope declaration: T-9605's scope nominally includes .claude/hooks/**, but the guard itself is still DENIED" \
  "$SID_A" "${SANDBOX}/.claude/hooks/scope_guard.sh"
assert_allow "PROTECTED is narrow, not a blanket docs/ block: a non-protected docs/ file IS allowed under T-9605's docs/** scope" \
  "$SID_A" "${SANDBOX}/docs/onefile.md"

# ===========================================================================
# PROTECTED denies unconditionally even for a session whose scope does NOT
# nominally cover the path at all (the more obvious case).
# ===========================================================================
set_claim "$SID_A" "T-9604"
assert_deny "PROTECTED: docs/tickets.json is DENIED regardless of scope" "$SID_A" "${SANDBOX}/docs/tickets.json"
assert_deny "PROTECTED: docs/SPEC.md is DENIED regardless of scope" "$SID_A" "${SANDBOX}/docs/SPEC.md"
assert_deny "PROTECTED: docs/DESIGN.md is DENIED regardless of scope" "$SID_A" "${SANDBOX}/docs/DESIGN.md"
assert_deny "PROTECTED: .claude/scripts/harness_lib.py is DENIED regardless of scope" \
  "$SID_A" "${SANDBOX}/.claude/scripts/harness_lib.py"
assert_deny "PROTECTED: .claude/settings.json is DENIED regardless of scope" \
  "$SID_A" "${SANDBOX}/.claude/settings.json"
assert_deny "PROTECTED: .claude/claims/<sid>.json is DENIED via the Edit/Write path -- only harness_lib.py's own open() writes it (replaces the dropped .claude/active-ticket checklist case, see header)" \
  "$SID_A" "${CLAIMS}/${SID_A}.json"
assert_deny "PROTECTED: .claude/evidence/<id>.json is DENIED via the Edit/Write path -- only the close path writes it (inverts the deleted suite's checklist-b expectation, see header)" \
  "$SID_A" "${SANDBOX}/.claude/evidence/T-9604.json"

# ===========================================================================
# META_ALLOW is unconditional: allowed even under a claim whose scope does
# not cover it, and even with no claim at all.
# ===========================================================================
assert_allow "META_ALLOW: .claude/NEEDS_HUMAN.md is ALLOWED under a claim whose scope doesn't cover it" \
  "$SID_A" "${SANDBOX}/.claude/NEEDS_HUMAN.md"
assert_allow "META_ALLOW: .claude/monitor/heartbeat.jsonl is ALLOWED under a claim whose scope doesn't cover it" \
  "$SID_A" "${SANDBOX}/.claude/monitor/heartbeat.jsonl"
assert_allow "META_ALLOW: docs/TASKS.md is ALLOWED under a claim whose scope doesn't cover it" \
  "$SID_A" "${SANDBOX}/docs/TASKS.md"

clear_claim "$SID_A"
assert_allow "META_ALLOW: .claude/NEEDS_HUMAN.md is ALLOWED even with no active claim at all" \
  "$SID_A" "${SANDBOX}/.claude/NEEDS_HUMAN.md"

# ===========================================================================
# A path outside CLAUDE_PROJECT_DIR is ALLOWED unconditionally (scratch/tmp
# work), with or without a claim active.
# ===========================================================================
OUTSIDE_PATH="${WORK_DIR}/outside-the-repo/scratch.txt"
set_claim "$SID_A" "T-9601"
assert_allow "outside-project-dir: a scratch path outside CLAUDE_PROJECT_DIR is ALLOWED with an active claim" \
  "$SID_A" "$OUTSIDE_PATH"
clear_claim "$SID_A"
assert_allow "outside-project-dir: still ALLOWED with no active claim" \
  "$SID_A" "$OUTSIDE_PATH"

# ===========================================================================
# Robustness: a claim naming a ticket id absent from tickets.json fails
# CLOSED (denied), not open -- guard()'s `if t and match_any(...)` falls to
# the deny branch when ticket() returns None.
# ===========================================================================
set_claim "$SID_A" "T-9999-DOES-NOT-EXIST"
assert_deny "robustness: a claim for a ticket id absent from tickets.json fails CLOSED (denied), not open" \
  "$SID_A" "${SANDBOX}/areaA/whatever.py"

# ===========================================================================
# No docs/tickets.json in the project at all: PROTECTED paths still fail
# closed (checked before tickets.json's existence), everything else is
# unrestricted ("harness inert except protected files" -- harness_lib.py's
# own guard() comments this explicitly).
# ===========================================================================
set_claim "$SID_A" "T-9601"
mv "$TICKETS" "${TICKETS}.hidden"
assert_allow "no docs/tickets.json in project: a non-protected path is ALLOWED (harness inert except protected files)" \
  "$SID_A" "${SANDBOX}/areaA/anything.py"
assert_deny "no docs/tickets.json in project: PROTECTED paths are still DENIED (fail-safe, checked before tickets.json's existence)" \
  "$SID_A" "${SANDBOX}/docs/tickets.json"
mv "${TICKETS}.hidden" "$TICKETS"
assert_allow "restoring docs/tickets.json: T-9601's in-scope write is ALLOWED again" \
  "$SID_A" "${SANDBOX}/areaA/anything2.py"

# ===========================================================================
# KNOWN, DOCUMENTED, OPEN DEFECT: the guard is inert for a session holding
# NO claim at all. harness_lib.py's guard(): `c = session_claim(sid); if
# not c: print("allow")` (comment: "non-owning sessions are unconstrained
# (jarvis T-21)") -- so an unclaimed session may write ANYWHERE except the
# hardcoded PROTECTED list, including paths that plainly belong to another
# ticket's declared scope. These two cases assert the CORRECT behavior (an
# out-of-scope write with no claim should be denied, or at minimum not
# silently allowed) and are EXPECTED TO FAIL against the current guard.
# LEFT FAILING ON PURPOSE -- do not weaken these to force green.
# ===========================================================================
clear_claim "$SID_NONE"
assert_deny "KNOWN DEFECT (guard inert without a claim): an arbitrary out-of-scope write with no claim active should be DENIED, not silently allowed" \
  "$SID_NONE" "${SANDBOX}/randomdir/randomfile.txt"
assert_deny "KNOWN DEFECT (guard inert without a claim): a write into another ticket's declared scope (areaB, owned by T-9602) with no claim active should be DENIED, not silently allowed" \
  "$SID_NONE" "${SANDBOX}/areaB/nested/anything.py"

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d scope guard tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d scope guard test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
