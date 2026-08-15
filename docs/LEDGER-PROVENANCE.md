# What the ticket ledger actually attests

Written 2026-08-15, when the ledger went from 5 resolved to 29 of 35 in a single
afternoon. That jump is real work being recorded, not work being done — and a reader
who assumes otherwise will over-trust the number. This document says exactly what a
receipt in `.claude/evidence/` does and does not claim.

Raised by watchdog P (`AUDIT.md`, cycle 3, 18:20) as `retro-receipting-indistinguishable`.
The watchdog's measurement: **23 of 25 `ticket-close` commits contain zero
non-bookkeeping file changes**, and the gap between `ticket-close: T-x` and the next
`ticket-start: T-y` is 0–1 seconds. No implementation happened under those claims.

## What a receipt means

A receipt (`.claude/evidence/<T-id>.json`) asserts exactly this:

> At commit `<commit>`, this ticket's `verify` command ran and passed, the full
> cross-ticket regression suite ran and passed, and the content of the ticket's
> declared scope hashed to `<fingerprint>`.

That is a real, re-checkable claim, and every one of the 29 was earned: each close ran
the ticket's own verify plus `full_verify` (522 backend tests + `ruff check src tests`,
105 frontend tests + a production build), about 34 s per ticket, comfortably above the
23 s floor a genuine `full_verify` run takes. Nothing was rubber-stamped.

## What a receipt does NOT mean

It does **not** assert that the deliverable was implemented under that claim.

For most of these tickets the code shipped in an earlier session — in several cases
outside the ledger entirely, because the harness was unclaimable at the time (see
`docs/HARNESS-BLOCKER-2026-08-14.md`). The claim/close cycle re-ran the verification
against work that already existed and recorded the result. That is legitimate ledger
repair: it replaces "unknown" with "verified at this commit". It is not the same thing
as "built and verified under supervision", and the receipt schema has no field that
distinguishes them.

Concretely, of the 29:

- **None** were implemented between their `ticket-start` and `ticket-close` commits.
- **T-22 and T-34** had their deliverables built earlier the same session (the ported
  evidence suite, `.json` receipt support, and the MISSING classification), then claimed
  and closed. Closest to "built under claim", still not literally that.
- **T-29–T-33** were built out-of-ledger in a prior session and receipted afterward.
- **T-0–T-27** predate this session; their verifies were re-run retroactively.

## The gap, and why it is not patched here

The fix the watchdog suggests — a `retroactive: true` field, or a provenance note in the
receipt — belongs in `harness_lib.py`, which is PROTECTED. No agent can add it. Until
someone does, **this document is the record**, and the honest summary is:

> 29 of 35 tickets have a verified-at-a-commit receipt. Zero of 35 have a receipt
> proving the work was done under that claim.

## T-10: the one close that failed, and why

Recorded here because the release note pointed at `/tmp/failed_T-10.log`, a machine-local
path that vanishes on reboot and is invisible to anyone else reading the repo
(`failure-evidence-in-tmp`, watchdog P, cycle 3).

T-10's verify invokes `scripts/load_smoke.sh`, which **refused to run** and exited
non-zero:

```
FAIL: refusing to run without ALLOW_CARTESIA_SPEND=1 (see warning above).
```

This is the spend guard working exactly as designed, not a defect in T-10. The guard
exists because Cartesia bills by **seconds of generated audio** (~12 credits/second, not
per character), and a single 55-turn `drive_turns.py` run against the deployed worker
exhausted a 20,000-credit monthly free tier in about 13 minutes.

Two ways to close T-10, both requiring a human decision because both spend or change
deployed state:

1. **Free** — point the deployed worker at Deepgram for the duration, as
   `load_smoke.sh`'s own warning recommends:
   `flyctl secrets set TTS_PROVIDER=aura2 -a jarvisforge`, close T-10, then
   `flyctl secrets unset TTS_PROVIDER -a jarvisforge`.
2. **Paid** — run the close with `ALLOW_CARTESIA_SPEND=1` and accept the credit burn.

T-10 is the only ticket whose close has ever failed.
