#!/usr/bin/env bash
# ===========================================================================
# T-28: no verify string can neuter itself.
#
# T-28's original verify pointed at .claude/hooks/tests/run_verify_lint_tests.sh,
# deleted in dc23222. Its subject SURVIVED, though: the lint moved into
# harness_lib.py's `lint_verify`, which cmd_claim calls before it will let a
# ticket be claimed. So this is a port against the live implementation.
#
# What it protects. A verify string is a ticket's only executable evidence.
# A string that cannot fail is worse than no string, because it mints a
# receipt that reads exactly like an earned one. T-19 was the live example:
# its verify ended `... --client echo || true`, so the gate passed
# unconditionally. Removing the `|| true` revealed the eval had been failing
# the whole time (10 fabrications against a max of 0).
#
# This suite drives harness_lib.lint_verify directly -- no sandbox repo is
# needed, since lint_verify is a pure function of the string.
#
# Usage: bash scripts/tests/run_verify_lint_tests.sh
# ===========================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HARNESS_LIB="${REPO_ROOT}/.claude/scripts/harness_lib.py"

if [ ! -f "$HARNESS_LIB" ]; then
  echo "FATAL: ${HARNESS_LIB} not found -- lint_verify is the subject of this suite." >&2
  exit 2
fi

PASSES=0
FAILURES=0
pass() { PASSES=$((PASSES + 1)); echo "PASS: $1"; }
fail() { FAILURES=$((FAILURES + 1)); echo "FAIL: $1 -- $2"; }

# Returns lint findings for a verify string, one per line (empty = clean).
lint() {
  CLAUDE_PROJECT_DIR="$REPO_ROOT" python3 - "$1" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.environ["CLAUDE_PROJECT_DIR"], ".claude", "scripts"))
import harness_lib as H
for msg in H.lint_verify(sys.argv[1]):
    print(msg)
PY
}

# NOTE the herestrings. Under `set -uo pipefail`, `echo "$x" | grep -q RE`
# returns non-zero when grep matches EARLY -- grep exits, echo takes SIGPIPE,
# pipefail propagates it. That yields false failures in a positive assertion
# and false PASSES in a negative one. Same fix as run_evidence_tests.sh.
assert_flagged() {
  # assert_flagged <case-name> <verify-string> <regex the finding must match>
  out="$(lint "$2")"
  if [ -z "$out" ]; then
    fail "$1" "expected a lint finding, got none"
  elif grep -qE "$3" <<<"$out"; then
    pass "$1"
  else
    fail "$1" "finding did not match /$3/ -- got: $out"
  fi
}

assert_clean() {
  # assert_clean <case-name> <verify-string>
  out="$(lint "$2")"
  if [ -z "$out" ]; then pass "$1"; else fail "$1" "expected no finding, got: $out"; fi
}

echo "=== self-neutering forms are rejected ==="
assert_flagged "'|| true' at the end"                    'pytest -q || true'                    'unconditionally'
assert_flagged "'|| true' mid-string"                    'a || true && b'                       'unconditionally'
assert_flagged "'||true' without a space"                'pytest -q ||true'                     'unconditionally'
assert_flagged "'|| :' (the ':' builtin)"                'pytest -q || :'                       'unconditionally'
assert_flagged "trailing '; true'"                       'pytest -q; true'                      'unconditionally'
assert_flagged "trailing '; :'"                          'pytest -q; :'                         'unconditionally'
assert_flagged "T-19's real historical string"           'cd backend && uv run pytest tests/grounding -q && uv run python -m evals.run --golden evals/golden.jsonl --max-fabrications 0 --client echo || true' 'unconditionally'

echo
echo "=== echo-only verifies are not evidence ==="
assert_flagged "bare echo"                               'echo ok'                              'evidence'
assert_flagged "echo with a message"                     'echo "T-5 done"'                      'evidence'
assert_clean   "echo followed by a real command"         'echo running && pytest -q'

echo
echo "=== a bare cd must not leak into a root-relative path ==="
assert_flagged "cd backend then a repo-root path"        'cd backend && bash scripts/tests/x.sh' 'subshell'
assert_flagged "cd frontend then docs/"                  'cd frontend && cat docs/TASKS.md'      'subshell'

echo
echo "=== legitimate strings stay clean (the regression that matters) ==="
# These are the shapes real tickets use. A lint that fires on them would
# make every ticket unclaimable, which is a worse failure than a missed
# neutering -- so they are asserted explicitly.
assert_clean "single leading cd, legs stay inside it"    'cd backend && uv run pytest -q && uv run ruff check src'
assert_clean "subshell-wrapped cd"                       '(cd backend && uv run pytest -q)'
assert_clean "two subshells joined"                      '(cd backend && uv run pytest -q) && (cd frontend && npm run -s build)'
assert_clean "plain root-relative script"                'bash scripts/tests/run_evidence_tests.sh'
assert_clean "test -f guard then a script"               'test -f .claude/evidence/T-22.json && bash scripts/tests/run_evidence_tests.sh'
assert_clean "cd .. mid-string, no root-relative leg"    'cd backend && uv run pytest -q && cd ../frontend && npm run -s build'

echo
echo "=== every verify string in the real plan is lint-clean ==="
# T-28's acceptance in one assertion: after its fixes, no ticket in
# docs/tickets.json carries a verify the lint rejects. This is the case that
# goes red if someone reintroduces a '|| true' into the plan.
DIRTY="$(CLAUDE_PROJECT_DIR="$REPO_ROOT" python3 - <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.environ["CLAUDE_PROJECT_DIR"], ".claude", "scripts"))
import harness_lib as H
bad = [t["id"] for t in H.load_tickets()["tickets"] if H.lint_verify(t["verify"])]
print(" ".join(bad))
PY
)"
if [ -z "$DIRTY" ]; then
  pass "no ticket in docs/tickets.json has a self-neutering verify"
else
  fail "no ticket in docs/tickets.json has a self-neutering verify" "still failing lint: $DIRTY"
fi

echo
TOTAL=$((PASSES + FAILURES))
if [ "$FAILURES" -eq 0 ]; then
  echo "SUMMARY: ${TOTAL}/${TOTAL} verify-lint tests passed."
  exit 0
else
  echo "SUMMARY: ${FAILURES}/${TOTAL} verify-lint test(s) FAILED."
  exit 1
fi
