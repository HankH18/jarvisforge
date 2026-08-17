# Jarvis — Submission checklist (T-13)

Human-facing status and action list for closing out submission. Run
`bash scripts/verify_submission.sh` any time to re-check live state — it is
expected to (and currently does) exit non-zero until the items in §2 below
are done. That is correct behavior, not a bug in the script: several items
are genuinely outside what an agent can do (billing, sending email, a
missing PRD region, creating a remote).

## 1. Status, item by item

| Item | Status | Detail |
|---|---|---|
| `docs/TECHNICAL.md` (architecture, stack rationale, known limits, AI-usage note) | **DONE** | Written against the code as it exists now; see the file for full detail, including an honest limits section. |
| `scripts/verify_submission.sh` extended with the 4 acceptance-3 checks | **DONE** | Adds repo-private, video-present, TECHNICAL.md-present, zip-built to the existing T-22 evidence-freshness gate. Each check fails loudly with a reason when it can't be performed — none silently skip. |
| `submission/build_zip.sh` + a verified zip | **DONE (mechanism)** | Builds a fresh zip from an explicit file list (not a fragile glob), excludes `.env`, `.git`, `backend/.venv`, `frontend/node_modules`, `backend/var`, `.claude/claims`, `.claude/evidence`, `.codegraph`, caches, and `frontend/dist`. Currently 250 files / ~1.6 MB, includes `docs/TECHNICAL.md`. Rebuilds every time `verify_submission.sh` runs, so it always reflects current repo state — including whatever ends up in `submission/video/` (see below). |
| Acceptance 1 — video content checklist | **PLACEHOLDER, BLOCKED** | `docs/SPEC.md` §Open questions item 1: the required contents of the 5–10 min video and the Attachments section were cut off in the provided PRD screenshots. No checklist has been invented to fill this gap — that would defeat the point of a human-approved checklist. **Needs**: the missing PRD screenshot region from Hank. |
| Demo video file | **UNBLOCKED 2026-08-15 — nothing prevents recording it now** | ~~Cartesia TTS returning HTTP 402~~ — **stale, and this row was the last thing saying otherwise.** Cartesia was upgraded on 2026-08-15 and the deployed assistant demonstrably speaks: 8/8 driven turns completed against Fly app `jarvisforge` v8 in `iad`, with real synthesized audio and worker-side latency records (`docs/latency-report.md`). TTS time-to-first-byte measured from inside the container: `sonic-3` median 162 ms. So a demo video of a working voice assistant is now possible; `submission/video/` is simply still empty. **Needs**: Hank to record it. Only the *content checklist* above remains genuinely blocked, on the missing PRD region — the recording itself is not. |
| Repo-private confirmation | **BLOCKED — no remote exists** | `git remote -v` is empty. The submission target is a **self-hosted GitLab at `labs.gauntletai.com`** (user `hankholcomb`), not github.com. **Needs**: a remote pushed/created. |
| Zip emailed to both addresses | **BLOCKED — human action** | Sending email is explicitly the human's call; no agent sent anything, will send anything, or has been authorized to. **Needs**: Hank sends `submission/jarvis-submission.zip` to `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`. Sent-confirmation placeholder is in §3 below — leave it blank until it actually happens. |
| Evidence-freshness gate (T-22, wraps `check_evidence_staleness.sh`) | **FAILING today — 11 receipts UNVERIFIABLE** | `T-0, T-1, T-2, T-3, T-4, T-5, T-6, T-7, T-8, T-14, T-23` predate T-22's content fingerprinting, so this gate cannot certify them as current — an `UNVERIFIABLE` receipt fails the gate exactly like a `STALE` one does (T-23's own precedent: an unverifiable check cannot report green). **Needs**: re-run each of those 11 tickets' own `verify` command (see `docs/tickets.json`) to mint a fresh, fingerprinted `.pass` receipt. This script does not do that itself — re-running another ticket's verify is out of its scope, by design. |

## 2. Action list (do these in order)

1. **Top up / restore Cartesia TTS credits.** The deployed app cannot speak
   until this is fixed — nothing else in this checklist can produce a real
   voice demo without it. Confirm the fix by driving one live turn against
   the deployed worker and checking it actually gets audio out (e.g.
   `scripts/drive_turns.py` against a single utterance, or just opening the
   deployed SPA and talking to it).
2. **Supply the missing PRD screenshot region** (`docs/SPEC.md` §Open
   questions item 1) — the required video content checklist and the
   Attachments section. Once that's in hand, acceptance 1's placeholder can
   be replaced with a real, human-approved checklist (a plan change, so per
   `CLAUDE.md` rule 1 that's a follow-up to hand to the build process, not
   something to improvise here).
3. **Create/point the GitLab remote** (push-to-create makes the project
   private automatically):
   ```
   git push https://labs.gauntletai.com/hankholcomb/<repo>.git main
   ```
   The PAT is already in the macOS keychain (`osxkeychain` helper, host
   `labs.gauntletai.com`, user `hankholcomb`) — this should not need a
   pasted token. After pushing, re-run `scripts/verify_submission.sh`; its
   repo-private check will call the GitLab API (read-only) to confirm
   `visibility: private`. If that check fails with an HTTP 401, the saved
   token is write-only (no `api`/`read_api` scope) — that's a token-scope
   issue, not a broken push; confirm visibility manually in the GitLab UI
   in that case (Settings → General → Visibility).
4. **Record the 5–10 min demo video**, including GitHub Q&A against the
   chosen active mid-size OSS repo, once steps 1 and 2 are done. Save it to
   `submission/video/<name>.mp4` (or `.mov`/`.mkv`/`.webm`/`.avi`) — the
   verify script looks there.
5. **Re-run `bash scripts/verify_submission.sh`.** With steps 1, 3, and 4
   done, checks [2/5]–[5/5] should all pass.
6. **Re-run the 11 UNVERIFIABLE tickets' verify commands** (`T-0, T-1, T-2,
   T-3, T-4, T-5, T-6, T-7, T-8, T-14, T-23` — exact commands in
   `docs/tickets.json`) so each mints a fresh fingerprinted receipt. This
   closes check [1/5]. None of this repo's code needs to change for this —
   it's purely re-running existing verify commands to re-certify evidence
   that the fingerprinting mechanism (T-22) simply didn't exist yet to
   capture originally.
7. **Rebuild the zip one last time right before sending**
   (`bash submission/build_zip.sh`, or just re-run
   `verify_submission.sh`, which rebuilds it as part of check [5/5]) so it
   captures the final state, including the video dropped into
   `submission/video/` in step 4.
8. **Send `submission/jarvis-submission.zip`** to both
   `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`, with
   `docs/TECHNICAL.md` (already inside the zip) called out in the email
   body. Then fill in §3 below.

**Operational note:** `submission/jarvis-submission.zip` is a rebuilt
artifact (not covered by any `.gitignore` entry — the ticket that built
this checklist had no scope to add one). Whether to commit it is a call for
whoever owns repo hygiene next; it does not need to be tracked in git to be
emailed.

## 3. Sent confirmation

**PLACEHOLDER — NOT YET SENT.** Do not consider T-13 acceptance 4 met until
Hank has actually sent the email and filled this in himself.

- Sent by: _______
- Sent at (UTC): _______
- To: wduffy@frontieraudio.com — confirmed sent: [ ]
- To: pmoeckel@frontieraudio.com — confirmed sent: [ ]
- Zip file sent (path/size): _______
- Zip sha256 (`shasum -a 256 submission/jarvis-submission.zip`): _______

## 4. Not blocking T-13, but worth knowing before calling the whole project done

- **R10 (agent-PR) token is broader than its own acceptance #4 wants.** The
  `GITHUB_TOKEN` used for the one live recorded demo run is a classic PAT
  with `repo` scope — genuinely capable of merge/force-push across every
  repo its owner can push to, not just the intended test repo. No code
  change can fix this; only a fine-grained, single-repo token (exact spec
  in `docs/agent-pr-demo.md` §"Token-scope finding") closes it. Not part of
  T-13's acceptance criteria, but worth fixing before this is called a
  finished bonus.
- **R11 (wake word) is implemented and wired in as the default, but
  detection has not been confirmed by a human.** Picovoice discontinued
  free-tier Porcupine AccessKeys, so the default engine is now openWakeWord's
  keyless "hey Jarvis" model running on-device via `onnxruntime-web` — no
  key, no signup, no env var, loading lazily only once passive mode is
  switched on. The 134 frontend tests (including
  `openWakeWordDetector.test.ts`) prove the orchestration around it, not
  that real speech triggers it: no one has yet said "hey Jarvis" at a
  deployed build. Worth doing once before the demo video, since the video
  content checklist (item above) may end up wanting to show it. Also note
  the three vendored model weight files are CC BY-NC-SA 4.0
  (NonCommercial), not the same license as the rest of the stack — see
  `docs/TECHNICAL.md` §5 and `frontend/public/wake-models/NOTICE.md`.
