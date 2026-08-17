#!/usr/bin/env bash
# build_zip.sh -- T-13's submission zip builder.
#
# Builds a fresh zip of the deliverable (source + docs) at
# submission/jarvis-submission.zip, from an explicit file list (not zip's
# own -x glob excludes, which are easy to get subtly wrong across nesting
# levels) so what goes in is easy to verify by eye or by grep.
#
# Excluded on purpose (per T-13's brief -- never ship these):
#   .env (secrets)            backend/.venv, frontend/node_modules (huge,
#   .git (not the deliverable)                       rebuildable dependency trees)
#   backend/var, ./var        (per-run telemetry, see .gitignore)
#   .claude/claims            (machine-local session state)
#   .claude/evidence-v1       26 receipts from the OLD .pass harness, superseded
#                             by .claude/evidence/*.json. Shipping both formats
#                             invites a reader to check the wrong one -- and the
#                             v1 copies attest to commits that no longer describe
#                             the tree. Excluded 2026-08-16.
#   heartbeat.jsonl           299 KB of watchdog liveness pings -- ~18% of the
#                             whole archive, and of no use to a reader. It is
#                             matched by name rather than by path because it is
#                             append-only machine output wherever it lands.
#   .claude/evidence          (regeneratable verify receipts)
#   .codegraph                (local dev-tool index, not source)
#   __pycache__, .pytest_cache, .ruff_cache, *.egg-info, frontend/dist,
#   .DS_Store, *.zip          (build/OS artifacts, and the output itself)
#
# .env.example is NOT excluded -- it has no real secrets and documents what
# a deployer needs to set.
#
# Usage: build_zip.sh [output_zip_path]
#   Defaults to submission/jarvis-submission.zip next to this script.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT="${1:-${SCRIPT_DIR}/jarvis-submission.zip}"

if ! command -v zip >/dev/null 2>&1; then
  echo "build_zip: FAILED -- 'zip' command not found on PATH" >&2
  exit 1
fi

cd "$REPO_ROOT" || {
  echo "build_zip: FAILED -- cannot cd to repo root ${REPO_ROOT}" >&2
  exit 1
}

rm -f "$OUT"

FILELIST="$(mktemp)"
trap 'rm -f "$FILELIST"' EXIT

find . \
  \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \
     -o -name .pytest_cache -o -name .ruff_cache -o -name '*.egg-info' \
     -o -name .codegraph \
     -o -path './backend/var' -o -path './var' \
     -o -path './.claude/claims' -o -path './.claude/evidence' \
     -o -path './.claude/monitor' -o -path './.claude/watchdog' \
     -o -path './.claude/evidence-v1' \
     -o -path './frontend/dist' \
  \) -prune \
  -o -type f \
     ! -name '.env' \
     ! -name '.DS_Store' \
     ! -name '*.zip' \
     ! -name 'heartbeat.jsonl' \
     ! -name 'NEEDS_HUMAN.md' \
     -print \
  | sed 's#^\./##' \
  > "$FILELIST"

FILE_COUNT="$(wc -l < "$FILELIST" | tr -d ' ')"
if [ -z "$FILE_COUNT" ] || [ "$FILE_COUNT" -eq 0 ]; then
  echo "build_zip: FAILED -- file list is empty, nothing to zip" >&2
  exit 1
fi

if ! zip -X -q "$OUT" -@ <"$FILELIST"; then
  echo "build_zip: FAILED -- zip command exited non-zero" >&2
  exit 1
fi

if [ ! -f "$OUT" ]; then
  echo "build_zip: FAILED -- zip reported success but ${OUT} does not exist" >&2
  exit 1
fi

SIZE_H="$(du -h "$OUT" 2>/dev/null | cut -f1)"
echo "build_zip: wrote ${OUT} (${FILE_COUNT} files, ${SIZE_H})"
