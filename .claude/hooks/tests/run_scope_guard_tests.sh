#!/usr/bin/env bash
# run_scope_guard_tests.sh (T-20) - scope_guard.sh is the PreToolUse hook
# every ticket in this project depends on to keep an agent's Edit/Write
# calls inside its declared scope. This suite drives the REAL hook script
# directly (never the harness, never a real Edit/Write tool call) against a
# sandboxed CLAUDE_PROJECT_DIR, and proves the four bugs named in T-20's
# objective are actually fixed:
#
#   1. docs/ (and .claude/) were blanket-exempt, waving through exactly the
#      files protocol rule 1 exists to protect (docs/tickets.json,
#      docs/SPEC.md, docs/DESIGN.md) -- and .claude/hooks/scope_guard.sh
#      itself, which is a strictly worse hole than docs/ (a ticket with no
#      .claude/ in scope could disable the guard). Fixed with two narrow,
#      named exemptions (.claude/active-ticket, .claude/evidence/*.pass)
#      instead of a directory-wide pass.
#   2. an absolute path outside CLAUDE_PROJECT_DIR was left unchanged by the
#      old prefix-strip, then fell through to the deny branch -- so every
#      write to a scratch/tmp dir was denied while a ticket was claimed.
#      Fixed: paths outside the project dir are allowed before any
#      scope-matching happens.
#   3. the glob->regex translation appended "$" but never "^", so jq's
#      test() (a SEARCH, not a full match) let any path merely CONTAINING a
#      scope prefix through, e.g. "vendor/backend/src/jarvis/cadence/x.py"
#      satisfied scope "backend/src/jarvis/cadence/**". Fixed by anchoring
#      both ends.
#
# It uses the REAL docs/tickets.json (read-only, copied into the sandbox
# verbatim -- never edited, never regenerated) so every scope in this file
# is a real ticket's real declared scope, not a fabricated fixture. Cases
# below exercise T-3, T-6, T-9, T-13, T-20 and T-22.
#
# Every regression case also runs against a byte-for-byte snapshot of the
# PRE-T-20 hook (embedded below, taken from git commit 73d5976, the T-20
# ticket-start commit) to prove each one actually reproduces the bug this
# ticket fixes, rather than passing vacuously.
#
# Usage: bash .claude/hooks/tests/run_scope_guard_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HOOK="${REPO_ROOT}/.claude/hooks/scope_guard.sh"
TICKETS="${REPO_ROOT}/docs/tickets.json"

FAILURES=0
CASES_RUN=0

pass() { CASES_RUN=$((CASES_RUN + 1)); printf 'PASS: %s\n' "$1"; }
fail() { CASES_RUN=$((CASES_RUN + 1)); FAILURES=$((FAILURES + 1)); printf 'FAIL: %s -- %s\n' "$1" "$2"; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

SANDBOX="${WORK_DIR}/sandbox"
mkdir -p "${SANDBOX}/.claude/hooks" "${SANDBOX}/.claude/evidence" "${SANDBOX}/docs" \
  "${SANDBOX}/backend/src/jarvis/tools/weather" "${SANDBOX}/backend/src/jarvis/cadence" \
  "${SANDBOX}/vendor/backend/src/jarvis/cadence"

# Real hook, real ticket file -- both copied read-only into the sandbox.
# docs/tickets.json is never written by this script.
cp "$HOOK" "${SANDBOX}/.claude/hooks/scope_guard.sh"
chmod +x "${SANDBOX}/.claude/hooks/scope_guard.sh"
cp "$TICKETS" "${SANDBOX}/docs/tickets.json"

# Byte-for-byte snapshot of the hook as it stood at the T-20 ticket-start
# commit (73d5976), i.e. before any of this ticket's fixes. Used only to
# prove the denied/allowed cases below actually flip relative to the old
# behaviour -- this script never re-writes the real project's hook.
cat > "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" <<'OLDHOOK'
#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): deny paths outside the active ticket's scope globs.
set -uo pipefail
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
AT="${CLAUDE_PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0            # no active ticket claimed; don't block setup work
TID=$(head -n1 "$AT" | tr -d '[:space:]')
REL="${FILE#"$CLAUDE_PROJECT_DIR"/}"
case "$REL" in .claude/*|docs/*) exit 0 ;; esac   # meta paths always allowed
MATCH=$(jq -r --arg id "$TID" --arg f "$REL" '
  .tickets[] | select(.id==$id) | .scope[] as $g
  | select(($f | test(($g | gsub("\\.";"\\.") | gsub("\\*\\*";".∞") | gsub("\\*";"[^/]*") | gsub("∞";"*")) + "$"))) | "yes"' \
  "${CLAUDE_PROJECT_DIR}/docs/tickets.json" | head -n1)
if [ "$MATCH" = "yes" ]; then exit 0; fi
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$REL is outside ticket $TID scope. If genuinely required, this is a plan defect — stop and ask the human."}}
EOF
exit 0
OLDHOOK
chmod +x "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh"

set_active_ticket() {
  printf '%s\n' "$1" > "${SANDBOX}/.claude/active-ticket"
}
clear_active_ticket() {
  rm -f "${SANDBOX}/.claude/active-ticket"
}

# run_hook <hook-path> <file-path>: prints the hook's stdout.
run_hook() {
  local hookpath="$1" filepath="$2"
  printf '{"tool_input":{"file_path":"%s"}}' "$filepath" |
    CLAUDE_PROJECT_DIR="$SANDBOX" "$hookpath"
}

is_deny() { echo "$1" | grep -q '"permissionDecision":"deny"'; }

assert_allow() {
  local label="$1" filepath="$2"
  local out; out="$(run_hook "$HOOK" "$filepath")"
  if is_deny "$out"; then fail "$label" "expected ALLOW, got DENY: $out"; else pass "$label"; fi
}
assert_deny() {
  local label="$1" filepath="$2"
  local out; out="$(run_hook "$HOOK" "$filepath")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected DENY, got ALLOW (empty output)"; fi
}
# assert the OLD hook produces the OPPOSITE decision, proving this case is a
# real regression fix and not a vacuous test.
assert_old_hook_allowed() {
  local label="$1" filepath="$2"
  local out; out="$(run_hook "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" "$filepath")"
  if is_deny "$out"; then fail "$label" "expected OLD hook to ALLOW (proving the bug), got DENY: $out"; else pass "$label"; fi
}
assert_old_hook_denied() {
  local label="$1" filepath="$2"
  local out; out="$(run_hook "${SANDBOX}/.claude/hooks/scope_guard_OLD.sh" "$filepath")"
  if is_deny "$out"; then pass "$label"; else fail "$label" "expected OLD hook to DENY (proving the bug), got ALLOW"; fi
}

# ===========================================================================
# Baseline: in-scope and out-of-scope writes for a real ticket (T-3)
# ===========================================================================
set_active_ticket "T-3"
assert_allow  "T-3: write inside its own scope (weather tool file) is ALLOWED" \
  "${SANDBOX}/backend/src/jarvis/tools/weather/geocode.py"
assert_deny   "T-3: write to another ticket's scope (cadence) is DENIED" \
  "${SANDBOX}/backend/src/jarvis/cadence/fillers.py"

# ===========================================================================
# Acceptance 4: anchored regex, both ends. T-6 scope has
# "backend/src/jarvis/cadence/**". A path that merely CONTAINS that prefix
# must NOT match; a path that actually IS under that prefix still must.
# ===========================================================================
set_active_ticket "T-6"
assert_deny  "acc4 (T-6): 'vendor/backend/src/jarvis/cadence/x.py' does NOT satisfy scope 'backend/src/jarvis/cadence/**' (unanchored bug fixed)" \
  "${SANDBOX}/vendor/backend/src/jarvis/cadence/x.py"
assert_old_hook_allowed "acc4 regression proof: the OLD hook WRONGLY allowed the vendor/ path (unanchored regex bug reproduced)" \
  "${SANDBOX}/vendor/backend/src/jarvis/cadence/x.py"
assert_allow "acc4 (T-6): a real in-scope cadence path still matches after anchoring" \
  "${SANDBOX}/backend/src/jarvis/cadence/fillers.py"

# ===========================================================================
# Acceptance 2: docs/ is no longer blanket-exempt. T-3's scope has no docs/
# entry at all, so docs/tickets.json, docs/SPEC.md and docs/DESIGN.md must
# all be DENIED under T-3. Under T-13 (scope: docs/**), the same file is
# legitimately ALLOWED -- the fix removes the blanket pass, not scoped
# access.
# ===========================================================================
set_active_ticket "T-3"
assert_deny "acc2 (T-3): docs/tickets.json is DENIED (T-3's scope has no docs/ entry)" \
  "${SANDBOX}/docs/tickets.json"
assert_old_hook_allowed "acc2 regression proof: the OLD hook WRONGLY allowed docs/tickets.json under any ticket (blanket docs/* exemption reproduced)" \
  "${SANDBOX}/docs/tickets.json"
assert_deny "acc2 (T-3): docs/SPEC.md is DENIED (T-3's scope has no docs/ entry)" \
  "${SANDBOX}/docs/SPEC.md"
assert_deny "acc2 (T-3): docs/DESIGN.md is DENIED (T-3's scope has no docs/ entry)" \
  "${SANDBOX}/docs/DESIGN.md"

set_active_ticket "T-13"
assert_allow "acc2 (T-13): docs/tickets.json IS allowed -- T-13's real scope is docs/** (scoped access still works)" \
  "${SANDBOX}/docs/tickets.json"

set_active_ticket "T-9"
assert_allow "T-9: its one literal (non-glob) scope entry docs/latency-report.md matches exactly" \
  "${SANDBOX}/docs/latency-report.md"
assert_deny "T-9: a different docs/ file is DENIED -- T-9's scope names only that one file, not docs/**" \
  "${SANDBOX}/docs/other-notes.md"

# The .claude/ side of the same bug: the old guard's blanket ".claude/*"
# exemption let ANY ticket rewrite the guard itself. T-3's scope has no
# .claude/ entry, so this must now be denied.
assert_deny "closing the .claude/* hole: T-3 (no .claude/ in scope) cannot rewrite scope_guard.sh itself" \
  "${SANDBOX}/.claude/hooks/scope_guard.sh"
assert_old_hook_allowed "regression proof: the OLD hook WRONGLY let T-3 rewrite scope_guard.sh (blanket .claude/* exemption reproduced)" \
  "${SANDBOX}/.claude/hooks/scope_guard.sh"

# ===========================================================================
# Acceptance 3: a path outside CLAUDE_PROJECT_DIR is ALLOWED, not denied.
# ===========================================================================
set_active_ticket "T-3"
OUTSIDE_PATH="${WORK_DIR}/outside-the-repo/scratch.txt"
assert_allow "acc3: a path outside CLAUDE_PROJECT_DIR (scratch/tmp-shaped) is ALLOWED" \
  "$OUTSIDE_PATH"
assert_old_hook_denied "acc3 regression proof: the OLD hook WRONGLY denied a path outside the project dir" \
  "$OUTSIDE_PATH"

# ===========================================================================
# Checklist (a): claiming a ticket must still work. Writing
# .claude/active-ticket must be allowed under an unrelated ticket's scope
# (T-3's scope names nothing under .claude/).
# ===========================================================================
set_active_ticket "T-3"
assert_allow "checklist (a): writing .claude/active-ticket is ALLOWED regardless of the active ticket's scope" \
  "${SANDBOX}/.claude/active-ticket"

# ===========================================================================
# Checklist (b) (defence in depth): .claude/evidence/<id>.pass must be
# writable regardless of the active ticket's scope.
# ===========================================================================
assert_allow "checklist (b): writing .claude/evidence/T-3.pass is ALLOWED regardless of the active ticket's scope" \
  "${SANDBOX}/.claude/evidence/T-3.pass"

# ===========================================================================
# Checklist (e): T-20's own edits (this ticket's scope: .claude/hooks/**,
# .claude/settings.json, CLAUDE.md, .github/workflows/**) remain legal --
# the guard doesn't paint T-20 into a corner, including editing the hook
# script and this very test file.
# ===========================================================================
set_active_ticket "T-20"
assert_allow "checklist (e): T-20 editing scope_guard.sh itself is ALLOWED (in its own declared scope)" \
  "${SANDBOX}/.claude/hooks/scope_guard.sh"
assert_allow "checklist (e): T-20 editing this test file under .claude/hooks/** is ALLOWED" \
  "${SANDBOX}/.claude/hooks/tests/run_scope_guard_tests.sh"
assert_allow "checklist (e): T-20 editing CLAUDE.md is ALLOWED (in its own declared scope)" \
  "${SANDBOX}/CLAUDE.md"
assert_deny "checklist (e) sanity: T-20 cannot touch docs/tickets.json -- not in its declared scope" \
  "${SANDBOX}/docs/tickets.json"

# T-22 (a distinct ticket that shares .claude/hooks/** and
# .github/workflows/** but NOT CLAUDE.md or .claude/settings.json)
set_active_ticket "T-22"
assert_allow "T-22: scripts/** is in its declared scope" \
  "${SANDBOX}/scripts/evidence_fingerprint.sh"
assert_deny "T-22: CLAUDE.md is NOT in its declared scope (only T-20/T-21/T-24 have it)" \
  "${SANDBOX}/CLAUDE.md"

# ===========================================================================
# No active ticket claimed at all -> unrestricted (matches the pre-existing,
# unchanged "don't block setup work" behaviour).
# ===========================================================================
clear_active_ticket
assert_allow "no active ticket: any write is ALLOWED (setup/pre-claim state)" \
  "${SANDBOX}/anything/at/all.txt"

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d scope guard tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d scope guard test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
