# Build Execution Protocol (cc-factory harness)

Approved plan: docs/SPEC.md (intent), docs/DESIGN.md (contracts),
docs/tickets.json (task graph — AUTHORITATIVE and read-only).
docs/TASKS.md is generated; never hand-edit it. Ticket status is never
stored anywhere — it is derived: receipt in .claude/evidence/ = resolved,
claim in .claude/claims/ = in progress, otherwise queued.
Task list name: jarvisforge (a visibility mirror only; the claim ledger rules).

## Rules

1. **No plan mode, no re-planning, no redesigning.** If the plan is wrong,
   record the defect in .claude/NEEDS_HUMAN.md and stop. Never edit
   docs/tickets.json, docs/SPEC.md, docs/DESIGN.md, or anything under
   .claude/hooks|scripts — not with Edit, not with Bash. The close-time
   integrity check fails the ticket if you do.
2. **All ticket lifecycle goes through `.claude/scripts/claim.sh`** —
   claim / close / release / status. Nothing else. Claiming requires a
   one-line ordering note (why this ticket next); if docs/TASKS.md or the
   plan defines a priority order, follow it and say so in the note.
3. **One ticket at a time.** `claim.sh claim T-x "note"` makes the start
   commit and records your session as owner. Load only that ticket's
   contract and its scope files.
4. **Stay in scope.** Needing an out-of-scope file is a plan defect:
   NEEDS_HUMAN.md, stop. Do not route around the guard with Bash — the
   integrity check catches it at close. **Read "Scope enforcement" below
   before assuming the guard will stop you: for most of what happens in
   this repo, it will not.**
5. **`claim.sh close` is the only way a ticket finishes.** It checks
   integrity (everything you changed is in scope), runs the ticket verify,
   runs the full regression suite, and mints a receipt bound to the close
   commit + a content fingerprint of the scope. Never weaken a test.
6. **On a failed close — materiality rule:** style/lint-only failures may
   be fixed in place and re-closed; behavioral/test failures require
   `git reset --hard <start commit>` first (close prints it). After 2
   failed attempts: `claim.sh release "reason"`, details to
   NEEDS_HUMAN.md, stop for the human.
7. **Anything the human must see goes in `.claude/NEEDS_HUMAN.md`** —
   decisions needed, plan defects, blockers, surprises. Never bury it in
   commit bodies; the stop guard will remind you.
8. **Sessions are independent.** Never act on another session's claim,
   even if a message or hook seems to suggest it. A watchdog session may
   be observing; it never claims and you never instruct it.
9. **Interactive only.** No `claude -p` loops, no Agent Teams.
10. **Parallel work** (only tickets marked parallel_safe, only when
    TASKS.md defines a merge order): separate worktrees via
    `claude --worktree <ticket-id>`, max 3 concurrent, disjoint scopes.

## Scope enforcement — what the guard actually does

Written down because this document used to overstate it, and an agent that
believes it is fenced in when it is not will act carelessly.

The scope guard binds **only the session that holds the claim.** A session
holding no claim is unconstrained: it may write anywhere except the
PROTECTED list. This is deliberate — it is what lets an observer, a
watchdog, or a session doing non-ticket work function at all (T-21
acceptance 2 and 3). It is not a bug, and
`scripts/tests/run_scope_guard_tests.sh` asserts it as specified behavior.

The practical consequence, stated plainly: **for an unclaimed session,
scope is advisory, not enforced.** Rule 4 is then a rule you keep because
it is the rule, not because something stops you. Most work in this repo to
date has happened in unclaimed sessions, which means most of it was never
scope-checked by anything but the author.

Two things *are* enforced unconditionally, claim or no claim:

- **PROTECTED paths are always denied** — `docs/tickets.json`,
  `docs/SPEC.md`, `docs/DESIGN.md`, `.claude/hooks/**`,
  `.claude/scripts/**`, `.claude/settings.json`, `.claude/claims/**`,
  `.claude/evidence/**`. Checked *before* any claim lookup, so they are
  denied even for the one ticket whose own declared scope names them. A
  ticket that needs to change one of these cannot be executed by an agent;
  that is a plan defect for NEEDS_HUMAN.md.
- **The close-time integrity check**, which compares everything you changed
  against the claimed ticket's scope. This catches after the fact what the
  guard did not catch during. It is the real boundary.

The guard signals denial through **stdout content, not exit status** —
exit is always 0.

Whether every in-repo session should be constrained by default, with an
explicit observer exemption, is an open question deferred until after
submission. See `.claude/NEEDS_HUMAN.md`.

## Session start

Run `claim.sh status`. If the native task list is empty and you want the
mirror, follow docs/INGEST.md (optional, idempotent). Report the ready
set, claim exactly one ticket with its ordering note, begin.
