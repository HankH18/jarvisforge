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
