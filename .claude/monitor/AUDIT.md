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
