#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): deny paths outside the active ticket's scope globs.
#
# COVERAGE NOTE (T-20): this hook is wired to the Edit and Write tools only.
# It does not intercept Bash-mediated writes (sed -i, heredocs, cp, mv, git
# checkout, etc). That gap is deliberate and disclosed, not silent -- see
# CLAUDE.md "Scope guard coverage" for why a heuristic Bash-command guard
# was rejected and what the compensating discipline is.
set -uo pipefail
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR%/}"

# Acceptance 3: a path outside the project entirely (scratch/tmp dirs, a
# sub-agent's own worktree, etc) is not this guard's business -- it
# constrains work INSIDE the repo, it does not own the filesystem. Only
# normalise+scope-check paths that actually live under the project dir.
case "$FILE" in
  "$PROJECT_DIR"/*) REL="${FILE#"$PROJECT_DIR"/}" ;;
  "$PROJECT_DIR") REL="" ;;
  /*) exit 0 ;;                      # absolute path outside the project dir: allow
  *) REL="$FILE" ;;                  # defensive: bare relative path, treat as project-relative
esac

AT="${PROJECT_DIR}/.claude/active-ticket"
[ -f "$AT" ] || exit 0            # no active ticket claimed; don't block setup work
TID=$(head -n1 "$AT" | tr -d '[:space:]')

# Narrow, mechanism-specific exemptions ONLY -- not a blanket .claude/* or
# docs/* pass (acceptance 2). These two exact paths are how the protocol
# itself claims a ticket and records a passing verify; no ticket's scope
# lists them, so without this they'd be permanently unwritable once any
# ticket is active. Everything else under .claude/ and docs/ -- including
# docs/tickets.json, docs/SPEC.md, docs/DESIGN.md, and scope_guard.sh
# itself -- is scope-checked like any other file.
case "$REL" in
  .claude/active-ticket) exit 0 ;;
  .claude/evidence/*.pass) exit 0 ;;
esac

# Acceptance 4: anchor at BOTH ends. The old pattern only appended "$", so
# jq's test() (a search, not a full match) let any path merely CONTAINING
# a scope prefix through, e.g. "vendor/backend/src/jarvis/cadence/x.py"
# satisfied "backend/src/jarvis/cadence/**".
MATCH=$(jq -r --arg id "$TID" --arg f "$REL" '
  .tickets[] | select(.id==$id) | .scope[] as $g
  | select(($f | test("^" + ($g | gsub("\\.";"\\.") | gsub("\\*\\*";".∞") | gsub("\\*";"[^/]*") | gsub("∞";"*")) + "$"))) | "yes"' \
  "${PROJECT_DIR}/docs/tickets.json" | head -n1)
if [ "$MATCH" = "yes" ]; then exit 0; fi
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$REL is outside ticket $TID scope. If genuinely required, this is a plan defect — stop and ask the human."}}
EOF
exit 0
