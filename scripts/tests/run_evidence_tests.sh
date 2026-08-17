#!/usr/bin/env bash
# run_evidence_tests.sh (T-34) - tests scripts/check_evidence_staleness.sh
# (the live evidence-freshness gate, stage [1/4] of scripts/verify_submission.sh)
# against BOTH receipt formats it now reads.
#
# LINEAGE: this suite replaces .claude/hooks/tests/run_evidence_tests.sh,
# deleted in commit dc23222 along with the OLD harness it drove
# (.claude/hooks/verify_gate.sh). Roughly half of that suite's 479 lines
# exercised verify_gate.sh directly (its own allow/deny exit codes, and its
# own resilience to a missing/corrupted evidence_fingerprint.sh when MINTING
# a .pass receipt) -- that hook no longer exists, so none of that is
# portable, and per the T-34 brief it is not stubbed or faked here. What
# *is* portable is everything about READING a receipt after the fact:
# check_evidence_staleness.sh, evidence_fingerprint.sh, and (new in T-34)
# .claude/scripts/harness_lib.py's `fingerprint` subcommand all still exist
# and are exercised directly.
#
# WHY TWO FORMATS: the OLD harness wrote .claude/evidence/<T-id>.pass
# (sha=/ts=/fp= key=value lines, fp computed by evidence_fingerprint.sh's
# fingerprint_live()). The NEW harness (.claude/scripts/harness_lib.py)
# writes .claude/evidence/<T-id>.json ({"fingerprint": ...}, computed by
# harness_lib.py's own fingerprint()). These are DIFFERENT algorithms (see
# scripts/check_evidence_staleness.sh's header comment for the full
# comparison) -- a .json receipt's fingerprint must only ever be recomputed
# via harness_lib.py, never via fingerprint_live(). Several cases below
# exist specifically to pin that down.
#
# WHY MISSING: before T-34, the checker walked .claude/evidence/*.pass
# directly, so an id in docs/tickets.json with no receipt was invisible --
# deleting every receipt made the loop run zero times and exit 0 (green,
# attesting to nothing). T-34 makes docs/tickets.json's id list authoritative
# and gives "no receipt of either format" its own terminal, gate-failing
# MISSING status. Several cases below prove that directly.
#
# Everything runs inside a throwaway sandbox git repo with a fabricated
# docs/tickets.json and sandbox-local COPIES of check_evidence_staleness.sh,
# evidence_fingerprint.sh, harness_lib.py, and verify_submission.sh (so the
# missing/corrupted-dependency cases can safely delete/corrupt those copies
# without ever touching the real project's files). This script never writes
# to the real project's .claude/evidence/ or docs/tickets.json, and never
# re-runs a real ticket's verify command.
#
# Usage: bash scripts/tests/run_evidence_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REAL_STALENESS="${REPO_ROOT}/scripts/check_evidence_staleness.sh"
REAL_FPLIB="${REPO_ROOT}/scripts/evidence_fingerprint.sh"
REAL_HARNESSLIB="${REPO_ROOT}/.claude/scripts/harness_lib.py"
REAL_SUBMIT="${REPO_ROOT}/scripts/verify_submission.sh"

for f in "$REAL_STALENESS" "$REAL_FPLIB" "$REAL_HARNESSLIB" "$REAL_SUBMIT"; do
  if [ ! -f "$f" ]; then
    echo "run_evidence_tests: BUG -- expected file missing: $f" >&2
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
STALENESS="${SANDBOX}/scripts/check_evidence_staleness.sh"
FPLIB="${SANDBOX}/scripts/evidence_fingerprint.sh"
HARNESSLIB="${SANDBOX}/.claude/scripts/harness_lib.py"
SUBMIT="${SANDBOX}/scripts/verify_submission.sh"
EVID="${SANDBOX}/.claude/evidence"

mkdir -p "${SANDBOX}/docs" "${SANDBOX}/.claude/evidence" "${SANDBOX}/.claude/scripts" "${SANDBOX}/scripts" \
  "${SANDBOX}/scopeJC" "${SANDBOX}/scopeJS" "${SANDBOX}/scopeJU" \
  "${SANDBOX}/scopePC" "${SANDBOX}/scopePS" "${SANDBOX}/scopePU1" "${SANDBOX}/scopePU2" \
  "${SANDBOX}/scopeBoth1" "${SANDBOX}/scopeBoth2" "${SANDBOX}/scopeLifecycle"

cp "$REAL_STALENESS" "$STALENESS"
cp "$REAL_FPLIB" "$FPLIB"
cp "$REAL_HARNESSLIB" "$HARNESSLIB"
cp "$REAL_SUBMIT" "$SUBMIT"
chmod +x "$STALENESS" "$SUBMIT"

git -C "$SANDBOX" init -q -b main
git -C "$SANDBOX" config user.email "test@example.com"
git -C "$SANDBOX" config user.name "Evidence Test"

echo jc1 >"${SANDBOX}/scopeJC/f.txt"
echo js1 >"${SANDBOX}/scopeJS/f.txt"
echo ju1 >"${SANDBOX}/scopeJU/f.txt"
echo pc1 >"${SANDBOX}/scopePC/f.txt"
echo ps1 >"${SANDBOX}/scopePS/f.txt"
echo pu1 >"${SANDBOX}/scopePU1/f.txt"
echo pu2 >"${SANDBOX}/scopePU2/f.txt"
echo b1a >"${SANDBOX}/scopeBoth1/f.txt"
echo b2a >"${SANDBOX}/scopeBoth2/f.txt"
echo lc1 >"${SANDBOX}/scopeLifecycle/f.txt"

python3 - "${SANDBOX}/docs/tickets.json" <<'PYEOF'
import json, sys

def t(tid, scope):
    return {"id": tid, "verify": "true", "scope": [scope]}

tickets = [
    t("T-9101", "scopeJC/**"),
    t("T-9102", "scopeJS/**"),
    t("T-9103", "scopeJU/**"),
    t("T-9201", "scopePC/**"),
    t("T-9202", "scopePS/**"),
    t("T-9203", "scopePU1/**"),
    t("T-9204", "scopePU2/**"),
    t("T-9301", "scopeMissing1/**"),
    t("T-9302", "scopeMissing2/**"),
    t("T-9303", "scopeMissing3/**"),
    t("T-9401", "scopeBoth1/**"),
    t("T-9402", "scopeBoth2/**"),
    t("T-9501", "scopeLifecycle/**"),
]
doc = {"full_verify": "true", "tickets": tickets}
with open(sys.argv[1], "w") as f:
    json.dump(doc, f, indent=1)
PYEOF

git -C "$SANDBOX" add -A
git -C "$SANDBOX" commit -qm "sandbox init"
HEAD="$(git -C "$SANDBOX" rev-parse HEAD)"
NOW="$(date -u +%s)"

# ---------------------------------------------------------------------------
# Helpers: compute a fingerprint with each format's OWN algorithm, and
# hand-write a receipt in either format. Hand-writing receipt DATA (not the
# hook's/harness's decision logic) mirrors the deleted suite's own GAP3
# precedent ("hand-written: legacy shape, no sha=, no fp=") -- this is test
# fixture construction, not faking the system under test.
# ---------------------------------------------------------------------------
compute_json_fp() {
  CLAUDE_PROJECT_DIR="$SANDBOX" python3 "$HARNESSLIB" fingerprint "$1"
}
compute_pass_fp() {
  ( cd "$SANDBOX" && source "$FPLIB" && fingerprint_live "$1" )
}
write_pass_receipt() {
  # write_pass_receipt <tid> <fp>
  { printf 'sha=%s\n' "$HEAD"; printf 'ts=%s\n' "$NOW"; printf 'fp=%s\n' "$2"; } >"${EVID}/$1.pass"
}
write_json_receipt() {
  # write_json_receipt <tid> <fp|""> -- empty fp omits the field entirely
  if [ -z "$2" ]; then
    printf '{"ticket":"%s","session":"test","verify":"true","commit":"%s","attempts":0,"ts":%s}\n' "$1" "$HEAD" "$NOW" >"${EVID}/$1.json"
  else
    printf '{"ticket":"%s","session":"test","verify":"true","commit":"%s","fingerprint":"%s","attempts":0,"ts":%s}\n' "$1" "$HEAD" "$2" "$NOW" >"${EVID}/$1.json"
  fi
}
run_check() {
  bash "$STALENESS" "$SANDBOX" 2>&1
}
# NOTE the herestrings below, and do NOT "simplify" them back to
# `echo "$2" | grep -qE "$3"`. This file runs under `set -uo pipefail`
# (line 45). `grep -q` exits the instant it finds a match; when the match
# is EARLY in a large output, `echo` is still writing, takes SIGPIPE, and
# dies non-zero -- and pipefail then reports the whole pipeline as failed
# even though grep matched. Observed for real: the T-9103 assertion below
# (an early line) flaked ~1 run in 15, while the summary-line assertions
# (last lines, echo already done writing) never flaked.
#
# The polarity matters and is why this is worth a comment. In
# assert_match the race yields a FALSE FAILURE -- loud, annoying, and it
# would randomly fail any ticket close pinned to this suite. In
# assert_no_match the same race yields a FALSE PASS: a genuine match
# returns non-zero, the `if` takes the else branch, and a real regression
# is reported green. A herestring feeds grep from a temp file with no
# pipeline at all, so neither race exists.
assert_match() {
  # assert_match <case-name> <output> <regex>
  if grep -qE "$3" <<<"$2"; then pass "$1"; else fail "$1" "expected to match /$3/ -- got:\n$2"; fi
}
assert_no_match() {
  # assert_no_match <case-name> <output> <regex>
  if grep -qE "$3" <<<"$2"; then fail "$1" "expected NOT to match /$3/ -- got:\n$2"; else pass "$1"; fi
}

# ===========================================================================
# .json format: CURRENT, STALE, UNVERIFIABLE (missing fingerprint field)
# ===========================================================================
write_json_receipt "T-9101" "$(compute_json_fp T-9101)"     # CURRENT

FP_9102="$(compute_json_fp T-9102)"
write_json_receipt "T-9102" "$FP_9102"
echo "js1-REOPENED" >"${SANDBOX}/scopeJS/f.txt"              # STALE after minting

write_json_receipt "T-9103" ""                                # UNVERIFIABLE: no fingerprint field

# ===========================================================================
# .pass format: CURRENT, STALE, UNVERIFIABLE (legacy bare-epoch, and the
# FP-COMPUTE-FAILED sentinel)
# ===========================================================================
write_pass_receipt "T-9201" "$(compute_pass_fp 'scopePC/**')"  # CURRENT

FP_9202="$(compute_pass_fp 'scopePS/**')"
write_pass_receipt "T-9202" "$FP_9202"
echo "ps1-REOPENED" >"${SANDBOX}/scopePS/f.txt"                 # STALE after minting

printf '%s\n' "$NOW" >"${EVID}/T-9203.pass"                    # UNVERIFIABLE: legacy bare epoch, no keys at all (matches this repo's real T-0..T-8 shape)
write_pass_receipt "T-9204" "FP-COMPUTE-FAILED"                 # UNVERIFIABLE: explicit sentinel

# ===========================================================================
# MISSING (T-34): T-9301/9302/9303 declared in tickets.json, no receipt of
# either format ever written for them.
# ===========================================================================
# (nothing to do -- absence is the fixture)

# ===========================================================================
# Format precedence: a ticket with BOTH a .pass and a .json receipt is
# judged by the .json ONLY, regardless of which format is "correct".
# ===========================================================================
write_pass_receipt "T-9401" "0000000000000000000000000000000000000000000000000000000000000000"  # deliberately wrong
write_json_receipt "T-9401" "$(compute_json_fp T-9401)"                                             # correct -> overall must be CURRENT

write_pass_receipt "T-9402" "$(compute_pass_fp 'scopeBoth2/**')"                                   # correct
write_json_receipt "T-9402" "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"       # deliberately wrong -> overall must be STALE

# ===========================================================================
# Real lifecycle roundtrip (T-9501): drive the actual claim/close path
# through harness_lib.py (not hand-written), proving the checker reads a
# receipt exactly as the live harness mints one, end to end.
# ===========================================================================
CLAIM_OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" python3 "$HARNESSLIB" claim T-9501 "test setup" 2>&1)"
CLAIM_RC=$?
if [ "$CLAIM_RC" -eq 0 ]; then pass "lifecycle: claim T-9501 succeeds"; else fail "lifecycle: claim T-9501 succeeds" "exit $CLAIM_RC: $CLAIM_OUT"; fi
CLOSE_OUT="$(CLAUDE_PROJECT_DIR="$SANDBOX" python3 "$HARNESSLIB" close 2>&1)"
CLOSE_RC=$?
if [ "$CLOSE_RC" -eq 0 ]; then pass "lifecycle: close T-9501 succeeds and mints a .json receipt"; else fail "lifecycle: close T-9501 succeeds and mints a .json receipt" "exit $CLOSE_RC: $CLOSE_OUT"; fi
if [ -f "${EVID}/T-9501.json" ]; then pass "lifecycle: T-9501.json exists after close"; else fail "lifecycle: T-9501.json exists after close" "no receipt written"; fi

# ===========================================================================
# The full run, checked against every case above at once.
# ===========================================================================
BASE_OUT="$(run_check)"; BASE_RC=$?

if [ "$BASE_RC" -ne 0 ]; then pass "overall gate exits non-zero (STALE/UNVERIFIABLE/MISSING present)"; else fail "overall gate exits non-zero (STALE/UNVERIFIABLE/MISSING present)" "exit 0: $BASE_OUT"; fi

assert_match "json CURRENT (T-9101)"                "$BASE_OUT" '^CURRENT +T-9101: .*\(json\)'
assert_match "json STALE (T-9102)"                  "$BASE_OUT" '^STALE +T-9102:.*\(json\)'
assert_match "json UNVERIFIABLE, no fingerprint field (T-9103)" "$BASE_OUT" '^UNVERIFIABLE +T-9103:'

assert_match "pass CURRENT (T-9201)"                "$BASE_OUT" '^CURRENT +T-9201: .*\(pass\)'
assert_match "pass STALE (T-9202)"                  "$BASE_OUT" '^STALE +T-9202:.*\(pass\)'
assert_match "pass UNVERIFIABLE, legacy bare-epoch (T-9203)"    "$BASE_OUT" '^UNVERIFIABLE +T-9203:'
assert_match "pass UNVERIFIABLE, FP-COMPUTE-FAILED (T-9204)"    "$BASE_OUT" '^UNVERIFIABLE +T-9204:'

assert_match "MISSING T-9301" "$BASE_OUT" '^MISSING +T-9301:'
assert_match "MISSING T-9302" "$BASE_OUT" '^MISSING +T-9302:'
assert_match "MISSING T-9303" "$BASE_OUT" '^MISSING +T-9303:'

assert_match "both formats: .json wins CURRENT over a wrong .pass (T-9401)" "$BASE_OUT" '^CURRENT +T-9401: .*\(json\)'
assert_match "both formats: .json wins STALE over a correct .pass (T-9402)" "$BASE_OUT" '^STALE +T-9402:.*\(json\)'

assert_match "lifecycle receipt reads CURRENT (T-9501)" "$BASE_OUT" '^CURRENT +T-9501: .*\(json\)'

assert_match "summary STALE: line lists T-9102, T-9202, T-9402" "$BASE_OUT" '^STALE:.*T-9102.*T-9202.*T-9402'
assert_match "summary UNVERIFIABLE: line lists T-9103, T-9203, T-9204" "$BASE_OUT" '^UNVERIFIABLE:.*T-9103.*T-9203.*T-9204'
assert_match "summary MISSING: line lists T-9301, T-9302, T-9303" "$BASE_OUT" '^MISSING:.*T-9301.*T-9302.*T-9303'

# ===========================================================================
# Fingerprint determinism: recomputing either algorithm twice against
# unchanged content produces the identical token.
# ===========================================================================
D1="$(compute_json_fp T-9101)"; D2="$(compute_json_fp T-9101)"
if [ -n "$D1" ] && [ "$D1" = "$D2" ]; then pass "json fingerprint is deterministic across repeated calls"; else fail "json fingerprint is deterministic across repeated calls" "first=$D1 second=$D2"; fi

D3="$(compute_pass_fp 'scopePC/**')"; D4="$(compute_pass_fp 'scopePC/**')"
if [ -n "$D3" ] && [ "$D3" = "$D4" ]; then pass "pass fingerprint is deterministic across repeated calls"; else fail "pass fingerprint is deterministic across repeated calls" "first=$D3 second=$D4"; fi

# ===========================================================================
# harness_lib.py MISSING / CORRUPTED: a .json receipt's fingerprint can only
# be recomputed by harness_lib.py itself (Part 0's finding -- the two
# algorithms differ, so falling back to fingerprint_live() would be a false
# STALE). Both failure modes must degrade to UNVERIFIABLE, never a silent
# CURRENT and never a crash of the whole run.
# ===========================================================================
mv "$HARNESSLIB" "${HARNESSLIB}.hidden"
MISSING_LIB_OUT="$(run_check)"
mv "${HARNESSLIB}.hidden" "$HARNESSLIB"
assert_match "harness_lib.py MISSING -> T-9101 reads UNVERIFIABLE, not CURRENT" "$MISSING_LIB_OUT" '^UNVERIFIABLE +T-9101:.*does not exist'
RESTORED_OUT="$(run_check)"
assert_match "restoring harness_lib.py -> T-9101 reads CURRENT again" "$RESTORED_OUT" '^CURRENT +T-9101: .*\(json\)'

cp "$HARNESSLIB" "${HARNESSLIB}.bak"
printf 'this is not { valid python (((\n' >"$HARNESSLIB"
CORRUPT_LIB_OUT="$(run_check)"
cp "${HARNESSLIB}.bak" "$HARNESSLIB"
assert_match "harness_lib.py CORRUPTED -> T-9101 reads UNVERIFIABLE, not CURRENT" "$CORRUPT_LIB_OUT" '^UNVERIFIABLE +T-9101:.*recompute FAILED'
RESTORED_OUT2="$(run_check)"
assert_match "restoring harness_lib.py -> T-9101 reads CURRENT again (2)" "$RESTORED_OUT2" '^CURRENT +T-9101: .*\(json\)'

# ===========================================================================
# evidence_fingerprint.sh MISSING / CORRUPTED: this is the .pass format's
# ONLY fingerprint algorithm. Losing it must never silently report a .pass
# receipt CURRENT -- it must fail safe (the gate stays non-zero).
# ===========================================================================
mv "$FPLIB" "${FPLIB}.hidden"
MISSING_FPLIB_OUT="$(run_check)"; MISSING_FPLIB_RC=$?
mv "${FPLIB}.hidden" "$FPLIB"
assert_no_match "evidence_fingerprint.sh MISSING -> T-9201 is never reported CURRENT" "$MISSING_FPLIB_OUT" '^CURRENT +T-9201:'
if [ "$MISSING_FPLIB_RC" -ne 0 ]; then pass "evidence_fingerprint.sh MISSING -> overall gate still fails closed"; else fail "evidence_fingerprint.sh MISSING -> overall gate still fails closed" "exit 0"; fi
RESTORED_OUT3="$(run_check)"
assert_match "restoring evidence_fingerprint.sh -> T-9201 reads CURRENT again" "$RESTORED_OUT3" '^CURRENT +T-9201: .*\(pass\)'

cp "$FPLIB" "${FPLIB}.bak"
printf 'this is not { valid bash (((\n' >"$FPLIB"
CORRUPT_FPLIB_OUT="$(run_check)"; CORRUPT_FPLIB_RC=$?
cp "${FPLIB}.bak" "$FPLIB"
assert_no_match "evidence_fingerprint.sh CORRUPTED -> T-9201 is never reported CURRENT" "$CORRUPT_FPLIB_OUT" '^CURRENT +T-9201:'
if [ "$CORRUPT_FPLIB_RC" -ne 0 ]; then pass "evidence_fingerprint.sh CORRUPTED -> overall gate still fails closed"; else fail "evidence_fingerprint.sh CORRUPTED -> overall gate still fails closed" "exit 0"; fi
RESTORED_OUT4="$(run_check)"
assert_match "restoring evidence_fingerprint.sh -> T-9201 reads CURRENT again (2)" "$RESTORED_OUT4" '^CURRENT +T-9201: .*\(pass\)'

# ===========================================================================
# verify_submission.sh (stage [1/4]) actually fails when this gate fails.
# Drives the REAL check under test via the sandbox's own copy of
# verify_submission.sh (which shells out to its own sandbox-local
# check_evidence_staleness.sh, exactly as the real one shells out to its
# real sibling) -- not a reimplementation.
# ===========================================================================
SUBMIT_OUT="$(bash "$SUBMIT" "$SANDBOX" 2>&1)"; SUBMIT_RC=$?
if [ "$SUBMIT_RC" -ne 0 ]; then pass "verify_submission.sh exits non-zero when the evidence gate fails"; else fail "verify_submission.sh exits non-zero when the evidence gate fails" "exit 0: $SUBMIT_OUT"; fi
assert_match "verify_submission.sh names stage [1/4] as the failure" "$SUBMIT_OUT" '\[1/4\] evidence freshness gate'
assert_match "verify_submission.sh's own FAILED message for stage 1" "$SUBMIT_OUT" 'FAILED -- stale or unverifiable ticket evidence exists'

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d evidence tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d evidence test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
