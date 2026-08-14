#!/usr/bin/env bash
# T-23's own ticket verify command is:
#   cd backend && uv run pytest tests/memory -q && bash scripts/tests/run_smoke_exit_code_tests.sh
# which, because `cd backend` runs first, resolves "scripts/tests/..." relative
# to backend/ -- not the repo root. The real implementation lives at the
# repo-root scripts/tests/run_smoke_exit_code_tests.sh (next to the
# scripts/smoke_deploy.sh it tests), so this is a thin relay to that single
# source of truth rather than a second copy that could drift out of sync.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${HERE}/../../../scripts/tests/run_smoke_exit_code_tests.sh" "$@"
