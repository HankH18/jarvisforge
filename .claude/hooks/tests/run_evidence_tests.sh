#!/usr/bin/env bash
# run_evidence_tests.sh (T-22) - verify_gate.sh's TaskCompleted hook is the
# gate every ticket in this project depends on to close; this suite proves
# its receipt format change (bind it to a content fingerprint of the
# ticket's scope, plus the sha/ts) doesn't break that gate, and that the
# staleness checker is sound: bound to WHAT changed, not WHEN or in what
# commit-graph shape.
#
# DESIGN HISTORY (why content, not commit position): a first version
# compared the receipt's sha to the "last commit touching scope" and was
# stale-by-construction (the hook fires BEFORE the ticket's own completion
# commit, which always touches its own scope). A second version excluded a
# single positional "GRACE" commit to work around that -- also unsound:
# content-blind, so an unprefixed commit that was the ONLY post-receipt
# change got silently pardoned, a foreign commit landing between a receipt
# and its own completion commit got pardoned INSTEAD of the real
# completion (false STALE on a clean close), and non-linear history broke
# the positional assumption outright. The fix -- pure content-fingerprint
# comparison, `git log` used only for attribution labeling, never for the
# verdict -- is proven by proofs A/B, C, D, E, F, H below.
#
# A third round then found four narrower content-fingerprint gaps, each
# with its own proof below:
#   GAP 1 - byte-hash alone misses a chmod (mode is now part of the
#     fingerprint: (path, mode, hash) triples, not (path, hash) pairs).
#   GAP 2 - gitignored files inside a ticket's scope are invisible to the
#     fingerprint on purpose (build artifacts must not be hashed), but
#     that exclusion used to be silent; it is now disclosed per ticket.
#   GAP 3 - a receipt written before fingerprinting existed (or any
#     receipt missing fp=) used to get a "closest commit in time" guessed
#     baseline -- provably unsound (an unverified regression can land
#     closer in time than the real completion). It is now a hard,
#     non-inferred UNVERIFIABLE status that fails the gate.
#   GAP 4 - if fingerprinting itself fails at verify time, the receipt
#     used to get a blank fp= that silently fell into the legacy path,
#     inheriting Gap 3's risk. It now gets an explicit
#     "FP-COMPUTE-FAILED" sentinel that reads UNVERIFIABLE, never CURRENT,
#     and the hook's exit-0-on-pass behavior is unaffected either way.
#
# Everything except the last "real repo" case runs inside a throwaway
# sandbox git repo with its own fabricated docs/tickets.json -- this script
# never re-runs a real ticket's verify command and never writes to the real
# project's .claude/evidence/.
#
# Usage: bash .claude/hooks/tests/run_evidence_tests.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HOOK="${REPO_ROOT}/.claude/hooks/verify_gate.sh"
FPLIB="${REPO_ROOT}/scripts/evidence_fingerprint.sh"
STALENESS="${REPO_ROOT}/scripts/check_evidence_staleness.sh"
SUBMIT="${REPO_ROOT}/scripts/verify_submission.sh"

FAILURES=0
CASES_RUN=0

pass() { CASES_RUN=$((CASES_RUN + 1)); printf 'PASS: %s\n' "$1"; }
fail() { CASES_RUN=$((CASES_RUN + 1)); FAILURES=$((FAILURES + 1)); printf 'FAIL: %s -- %s\n' "$1" "$2"; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

SANDBOX="${WORK_DIR}/sandbox"
mkdir -p "${SANDBOX}/docs" "${SANDBOX}/.claude/hooks" "${SANDBOX}/scripts" \
  "${SANDBOX}/scopeA" "${SANDBOX}/scopeB" "${SANDBOX}/sub" \
  "${SANDBOX}/scopeD" "${SANDBOX}/scopeF" "${SANDBOX}/scopeG" \
  "${SANDBOX}/scopeH" "${SANDBOX}/scopeH-other" "${SANDBOX}/scopeI" \
  "${SANDBOX}/scopeK" "${SANDBOX}/scopeL" "${SANDBOX}/scopeM" \
  "${SANDBOX}/scopeN" "${SANDBOX}/scopeO"

git -C "$SANDBOX" init -q -b main
git -C "$SANDBOX" config user.email "test@example.com"
git -C "$SANDBOX" config user.name "Evidence Test"

cp "$HOOK" "${SANDBOX}/.claude/hooks/verify_gate.sh"
chmod +x "${SANDBOX}/.claude/hooks/verify_gate.sh"
cp "$FPLIB" "${SANDBOX}/scripts/evidence_fingerprint.sh"

echo marker >"${SANDBOX}/sub/marker.txt"
echo a1 >"${SANDBOX}/scopeA/f.txt"
echo b1 >"${SANDBOX}/scopeB/f.txt"
echo d1 >"${SANDBOX}/scopeD/f.txt"
echo f1 >"${SANDBOX}/scopeF/f.txt"
echo g1 >"${SANDBOX}/scopeG/f.txt"
echo h1 >"${SANDBOX}/scopeH/f.txt"
echo ho1 >"${SANDBOX}/scopeH-other/f.txt"
echo i1 >"${SANDBOX}/scopeI/f.txt"
echo k1 >"${SANDBOX}/scopeK/f.sh"          # GAP 1: mode
chmod -x "${SANDBOX}/scopeK/f.sh"
echo tracked >"${SANDBOX}/scopeL/tracked.txt"   # GAP 2: gitignore disclosure
echo m1 >"${SANDBOX}/scopeM/f.txt"         # GAP 3: legacy UNVERIFIABLE
echo n1 >"${SANDBOX}/scopeN/f.txt"         # GAP 4: fp-compute-failed (missing lib)
echo o1 >"${SANDBOX}/scopeO/f.txt"         # GAP 4: fp-compute-failed (corrupted lib)
printf 'scopeL/*.ignored\n' >"${SANDBOX}/.gitignore"

cat >"${SANDBOX}/docs/tickets.json" <<'JSON'
{"tickets":[
  {"id":"T-9001","verify":"true","scope":["scopeA/**"]},
  {"id":"T-9002","verify":"false","scope":["scopeB/**"]},
  {"id":"T-9003","verify":"cd sub && test -f marker.txt && echo \"found it\"","scope":["sub/**"]},
  {"id":"T-9004","verify":"false || true","scope":["scopeA/**"]},
  {"id":"T-9010","verify":"true","scope":["scopeD/**"]},
  {"id":"T-9012","verify":"true","scope":["scopeF/**"]},
  {"id":"T-9013","verify":"true","scope":["scopeG/**"]},
  {"id":"T-9014","verify":"true","scope":["scopeH/**"]},
  {"id":"T-9015","verify":"true","scope":["scopeI/**"]},
  {"id":"T-9020","verify":"true","scope":["scopeK/**"]},
  {"id":"T-9021","verify":"true","scope":["scopeL/**"]},
  {"id":"T-9022","verify":"true","scope":["scopeM/**"]},
  {"id":"T-9023","verify":"true","scope":["scopeN/**"]},
  {"id":"T-9024","verify":"true","scope":["scopeO/**"]}
]}
JSON

git -C "$SANDBOX" add -A
git -C "$SANDBOX" commit -qm "sandbox init"

run_hook() {
  # run_hook <subject>: exit code becomes this function's exit code.
  local subject="$1"
  printf '{"task":{"subject":"%s"}}' "$subject" |
    CLAUDE_PROJECT_DIR="$SANDBOX" "${SANDBOX}/.claude/hooks/verify_gate.sh"
}

commit_all() {
  # commit_all <message>: commits whatever is currently on disk.
  git -C "$SANDBOX" add -A
  git -C "$SANDBOX" commit -qm "$1" >/dev/null
}

stale_check() { "$STALENESS" "$SANDBOX" 2>&1; }

# ===========================================================================
# Part 1: hook safety (block/allow), both directions, and real ticket shapes
# ===========================================================================

HEAD1="$(git -C "$SANDBOX" rev-parse HEAD)"
OUT=$(run_hook "T-9001: dummy ticket" 2>&1); RC=$?
if [ "$RC" -eq 0 ]; then pass "hook allows a passing verify (exit 0)"; else fail "hook allows a passing verify (exit 0)" "got exit $RC, output: $OUT"; fi

RECEIPT="${SANDBOX}/.claude/evidence/T-9001.pass"
if [ -f "$RECEIPT" ]; then
  SHA_LINE="$(grep -E '^sha=' "$RECEIPT" | cut -d= -f2-)"
  TS_LINE="$(grep -E '^ts=' "$RECEIPT" | cut -d= -f2-)"
  FP_LINE="$(grep -E '^fp=' "$RECEIPT" | cut -d= -f2-)"
  if [ "$SHA_LINE" = "$HEAD1" ]; then pass "fresh receipt records the sha it was produced at"; else fail "fresh receipt records the sha it was produced at" "receipt sha='$SHA_LINE' expected='$HEAD1'"; fi
  if echo "$TS_LINE" | grep -qE '^[0-9]+$'; then pass "fresh receipt records a timestamp"; else fail "fresh receipt records a timestamp" "ts field='$TS_LINE'"; fi
  if [ -n "$FP_LINE" ] && [ "$FP_LINE" != "unknown" ] && [ "$FP_LINE" != "FP-COMPUTE-FAILED" ]; then pass "fresh receipt records a content fingerprint"; else fail "fresh receipt records a content fingerprint" "fp field='$FP_LINE'"; fi
else
  fail "fresh receipt records the sha it was produced at" "no receipt file written at $RECEIPT"
  fail "fresh receipt records a timestamp" "no receipt file written"
  fail "fresh receipt records a content fingerprint" "no receipt file written"
fi

OUT=$(run_hook "T-9002: dummy ticket" 2>&1); RC=$?
if [ "$RC" -eq 2 ]; then pass "hook blocks a failing verify (exit 2)"; else fail "hook blocks a failing verify (exit 2)" "got exit $RC, output: $OUT"; fi
if [ -f "${SANDBOX}/.claude/evidence/T-9002.pass" ]; then fail "hook writes no receipt on a failing verify" "receipt exists for T-9002"; else pass "hook writes no receipt on a failing verify"; fi

OUT=$(run_hook "T-9003: dummy ticket" 2>&1); RC=$?
if [ "$RC" -eq 0 ]; then pass "hook allows a cd/&&/quoted verify string (T-23-shaped)"; else fail "hook allows a cd/&&/quoted verify string (T-23-shaped)" "got exit $RC, output: $OUT"; fi

OUT=$(run_hook "T-9004: dummy ticket" 2>&1); RC=$?
if [ "$RC" -eq 0 ]; then pass "hook allows a verify string ending in || true (T-19-shaped)"; else fail "hook allows a verify string ending in || true (T-19-shaped)" "got exit $RC, output: $OUT"; fi

# ===========================================================================
# Proof A + B: silent regression, no completion commit ever lands, and the
# sole post-receipt commit is NOT prefixed with the ticket's id.
# ===========================================================================
run_hook "T-9010: dummy ticket" >/dev/null 2>&1
echo SILENT-REGRESSION >"${SANDBOX}/scopeD/f.txt"
commit_all "chore: cleanup"   # deliberately NOT prefixed "T-9010:"

AB_OUT="$(stale_check)"; AB_RC=$?
if echo "$AB_OUT" | grep -qE '^STALE +T-9010:'; then
  pass "PROOF A/B: unprefixed sole post-receipt commit changing scope reports STALE, not CURRENT"
else
  fail "PROOF A/B: unprefixed sole post-receipt commit changing scope reports STALE, not CURRENT" "$AB_OUT"
fi
if [ "$AB_RC" -ne 0 ]; then
  pass "PROOF A: staleness check exits non-zero on the silent regression"
else
  fail "PROOF A: staleness check exits non-zero on the silent regression" "exit 0: $AB_OUT"
fi

# ===========================================================================
# Proof C: normally closed, realistic claim -> hook -> completion-commit
# cycle, never touched again -> CURRENT.
# ===========================================================================
run_hook "T-9012: dummy ticket" >/dev/null 2>&1
commit_all "T-9012: completion"

C_OUT="$(stale_check)"
if echo "$C_OUT" | grep -qE '^CURRENT +T-9012:'; then
  pass "PROOF C: a normally-closed ticket, untouched since, reports CURRENT"
else
  fail "PROOF C: a normally-closed ticket, untouched since, reports CURRENT" "$C_OUT"
fi

# ===========================================================================
# Proof D: two commits after the receipt (completion + fixup). A no-op
# fixup (scope bytes unchanged) must NOT cause a false STALE; a real fixup
# (scope bytes changed) MUST.
# ===========================================================================
run_hook "T-9013: dummy ticket" >/dev/null 2>&1
commit_all "T-9013: completion"
echo "unrelated" >"${SANDBOX}/scopeG-note.txt"
commit_all "T-9013: fixup (does not touch scope)"

D1_OUT="$(stale_check)"
if echo "$D1_OUT" | grep -qE '^CURRENT +T-9013:'; then
  pass "PROOF D1: two post-receipt commits, fixup outside scope, stays CURRENT"
else
  fail "PROOF D1: two post-receipt commits, fixup outside scope, stays CURRENT" "$D1_OUT"
fi

echo g2-real-fix >"${SANDBOX}/scopeG/f.txt"
commit_all "T-9013: fixup (changes scope)"

D2_OUT="$(stale_check)"
if echo "$D2_OUT" | grep -qE '^STALE +T-9013:'; then
  pass "PROOF D2: a fixup that actually changes scope bytes reports STALE"
else
  fail "PROOF D2: a fixup that actually changes scope bytes reports STALE" "$D2_OUT"
fi

# ===========================================================================
# Proof E: another ticket's commit lands BETWEEN a receipt and its own
# completion commit, but touches a DIFFERENT scope.
# ===========================================================================
run_hook "T-9014: dummy ticket" >/dev/null 2>&1
echo "foreign edit, different scope entirely" >"${SANDBOX}/scopeH-other/f.txt"
commit_all "T-9099: unrelated ticket's commit lands before T-9014's own completion"
commit_all "T-9014: completion"

E_OUT="$(stale_check)"
if echo "$E_OUT" | grep -qE '^CURRENT +T-9014:'; then
  pass "PROOF E: a foreign commit landing between a receipt and its own completion (different scope) does not cause a false STALE"
else
  fail "PROOF E: a foreign commit landing between a receipt and its own completion (different scope) does not cause a false STALE" "$E_OUT"
fi

# ===========================================================================
# Proof F: non-linear history (branch, change scope, merge back).
# ===========================================================================
run_hook "T-9015: dummy ticket" >/dev/null 2>&1
commit_all "T-9015: completion"
git -C "$SANDBOX" checkout -q -b side-branch
echo "changed on a side branch" >"${SANDBOX}/scopeI/f.txt"
git -C "$SANDBOX" add -A
git -C "$SANDBOX" commit -qm "T-9015: reopened on a side branch"
git -C "$SANDBOX" checkout -q main
git -C "$SANDBOX" merge -q --no-ff side-branch -m "merge side-branch"

F_OUT="$(stale_check)"
if echo "$F_OUT" | grep -qE '^STALE +T-9015:'; then
  pass "PROOF F: a scope change merged in via non-linear history is still detected as STALE"
else
  fail "PROOF F: a scope change merged in via non-linear history is still detected as STALE" "$F_OUT"
fi

# ===========================================================================
# Proof H: uncommitted working-tree change, no commit at all.
# ===========================================================================
echo "uncommitted edit, never committed" >"${SANDBOX}/scopeF/f.txt"

H_OUT="$(stale_check)"
if echo "$H_OUT" | grep -qE '^STALE +T-9012:'; then
  pass "PROOF H: an uncommitted working-tree change to a scope file is caught as STALE"
else
  fail "PROOF H: an uncommitted working-tree change to a scope file is caught as STALE" "$H_OUT"
fi
git -C "$SANDBOX" checkout -q -- scopeF/f.txt

# ===========================================================================
# GAP 1: chmod +x on a scope file, byte content unchanged, must be STALE.
# chmod back must return it to CURRENT.
# ===========================================================================
run_hook "T-9020: dummy ticket" >/dev/null 2>&1
commit_all "T-9020: completion"

G1_BEFORE="$(stale_check)"
if echo "$G1_BEFORE" | grep -qE '^CURRENT +T-9020:'; then
  pass "GAP1 setup: T-9020 starts CURRENT before any chmod"
else
  fail "GAP1 setup: T-9020 starts CURRENT before any chmod" "$G1_BEFORE"
fi

chmod +x "${SANDBOX}/scopeK/f.sh"
G1_CHMOD_OUT="$(stale_check)"
if echo "$G1_CHMOD_OUT" | grep -qE '^STALE +T-9020:'; then
  pass "GAP1: chmod +x on a scope file (content unchanged) reports STALE"
else
  fail "GAP1: chmod +x on a scope file (content unchanged) reports STALE" "$G1_CHMOD_OUT"
fi

chmod -x "${SANDBOX}/scopeK/f.sh"
G1_RESTORED_OUT="$(stale_check)"
if echo "$G1_RESTORED_OUT" | grep -qE '^CURRENT +T-9020:'; then
  pass "GAP1: chmod back to the original mode returns to CURRENT"
else
  fail "GAP1: chmod back to the original mode returns to CURRENT" "$G1_RESTORED_OUT"
fi

# ===========================================================================
# GAP 2: gitignored file inside a ticket's scope is disclosed, not silent
# -- and the disclosure does not clutter a ticket whose scope has none.
# ===========================================================================
run_hook "T-9021: dummy ticket" >/dev/null 2>&1
commit_all "T-9021: completion"
echo "secret build artifact" >"${SANDBOX}/scopeL/leftover.ignored"   # matches .gitignore, stays untracked

G2_OUT="$(stale_check)"
G2_LINE="$(echo "$G2_OUT" | grep -E 'T-9021:')"
if echo "$G2_LINE" | grep -q '\[NOTE:.*gitignored'; then
  pass "GAP2: a gitignored file inside scope is disclosed with a [NOTE: ...] on that ticket's line"
else
  fail "GAP2: a gitignored file inside scope is disclosed with a [NOTE: ...] on that ticket's line" "$G2_LINE"
fi

G2_OTHER_LINE="$(echo "$G2_OUT" | grep -E 'T-9012:')"
if echo "$G2_OTHER_LINE" | grep -q '\[NOTE:'; then
  fail "GAP2: the disclosure does not clutter a ticket whose scope has no gitignored file" "$G2_OTHER_LINE"
else
  pass "GAP2: the disclosure does not clutter a ticket whose scope has no gitignored file"
fi

# ===========================================================================
# GAP 3: the verifier's constructed case -- a legacy receipt (no fp=) with
# an UNVERIFIED same-ticket-prefixed regression landing closer in time to
# the receipt than the real completion. The old "closest commit in time"
# baseline picked the regression and reported CURRENT. The fix removes
# baseline inference entirely: any receipt with no fp= is UNVERIFIABLE,
# unconditionally, regardless of commit timing.
# ===========================================================================
LEGACY_TS="$(date -u +%s)"
printf 'ts=%s\n' "$LEGACY_TS" >"${SANDBOX}/.claude/evidence/T-9022.pass"   # hand-written: legacy shape, no sha=, no fp=
echo "unverified regression, never re-verified" >"${SANDBOX}/scopeM/f.txt"
commit_all "T-9022: unverified regression (lands closer in time to the receipt than the real completion below)"
echo "the real completion, lands later" >"${SANDBOX}/scopeM/f.txt"
commit_all "T-9022: completion (the real one)"

G3_OUT="$(stale_check)"
G3_LINE="$(echo "$G3_OUT" | grep -E 'T-9022:')"
if echo "$G3_LINE" | grep -qE '^CURRENT'; then
  fail "GAP3: the verifier's closest-in-time regression case never reports CURRENT" "$G3_LINE"
else
  pass "GAP3: the verifier's closest-in-time regression case never reports CURRENT"
fi
if echo "$G3_LINE" | grep -qE '^UNVERIFIABLE'; then
  pass "GAP3: a receipt with no fp= reports UNVERIFIABLE, not an inferred verdict"
else
  fail "GAP3: a receipt with no fp= reports UNVERIFIABLE, not an inferred verdict" "$G3_LINE"
fi

G3_SUBMIT_OUT="$("$SUBMIT" "$SANDBOX" 2>&1)"; G3_SUBMIT_RC=$?
if [ "$G3_SUBMIT_RC" -ne 0 ]; then
  pass "GAP3: UNVERIFIABLE fails T-13's submission gate (acceptance 3: cannot report green)"
else
  fail "GAP3: UNVERIFIABLE fails T-13's submission gate (acceptance 3: cannot report green)" "exit 0: $G3_SUBMIT_OUT"
fi

# ===========================================================================
# GAP 4: fingerprinting fails at verify time (missing, then corrupted
# evidence_fingerprint.sh). The hook must still exit 0 on a passing verify
# (isolation from the block/allow decision), and the receipt must read
# UNVERIFIABLE later -- never CURRENT, never silently "legacy".
# ===========================================================================
mv "${SANDBOX}/scripts/evidence_fingerprint.sh" "${SANDBOX}/scripts/evidence_fingerprint.sh.hidden"
OUT=$(run_hook "T-9023: dummy ticket" 2>&1); RC=$?
mv "${SANDBOX}/scripts/evidence_fingerprint.sh.hidden" "${SANDBOX}/scripts/evidence_fingerprint.sh"

if [ "$RC" -eq 0 ]; then
  pass "GAP4a: hook still exits 0 on a passing verify when evidence_fingerprint.sh is MISSING"
else
  fail "GAP4a: hook still exits 0 on a passing verify when evidence_fingerprint.sh is MISSING" "got exit $RC, output: $OUT"
fi
FP23="$(grep -E '^fp=' "${SANDBOX}/.claude/evidence/T-9023.pass" 2>/dev/null | cut -d= -f2-)"
if [ "$FP23" = "FP-COMPUTE-FAILED" ]; then
  pass "GAP4a: the receipt gets the explicit FP-COMPUTE-FAILED sentinel, not a blank fp="
else
  fail "GAP4a: the receipt gets the explicit FP-COMPUTE-FAILED sentinel, not a blank fp=" "fp='$FP23'"
fi
commit_all "T-9023: completion"
G4A_OUT="$(stale_check)"
G4A_LINE="$(echo "$G4A_OUT" | grep -E 'T-9023:')"
if echo "$G4A_LINE" | grep -qE '^UNVERIFIABLE.*computation FAILED'; then
  pass "GAP4a: it later reads UNVERIFIABLE with the fingerprinting-failure reason, never CURRENT"
else
  fail "GAP4a: it later reads UNVERIFIABLE with the fingerprinting-failure reason, never CURRENT" "$G4A_LINE"
fi

printf 'this is not { valid bash (((\n' >"${SANDBOX}/scripts/evidence_fingerprint.sh"
OUT=$(run_hook "T-9024: dummy ticket" 2>&1); RC=$?
cp "$FPLIB" "${SANDBOX}/scripts/evidence_fingerprint.sh"

if [ "$RC" -eq 0 ]; then
  pass "GAP4b: hook still exits 0 on a passing verify when evidence_fingerprint.sh is CORRUPTED"
else
  fail "GAP4b: hook still exits 0 on a passing verify when evidence_fingerprint.sh is CORRUPTED" "got exit $RC, output: $OUT"
fi
FP24="$(grep -E '^fp=' "${SANDBOX}/.claude/evidence/T-9024.pass" 2>/dev/null | cut -d= -f2-)"
if [ "$FP24" = "FP-COMPUTE-FAILED" ]; then
  pass "GAP4b: a corrupted lib also produces the explicit sentinel, not a blank fp="
else
  fail "GAP4b: a corrupted lib also produces the explicit sentinel, not a blank fp=" "fp='$FP24'"
fi

# ===========================================================================
# verify_submission.sh (T-13's gate): still driven by the same exit code
# ===========================================================================
SUBMIT_OUT="$("$SUBMIT" "$SANDBOX" 2>&1)"; SUBMIT_RC=$?
if [ "$SUBMIT_RC" -ne 0 ] && echo "$SUBMIT_OUT" | grep -q "T-9010"; then
  pass "verify_submission.sh (T-13's gate) fails and names a stale ticket"
else
  fail "verify_submission.sh (T-13's gate) fails and names a stale ticket" "exit $SUBMIT_RC: $SUBMIT_OUT"
fi

# ===========================================================================
# Part 3: real repo, read-only. This is a PROPERTY check, not a snapshot.
#
# An earlier version of this section hard-coded "T-2 and T-6 are legacy"
# and "all 11 receipts predate fingerprinting" -- both true the hour they
# were written, both false the moment the documented remedy for
# UNVERIFIABLE ("re-run that ticket's verify to mint a fingerprinted
# receipt") was actually followed for every ticket, which is the intended
# end state, not a regression. A test that fails when the repo legitimately
# improves is binding transient environment state, not a behavioural
# property, so it does not hard-code which tickets are legacy. Instead it
# discovers, from whatever is in .claude/evidence/ right now, which
# receipts (if any) lack a usable fingerprint, and asserts the same
# behaviour the GAP3 synthetic fixtures prove in the sandbox above: a
# receipt with no fp= must read UNVERIFIABLE, never an inferred CURRENT.
# On this repo today that set is empty (every receipt is fingerprinted),
# so this is vacuously true -- still meaningful, because it will bite again
# the moment any receipt regresses to the legacy shape.
#
# This is NOT redundant with the GAP3 sandbox tests above: GAP3 proves the
# SCRIPT's logic is correct against a constructed fixture; this proves the
# ACTUAL DATA in this repo's .claude/evidence/ obeys that logic right now
# (e.g. it would also catch a hand-edited or corrupted real receipt that
# the sandbox fixtures never exercise).
# ===========================================================================
REAL_OUT="$("$STALENESS" "$REPO_ROOT" 2>&1 || true)"

UNFINGERPRINTED_REAL=""
for real_receipt in "${REPO_ROOT}/.claude/evidence"/T-*.pass; do
  [ -f "$real_receipt" ] || continue
  rtid="$(basename "$real_receipt" .pass)"
  rfp="$(grep -E '^fp=' "$real_receipt" 2>/dev/null | head -n1 | cut -d= -f2-)"
  if [ -z "$rfp" ] || [ "$rfp" = "FP-COMPUTE-FAILED" ]; then
    UNFINGERPRINTED_REAL="${UNFINGERPRINTED_REAL} ${rtid}"
  fi
done

REAL_PROPERTY_OK=1
REAL_PROPERTY_BAD=""
for rtid in $UNFINGERPRINTED_REAL; do
  echo "$REAL_OUT" | grep -qE "^UNVERIFIABLE +${rtid}:" || { REAL_PROPERTY_OK=0; REAL_PROPERTY_BAD="${REAL_PROPERTY_BAD} ${rtid}"; }
done

if [ "$REAL_PROPERTY_OK" -eq 1 ]; then
  if [ -n "$UNFINGERPRINTED_REAL" ]; then
    pass "real repo: every receipt currently lacking fp= reads UNVERIFIABLE (checked:${UNFINGERPRINTED_REAL})"
  else
    pass "real repo: every receipt currently lacking fp= reads UNVERIFIABLE (0 such receipts right now -- vacuously true)"
  fi
else
  fail "real repo: every receipt currently lacking fp= reads UNVERIFIABLE" "did not read UNVERIFIABLE for:${REAL_PROPERTY_BAD} -- full output: $REAL_OUT"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  printf 'SUMMARY: %d/%d evidence tests passed.\n' "$CASES_RUN" "$CASES_RUN"
  exit 0
else
  printf 'SUMMARY: %d/%d evidence test(s) FAILED.\n' "$FAILURES" "$CASES_RUN"
  exit 1
fi
