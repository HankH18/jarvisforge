# Build Execution Protocol

This repo has an approved build plan: docs/SPEC.md (intent),
docs/DESIGN.md (contracts), docs/tickets.json (the task graph —
authoritative). Your job is execution, not planning.

## Rules

1. **Do not enter plan mode. Do not re-plan or redesign.** The task
   graph is the approved plan. If the plan is wrong (discovered
   reality), STOP and tell the human — do not push through and do not
   silently rewrite tickets.
2. **One ticket at a time.** Pick a ready Task (pending, unowned, no
   open blockers) via TaskList. Claim it: TaskUpdate owner + status
   in_progress, and write its ticket ID (e.g. T-4) as the only line
   of `.claude/active-ticket`.
3. **Load only what the ticket needs**: its contract (Task
   description) and the files in its scope. Do not read the whole
   graph or other tickets' contracts.
4. **Stay inside the ticket's file scope.** A hook denies edits
   outside it. If the ticket genuinely requires touching an
   out-of-scope file, that's a plan defect — stop and tell the human.
5. **A ticket is done only when its verify command exits 0.** A hook
   runs it when you mark the Task completed and blocks completion on
   failure. Never weaken a test to pass it.
6. **Commit at every ticket boundary**: one commit immediately after
   claiming (marks ticket start), one on completion, message
   "T-x: <title>". /rewind does not undo bash side effects — the
   start commit is the durable revert point.
7. **On failed verify: revert, don't iterate.** `git reset --hard` to
   the ticket-start commit, then retry from clean state. After 2
   failed attempts, stop, summarize both failures, and ask the human.
8. **Interactive only.** Do not suggest or script `claude -p` loops.
   (The product's T-11 feature may use the Agent SDK at runtime —
   that is product code, not build execution.)
9. **Parallel work**: only tickets marked parallel_safe, in worktrees
   via `claude --worktree <ticket-id>`, max 3 concurrent, and merge
   strictly in the order pinned in docs/TASKS.md §Sequencing.

## Project pointers

- Interfaces are pinned in docs/DESIGN.md §Interfaces — implement them
  exactly; changing a pinned contract is a plan defect (rule 1).
- Secrets live in env vars listed in .env.example. Never commit keys.
- Keep CI light (SPEC C7): do not add jobs, matrices, or e2e to
  .github/workflows/ — GitHub Actions cost is a hard constraint.
- Model IDs come from $CONVO_MODEL / $TOOL_MODEL only. Do not hardcode.
- T-13 has a placeholder acceptance item blocked on a missing PRD
  region (SPEC §Open questions) — surface it when T-13 is claimed.

## Session start

If TaskList is empty for this project, follow docs/INGEST.md first.
Then: report which tickets are ready, claim exactly one, begin.
