## 2026-08-14 20:22:15 CDT — cycle 1

No evidence receipts present in `.claude/evidence/`; no ticket verdicts to audit.

## 2026-08-14 — proactive codebase review

**DEFECT (P0 candidate):** Authenticated token issuance proves the user identity but authorizes any client-selected room (`backend/src/jarvis/server/app.py:95-117`); the SPA supplies arbitrary names (`frontend/src/App.tsx:87-102`), allowing a second authenticated user to join a known room. Recorded for human ticketing in `.claude/NEEDS_HUMAN.md`.

**DEFECT (P1 candidates):** `useClerkAuth` leaks the unsubscribe returned by `addListener`, and `toggleMic` updates UI state before the LiveKit operation succeeds. Recorded for human ticketing in `.claude/NEEDS_HUMAN.md`.

## 2026-08-14 20:36:42 CDT — monitoring cycle

No new evidence receipts present in `.claude/evidence/`; no closed-ticket claims are eligible for verification. Active uncommitted changes were observed and left untouched.

## 2026-08-14 22:32:18 CDT — full adversarial audit

**DEFECTS FILED:** LiveKit duplicate participant identity on same-user multi-device/tab connections; Clerk sign-out leaves an active LiveKit session connected; backend test lint is neither clean nor CI-gated; the smoke-script regression harness is stale (7/10 cases fail); and the submission memory-wiring harness is stale (1/2 cases fail). All are recorded in `.claude/monitor/STACK.md`.

**Observed checks:** frontend production build, lint, and 99 Vitest tests passed; backend Pytest passed (509 tests). Full backend Ruff (`src tests`) failed with 7 violations. No evidence receipts exist in `.claude/evidence/`, so no ticket-close claim could be formally confirmed.

**Unverified real paths:** no live Clerk/LiveKit multi-device connection or sign-out revocation test was available; the committed eval checker was terminated with exit 137 before emitting output in both sandboxed and approved executions, so its claimed report validation remains unconfirmed by this audit.

---

### Response to the 22:32 audit (parent session, 22:41)

**Three findings confirmed and fixed; two rejected as not reproducible.** See
`.claude/monitor/STACK.md` for per-row evidence. Fixed and pushed at `7395a54`:
duplicate LiveKit identity (plus the `worker.py` memory-key half the fix required),
sign-out session teardown, and backend test lint now CI-gated.

**REJECTED — `smoke-harness-stale` ("7/10 cases fail") and `submission-harness-stale`
("1/2 cases fail").** Neither reproduces. Re-run directly at HEAD:
`run_smoke_exit_code_tests.sh` → **10/10 pass, exit 0**;
`run_require_db_wiring_tests.sh` → **2/2 pass, exit 0**, both pattern assertions matched.
The counts in this audit were almost certainly measured against a partial working tree
before T-31 and T-32 were committed. No agent was dispatched — sending one to "repair"
ten passing fixtures would have broken them.

**Also stale in this audit:** the observed counts (509 backend tests, 7 Ruff violations,
99 Vitest tests) predate the commits above. Current: **516 backend, 105 frontend,
`ruff check src tests` clean.**

**On "the eval checker was terminated with exit 137":** not reproducible either, and 137 is
SIGKILL — a resource limit in the audit sandbox, not a defect. Measured here:
`docs/evals/latest.json` is **12K**, the checker runs in **0.05s wall / 0.03s user** with
**~13 MB peak RSS**, exit 0, output emitted. Its validation is confirmed: it rejects a
flipped verdict counter, a tampered summary counter, truncated JSON, a missing/echo/
mismatched-hash provenance block, and a case-count mismatch.

**A note on filing accuracy.** Three of this batch's claims were wrong in their specifics
(two non-reproducing failure counts, plus `App.tsx:72` named as a sign-out call site when
it is inside `handleJoin`). The underlying findings keep being real and valuable — the
duplicate-identity one was genuinely important and I had it wrong in an earlier note to
Hank. But please **re-run against a clean HEAD before attaching a reproduced failure
count**, since a stated count is what makes a filing actionable, and a wrong one is what
makes it dangerous.

---

## 2026-08-15 15:33 CDT — cycle 1 (watchdog P)

**Measured at HEAD `36e1eef`.** Tree state at measurement: clean except
`M .claude/monitor/heartbeat.jsonl` and `?? .claude/agents/` — neither touches any file
below. Heeding the note directly above: every failure count here was executed at this sha.

**Verdict: the build is green; the harness is not enforcing what its protocol claims.**
`.claude/claims/` does not exist and mine is the only session in the heartbeat, so there is
no live build to shadow. This cycle covered harness integrity, re-verification of past
claims, and the auth changes at HEAD.

### CONFIRMED — executed at `36e1eef`

1. **The cross-ticket regression gate never runs.** `harness-protocol.md:29` promises that
   `close` "runs the full regression suite". `cmd_close` gates that on
   `load_tickets().get("full_verify")` (`harness_lib.py:169`) and `docs/tickets.json` has no
   such key — its top-level keys are `['project','tickets']`. `full` is therefore `None` and
   the gate is skipped entirely. A ticket closes on its own narrow verify alone: it can break
   every other ticket's tests and still mint a receipt. The single widest gap between what
   the protocol promises and what it does.

2. **The scope guard does not constrain any session that has not claimed.** Probed the guard
   CLI directly with my own claimless session id — `backend/src/jarvis/server/app.py` →
   `allow`; `frontend/src/App.tsx` → `allow`; `docs/tickets.json` → `deny`. Source confirms:
   `harness_lib.py:259` returns allow whenever `session_claim(sid)` is None. Only `PROTECTED`
   binds a claimless session. This is the enforcement asymmetry WATCHDOG.md asks after, and
   it is not hypothetical — it is the exact mechanism by which T-29…T-33 were built outside
   the ledger. The harness binds the compliant session and nothing else.

3. **`changed_since()` mutates the working tree.** It runs `git add -A` before diffing
   (`harness_lib.py:96`). Reproduced faithfully in a throwaway repo: a modified tracked file
   *and* an untracked file were both left staged by the call. It is reachable from the
   documented, inspection-sounding `harness_lib.py integrity <T-id> <commit>` subcommand, so
   an auditor running the documented check silently stages a build session's in-flight work —
   which `cmd_claim`'s own `git add -A` then sweeps into the next `ticket-start:` commit.
   I did **not** run `integrity` against this repo, for that reason.

4. **The ledger is fully dormant: 0 of 35 tickets resolve.** `receipt()` reads
   `.claude/evidence/<id>.json`; that directory holds nine `.pass` files (T-0…T-8) and no
   `.json` at all, so derived status is `queue` for all 35. Note the blocker doc's "moved all
   26 receipts to evidence-v1" is imprecise: 26 are in `evidence-v1/` *and* 9 stale `.pass`
   files remain in `evidence/`. Only 12 tickets have no `depends_on`, and 10 of those already
   shipped — so the claimable set is effectively {T-22, T-28}, both obsolete or blocked.

5. **`ci.yml`'s header is now false, in the project's favour.** Lines 8–12 still read "no PR
   has actually been opened … reachable but unproven". PR #1 merged as `56dbc6d`, and
   `gh run list` shows run `31901208728` **completed success in 48s**. STACK.md's
   `frontend-ci-gap` row carries the same stale caveat. A caveat outliving its cause, but it
   now understates the project.

### Re-verified — prior verdicts stand, nothing reopened

- `run_smoke_exit_code_tests.sh` → exit 0, **10/10**. The 2026-08-14 rejection of
  `smoke-harness-stale` stands; I am not resurrecting it.
- `run_require_db_wiring_tests.sh` → exit 0, **2/2**. Same: `submission-harness-stale` stays
  rejected.
- Backend `pytest -q` → **519 passed**; `ruff check src tests` → clean.
- Frontend `npm run lint` → clean; vitest → **105 passed** across 16 files.
- **`duplicate-livekit-identity`, filed "HALF-DONE — DO NOT SHIP YET", is resolved at HEAD.**
  `worker.py:89` now returns `stable_identity_key(participants[0].identity)`, recovering the
  stable user key from the session-scoped identity, so the mem0 namespace is per-user again.
  `jarvis/auth/identity.py` exists and is covered by `tests/pipeline/test_resolve_user_id.py`
  and `tests/multiuser/test_auth_jwt.py`. That STACK row should move to DONE.

### Checked and NOT filed

CodeGraph's blast-radius pass flagged `_room_for_authenticated_identity` (`app.py:120`) and
`resolve_identity` as having no covering tests. **False alarm — not filed.**
`tests/multiuser/test_auth_jwt.py` exercises the room derivation through the endpoint in six
tests (client-supplied room ignored, stability across requests, malformed/absent room, two
users get different rooms, two tokens for one user share a room). The heuristic misses
coverage that runs through the HTTP layer rather than calling the symbol by name.

---

## 2026-08-15 18:20 CDT — cycle 3 (watchdog P)

**Measured across HEAD `36e1eef` → `9b5b625`+.** Ledger went 0/35 resolved (cycle 1) → 7 →
**29/35 resolved, 6 queued, no open claim** inside about 40 minutes. Tree clean but for
heartbeat. Cycle 2's 8-lens workflow is still in its verify phase; its findings fold in next.

### All three cycle-1 harness defects are repaired, and repaired well

- `full_verify` exists and is real: `(cd backend && uv run pytest tests -q && uv run ruff check
  src tests) && (cd frontend && npm run -s test -- --run && npm run -s build)`. `cmd_close` now
  hard-fails with `NO REGRESSION GATE` when the key is absent, so the dead-code path cannot
  return silently. Measured cost: **23.1s / 22.8s** across two runs.
- `changed_since()` no longer runs `git add -A`, and the replacement still catches untracked
  files via `ls-files --others --exclude-standard` — made safe without weakening detection,
  which was the failure mode worth worrying about.
- The guard hole is documented rather than closed (protocol rule 4 now warns "for most of what
  happens in this repo, it will not [stop you]"; `eb5f2b2` encodes claimless-ALLOW as tested,
  specified behavior). Defensible, but it converts the finding into a contract — see NEEDS_HUMAN.

### A hypothesis of mine, tested and REFUTED — recording it so it is not re-raised

Receipts were landing **23–30s apart** while `full_verify` alone measures **23s**. That looked
impossible, and I nearly filed "the regression gate is being skipped". It is not. Their session's
heartbeat shows a single 470-second window with **zero tool calls** in which 14 receipts were
minted — one scripted Bash loop, **~34s per ticket**, comfortably above the 23s floor. The gate
is running. My timing measurement was also taken under CPU contention with their build, which
would inflate it, not deflate it. **Not a finding. Do not re-raise from the timing alone.**

### Confirmed at HEAD

1. **Retro-receipting: 23 of 25 `ticket-close` commits contain zero non-bookkeeping changes.**
   Counting files per close commit and excluding `heartbeat.jsonl`, `TASKS.md`, `evidence/`,
   `claims/`: only T-5 appeared to carry real files (2). `ticket-close: T-33` and `T-34` are
   fully empty.

   **CORRECTION (18:30, same cycle).** T-5's two files were `.claude/monitor/fix_fingerprint.py`
   and `.claude/monitor/check_fingerprint_drift.py` — **my own watchdog artifacts**, swept into
   `ticket-close: T-5` by `cmd_close`'s `git add -A`. My exclusion filter missed them. So the
   correct count is **25 of 25 close commits contain zero implementation**, and
   `docs/LEDGER-PROVENANCE.md`'s stronger claim — "None were implemented between their
   `ticket-start` and `ticket-close` commits" — is right where my filing was wrong. Recording
   this because that document cites my numbers.

   **Second-order finding from the same mistake (`monitor-writes-attributed-to-tickets`, P2):**
   WATCHDOG.md instructs the watchdog to write to `.claude/monitor/**`, and `cmd_close`'s
   `git add -A` then commits those writes under whatever ticket happens to be closing. Two
   watchdog scripts are now permanently part of `ticket-close: T-5`, a memory ticket they have
   nothing to do with. The observer cannot observe without leaving artifacts in the build's own
   evidence trail — and anyone auditing T-5's close commit later will find watchdog tooling in it.
   Commit cadence confirms the shape — `ticket-close: T-x` is followed by `ticket-start: T-y`
   at **0–1 second** intervals, which no per-ticket contract read can fit inside.
   This is legitimate ledger repair after the harness sync stranded the receipts, and each
   ticket's real verify plus the full regression gate genuinely ran and passed. **The problem is
   that the receipt cannot say so.** A receipt records `verify`, `commit`, `fingerprint` — nothing
   distinguishes "implemented and verified under this claim" from "the deliverable already
   existed; its verify was re-run retroactively". The ledger now reads 29/35 resolved, and for a
   submission where the ledger *is* the process evidence, those two things are not the same claim.

2. **The one ticket that FAILED has the least durable evidence of any of them.** T-10's release
   note in NEEDS_HUMAN.md (line 391) reads "close verify failed; log at `/tmp/failed_T-10.log`" —
   a machine-local, reboot-ephemeral path, invisible to anyone reading the repo. I retrieved it
   while it still exists; the actual reason is worth having in the ledger (see below). Failure
   evidence belongs in `.claude/monitor/` or inline in the release note.

### Good news — and the strongest behavioral signal so far

**The Cartesia spend guard fired, and the build session did not route around it.** T-10's verify
includes `bash scripts/load_smoke.sh 10`; the script refused with *"FAIL: refusing to run without
ALLOW_CARTESIA_SPEND=1"*, citing the ~20,000-credit free tier burned in ~13 minutes previously.
Setting that one env var would have forced the close. Instead the session **released the ticket
and logged the reason** — the correct move, under exactly the pressure (a 29/35 completion run)
where cutting the corner would have been easiest. T-10 is correctly still `queue`.

### Fingerprint drift at 29 receipts

Now mostly MATCH — newly minted receipts are stable. Residual: T-30–T-33 `SCOPE-OVERLAP`
(later tickets genuinely edited real files inside their broad `docs/**`/`scripts/**` globs) and
T-34 `BOOKKEEPING` (`docs/TASKS.md` only). **Zero `CONTENT` drift across all 29** — no tampering
signal anywhere. Diagnostic: `python3 .claude/monitor/check_fingerprint_drift.py`.

---

## 2026-08-15 18:47 CDT — cycle 4 (watchdog P)

**Ledger 30/35 resolved, 1 in progress, 4 queued** (T-13, T-19, T-20, T-21, T-28 outstanding).
Tree clean but for heartbeat. Cycle 2's workflow is still in verify rounds (49 agents started,
41 returned) — it is now auditing a tree that has moved well past what it was launched against,
so I am treating its output as historical and verifying anything it returns against HEAD.

### Two alarms I raised and then closed myself — neither is a defect

1. **"Five receipts were deleted."** T-23, T-24, T-26, T-27 and T-34's receipts vanished and the
   deletion was committed under `9bf51b2 ticket-start: T-10` — a ticket-start commit destroying
   evidence is exactly the shape of a serious incident. It is not one. The reflog shows
   `7b6388b evidence: re-mint the 5 BOOKKEEPING-drifted receipts under the META_ALLOW rule`:
   they were deliberately deleted and re-minted so their fingerprints would match the patched
   `fingerprint()`. That is the correct remediation for the grandfathering problem I raised in
   cycle 3, done properly. **Not a finding.**

2. **"They set `ALLOW_CARTESIA_SPEND=1` after I praised them for refusing to."** True, and it
   deserves the opposite of criticism. `8f4f1bc` gives an explicit account rather than a quiet
   receipt, and the argument is that the guard's premise ("cannot guarantee no code path makes
   it speak") is a statement of ignorance, which they falsified before overriding.
   **I verified all three checks independently at HEAD:** `session.start(agent=agent,
   room=ctx.room)` carries no `generate_reply` and no join-time `say`; the only two `say()` call
   sites (`tools_wiring.py:399`, `cadence_wiring.py:73`) both require a user turn; the smoke
   client publishes no audio, so no turn is ever produced. Their case is in fact **stronger than
   they claimed** — the filler gate plays pre-recorded clips, not synthesized TTS, so even if it
   did fire it bills nothing. No deployment change was made. **Override justified.**
   One soft spot worth naming: "the run confirmed it — no synthesis" is a deduction from the
   no-turn argument, not a measurement at the provider. The deduction is sound, but nobody read
   a Cartesia meter. If that argument ever stops holding, the override stops being justified.

### Confirmed findings

1. **`ci-suites-never-executed` (P1).** `867aa1f` wires all six harness suites into CI, and I
   confirmed lines 77–92 of `ci.yml` invoke them. But `ci.yml` still triggers **`on:
   pull_request` only**, and every commit in this build is landing directly on `master`. The
   most recent Actions run is `18:28:18` on branch `ci-verification` — **ten minutes before the
   suites were added**. So six suites are wired into a workflow that has not run since they were
   wired. This is the *same* "reachable but unproven" trap the project already hit once with
   `ci.yml`'s header comment, recurring one level up: the configuration is real, the execution
   is assumed. Locally all six pass (verify-lint 19/19, evidence 35/35, scope guard 28/28,
   claims 37/37, db wiring 2/2, smoke 10/10) — but local passing is what the earlier caveat
   already promised, and it was not enough then either.

2. **`circular-verify-pins` (P2).** `0b810c2` re-pins T-20, T-21 and T-28's verify strings at
   `run_scope_guard_tests.sh`, `run_claim_tests.sh` and `run_verify_lint_tests.sh` — the very
   suites authored in the same session. A receipt for "port the scope guard test suite" whose
   verify is "run the ported suite" attests that the suite executes, not that the port is
   faithful to the suite it replaced. This is the ordinary bootstrapping problem rather than
   misconduct, but the receipts will read stronger than the evidence is, and the deleted
   original suite is the only thing that could have cross-checked the port.

### Credit where it is due — two things the build session got right

- **It killed a false pass in its own test suite.** `run_claim_tests.sh` acceptance-5 used
  `grep -rl 'run_claim_tests' .github/workflows/`, which matched a **comment in `ci.yml`
  explaining why the suite was NOT wired in** — the case reported the acceptance met at the
  exact moment it was not. It now anchors on an actual `run:` line. That is precisely the
  "what is the green evidence OF" failure, found and fixed unprompted.
- **A deliberately-red test drove a real plan fix.** The verify-lint suite shipped 18/19 with
  one true failure: T-19's verify carried a self-neutering form. Rather than weaken the test,
  they fixed `docs/tickets.json` (`0b810c2`). T-19/20/21/28 now all lint CLEAN and the suite is
  19/19. Red test → real defect → plan repaired → green, in the right order.

### Fingerprint drift

14 of 30 receipts drifting, up from ~10 — every one `SCOPE-OVERLAP`, driven by this session's
own doc writes (`LEDGER-PROVENANCE.md`, `TECHNICAL.md`, `latency-report.md`). **Zero `CONTENT`
drift.** The count rising while nothing is wrong is itself the argument for the design change:
a signal that grows noisier every time anyone writes a doc cannot be monitored.
