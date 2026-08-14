#!/usr/bin/env bash
# verify_submission.sh -- T-13's submission checklist gate.
#
# Seeded by T-22 with exactly one check: no ticket's .claude/evidence/*.pass
# receipt may be stale (superseded by a later commit to that ticket's
# declared scope). Without this, T-13's checklist could report green while
# certifying work that no longer exists (docs/tickets.json T-22 objective).
#
# T-13 itself is not yet built. Its remaining acceptance items -- repo-
# private confirmation, demo video present, docs/TECHNICAL.md present,
# submission zip built -- are NOT implemented here. Whoever claims T-13
# extends this script with those checks; do not treat a pass here as a
# submission-ready signal on its own.
#
# Usage: verify_submission.sh [project_dir]
#   project_dir defaults to this repo. The optional argument exists so
#   .claude/hooks/tests/run_evidence_tests.sh can exercise this exact gate
#   against a throwaway sandbox instead of the real project.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_DIR="${1:-${DEFAULT_PROJECT_DIR}}"

echo "== T-13 submission verification =="
echo
echo "-- evidence freshness gate (T-22) --"
if ! "${SCRIPT_DIR}/check_evidence_staleness.sh" "${PROJECT_DIR}"; then
  echo >&2
  echo "verify_submission: FAILED -- stale ticket evidence exists; refresh it (re-run the affected ticket's verify) before submitting." >&2
  exit 1
fi

echo
echo "verify_submission: evidence freshness OK"
echo "verify_submission: NOTE -- remaining T-13 checklist items (repo-private, video, docs/TECHNICAL.md, submission zip) are not implemented by this script yet." >&2
