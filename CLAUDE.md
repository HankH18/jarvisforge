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
   This rule binds regardless of mechanism: the hook only technically
   enforces it for the Edit and Write tools (see "Scope guard
   coverage" below) — it is still a protocol violation to route a
   scope-violating write through Bash (`sed -i`, a heredoc redirect,
   `cp`, `mv`, `git checkout -- <path>`, etc.) just because the hook
   doesn't see it.
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

## Scope guard coverage (T-20)

`.claude/hooks/scope_guard.sh` runs as a `PreToolUse` hook matched to
`Edit|Write` only (`.claude/settings.json`). It does **not** run for
`Bash`, so a write performed through a shell command (`sed -i`, a
heredoc redirect, `cp`, `mv`, `git checkout -- <path>`, `python -c
"open(...).write(...)"`, etc.) is not technically intercepted.

This is a deliberate, disclosed gap, not an oversight: sound
detection of "did this arbitrary shell command write outside scope"
requires actually parsing the command the way a shell does — quoting,
command substitution, pipes, heredocs, `eval`, aliases, and encoding
tricks all defeat a regex/token heuristic. A heuristic good enough to
catch the obvious cases is also good enough to false-positive on
innocuous ones (a `>` inside a quoted `git commit -m "..."` message,
a `cp` in a comment, a `sed` invocation with no real target) — and
this repo's own protocol treats that failure mode as strictly worse
than the bypass it would guard against: a hook that blocks `git
commit`, `uv run pytest`, or `npm run build` stops every ticket, not
just a mis-scoped one. Given that trade, this gap is left open rather
than patched with a heuristic that provides false confidence while
remaining trivially defeatable by minimal obfuscation.

The compensating control is rule 4 above: staying in scope is a
protocol obligation independent of whether a hook enforces it for the
mechanism used. If a ticket's work seems to require a Bash-mediated
write outside its declared scope, that is the same plan defect as an
out-of-scope Edit — stop and tell the human, don't route around the
hook.

## Session start

If TaskList is empty for this project, follow docs/INGEST.md first.
Then: report which tickets are ready, claim exactly one, begin.
