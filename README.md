# Jarvis — Claude Code Handoff Package

Copy everything here into the root of the (private) jarvis repo.

```
CLAUDE.md                     execution protocol (enforced by hooks)
.claude/settings.json         hook wiring
.claude/hooks/*.sh            verify gate, scope guard, stop backstop
.claude/agents/verifier.md    optional cheap pre-close reviewer
docs/SPEC.md                  Tier 1 — intent (R1–R12, C1–C7)
docs/DESIGN.md                Tier 2 — pinned contracts
docs/TASKS.md                 Tier 3 — human-readable graph + merge order
docs/tickets.json             Tier 3 — AUTHORITATIVE (hooks read this)
docs/INGEST.md                first-session Tasks setup
```

## Launch checklist

1. Copy the package into the repo; `chmod +x .claude/hooks/*.sh`; ensure `jq` is installed.
2. Every session: `CLAUDE_CODE_TASK_LIST_ID=jarvis claude` from the repo root (persists the task graph across sessions/days).
3. First session: "Follow docs/INGEST.md." Verify the ready set matches the graph roots (only T-0).
4. Then: "Claim the next ready ticket and execute per CLAUDE.md." Repeat until TaskList is empty.
5. Do not set ANTHROPIC_API_KEY in this environment (avoids documented silent API-billing paths); run interactively, not via `claude -p`.

If Claude Code has shipped a new version since this package was generated
(Aug 2026), re-verify before first use: Task tool names + addBlockedBy
semantics, TaskCompleted blocking on exit 2, and headless billing status.
