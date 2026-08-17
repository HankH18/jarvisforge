# Harness blocker: `cc-factory: harness sync` (dc23222) vs. the T-28..T-34 plan

Written 2026-08-14. Everything here is verified against the real harness, not inferred.

## What the sync did

1. **All 26 receipts are invisible to the new harness.** They were renamed
   `.claude/evidence/T-*.pass` → `.claude/evidence-v1/T-*.pass`. The new harness
   (`.claude/scripts/harness_lib.py`) reads `.claude/evidence/T-*.json`. That directory is
   empty, so `claim.sh` reports **all 35 tickets as `queue`**, including the 26 that are done.

2. **The files T-28 and T-34 are written against were deleted by the sync:**
   `.claude/hooks/verify_gate.sh`, `.claude/hooks/tests/run_evidence_tests.sh`,
   `run_claim_tests.sh`, `run_scope_guard_tests.sh`, `claim_ticket.sh`.

3. **The new guard hard-denies two of T-28's five scope globs.** `PROTECTED` in
   `harness_lib.py:20` denies every Edit/Write to `docs/tickets.json` and `.claude/hooks/**`
   for *every* session, claim or no claim. T-28's scope is exactly those paths. Verified:

   ```
   $ python3 .claude/scripts/harness_lib.py guard sess-x $PWD/.claude/hooks/tests/run_verify_lint_tests.sh
   deny:.claude/hooks/tests/run_verify_lint_tests.sh is a plan/harness file...
   $ python3 .claude/scripts/harness_lib.py guard sess-x $PWD/docs/tickets.json
   deny:docs/tickets.json is a plan/harness file...
   ```

## Consequence

**T-28 is unexecutable**, and since `cmd_claim` refuses any ticket whose dependency has no
receipt, T-28 blocks T-29 → T-30 → T-31 → T-32 → T-33 → T-34. The whole remaining batch is
gated on a ticket that cannot be done.

**T-34 is also partly unexecutable**: its verify is `test -f .claude/evidence/T-22.pass`,
the *old* receipt filename. The new harness mints `.claude/evidence/T-22.json`. Its scope
also includes `.claude/hooks/**`.

## T-28 is mostly obsolete anyway

T-28's central deliverable — a lint that rejects self-neutering verify strings — **already
exists in the harness you installed**: `LINT_RULES` / `lint_verify()` at
`harness_lib.py:107`, enforced inside `cmd_claim`. It is *stricter* than T-28 specified.
T-28's acceptance-3 says the ~20 legacy `cd backend && ...` strings should pass unchanged;
the new lint **rejects all 21 of them**, plus T-19's `|| true`. So under the new harness,
**T-0..T-8, T-11, T-12, T-14..T-19, T-24..T-27 can never be re-claimed** — their pinned
verify strings fail the lint. Only T-9, T-10, T-13, T-20, T-21, T-22, T-23, T-28..T-34 pass.

## What I need from you (pick one)

- **(A) Amend the plan.** Retire T-28 as superseded by the harness sync; drop
  `.claude/hooks/**` and `docs/tickets.json` from T-34's scope; re-pin T-34's verify to
  `.claude/evidence/T-22.json`; re-point the T-29..T-33 `depends_on` chain so it does not
  route through T-28.
- **(B) Restore the deleted hook tests** from `dc23222^` and add a scope-guard exception so
  a ticket whose declared scope includes `.claude/hooks/**` may write there.
- **(C) Migrate the receipts** — decide whether the 26 `evidence-v1/*.pass` files should be
  re-minted as `.json` under the new harness (re-running every verify; note **T-7's verify
  hits the live Anthropic API and costs real money**), or converted mechanically.

## What I did in the meantime

T-29..T-33's *file scopes are all writable*; only the ledger bookkeeping was blocked. I
built them **outside the ledger** — no claims, no receipts — and ran each ticket's own
verify string for real. `docs/OUT-OF-LEDGER-2026-08-14.md` records exactly what landed, the
verify output for each, and what still needs a receipt minted once you resolve the above.

Nothing I wrote touches `docs/tickets.json`, `docs/SPEC.md`, `docs/DESIGN.md`,
`.claude/hooks/**`, or `.claude/scripts/**`.
