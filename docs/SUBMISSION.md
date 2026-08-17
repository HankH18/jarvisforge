# Jarvis — Submission checklist (T-13)

Human-facing status and action list for closing out submission. Run
`bash scripts/verify_submission.sh` any time to re-check live state — it is
expected to (and currently does) exit non-zero until the items in §2 below
are done. That is correct behavior, not a bug in the script: several items
are genuinely outside what an agent can do (sending email, a missing PRD
region, recording the demo video, and confirming GitLab visibility — the
saved token can push but has no API read scope; see §1 below).
**Updated 2026-08-16**: the remote now exists and Cartesia is fixed — both
used to be listed here as agent-unreachable blockers and no longer are.

## 1. Status, item by item

| Item | Status | Detail |
|---|---|---|
| `docs/TECHNICAL.md` (architecture, stack rationale, known limits, AI-usage note) | **DONE** | Written against the code as it exists now; see the file for full detail, including an honest limits section. |
| `scripts/verify_submission.sh` extended with the 4 acceptance-3 checks | **DONE** | Adds repo-private, video-present, TECHNICAL.md-present, zip-built to the existing T-22 evidence-freshness gate. Each check fails loudly with a reason when it can't be performed — none silently skip. **Update 2026-08-16**: T-31 (a later, unrelated ticket) added a 6th check, the memory suite — the script now reports `[1/6]`..`[6/6]`, not the `[1/5]`..`[5/5]` this row's own 4 checks would imply on their own. |
| `submission/build_zip.sh` + a verified zip | **DONE (mechanism)** | Builds a fresh zip from an explicit file list (not a fragile glob), excludes `.env`, `.git`, `backend/.venv`, `frontend/node_modules`, `backend/var`, `.claude/claims`, `.claude/evidence`, `.claude/evidence-v1` (the old `.pass`-era receipts, superseded by `.claude/evidence`), `heartbeat.jsonl` (watchdog liveness pings, no reader value), `.codegraph`, caches, and `frontend/dist`. **Rebuilt and re-measured 2026-08-16: 284 files / ~4.6 MB** (`unzip -l submission/jarvis-submission.zip`), includes `docs/TECHNICAL.md`. The `250 files / ~1.6 MB` this row previously quoted is stale — rebuild-derived numbers move as the repo grows; treat this as a snapshot, not a pinned fact, and re-run `unzip -l` yourself before trusting a number here. Rebuilds every time `verify_submission.sh` runs, so it always reflects current repo state — including whatever ends up in `submission/video/` (see below). |
| Acceptance 1 — video content checklist | **PLACEHOLDER, BLOCKED** | `docs/SPEC.md` §Open questions item 1: the required contents of the 5–10 min video and the Attachments section were cut off in the provided PRD screenshots. No checklist has been invented to fill this gap — that would defeat the point of a human-approved checklist. **Needs**: the missing PRD screenshot region from Hank. |
| Demo video file | **UNBLOCKED 2026-08-15 — nothing prevents recording it now** | ~~Cartesia TTS returning HTTP 402~~ — **stale, and this row was the last thing saying otherwise.** Cartesia was upgraded on 2026-08-15 and the deployed assistant demonstrably speaks. Two corroborating pieces of evidence, both in `docs/latency-report.md`, precisely as recorded there (an earlier pass of this row overstated this as "8/8 driven turns completed", which the report does not actually support — corrected 2026-08-16): (1) the deployed worker (Fly app `jarvisforge` v8, `iad`) produced real synthesized audio with worker-side `latency.jsonl` records for **6 of 8** driven turns (the other 2: one lookup turn excluded by definition, one that logged no record at all); (2) a separate **local** run completed a full **3/3**-turn live LiveKit conversation end-to-end with real speech in and real synthesized audio out (`docs/first-live-conversation-2026-08-15.json`). TTS time-to-first-byte measured from inside the container: `sonic-3` median 162 ms. So a demo video of a working voice assistant is now possible; `submission/video/` is simply still empty (confirmed empty 2026-08-16). **Needs**: Hank to record it. Only the *content checklist* above remains genuinely blocked, on the missing PRD region — the recording itself is not. |
| Repo-private confirmation | **BLOCKED — GitLab API returns HTTP 403 (write-only token), not "no remote"** | `git remote -v` now shows **two** remotes: `github` (`github.com/HankH18/jarvisforge`, personal backup only, not the submission target) and `gitlab` (`labs.gauntletai.com/hankholcomb/jarvisforge`, the real target) — both exist and `gitlab` has been pushed to. The "no remote exists" wording this row used to carry is stale as of whenever that remote was created; corrected 2026-08-16. The actual current blocker: `scripts/verify_submission.sh`'s repo-private check calls the GitLab API and gets **HTTP 403** for `GET /api/v4/projects/hankholcomb%2Fjarvisforge` (verified live 2026-08-16) — the saved PAT in the macOS keychain has `write_repository` (push works fine) but not `read_api`/`api` scope, so the visibility check itself cannot run; this is not evidence the repo is public, just that this script can't confirm either way. **Needs**: either mint/save a token with `read_api` scope, or confirm visibility manually in the GitLab UI (Settings → General → Visibility). |
| Zip emailed to both addresses | **BLOCKED — human action** | Sending email is explicitly the human's call; no agent sent anything, will send anything, or has been authorized to. **Needs**: Hank sends `submission/jarvis-submission.zip` to `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`. Sent-confirmation placeholder is in §3 below — leave it blank until it actually happens. |
| Evidence-freshness gate (T-22, wraps `check_evidence_staleness.sh`) | **FAILING today, but this row's older "11 UNVERIFIABLE" wording was completely obsolete — corrected 2026-08-16** | The `.pass`-era receipts this row used to describe are gone. Every ticket's receipt is now a fingerprinted `.json` (`.claude/evidence/*.json`, 34 of them; `ls .claude/evidence/ \| wc -l` → 34), and `claim.sh status` genuinely reports the ledger at **34/35 resolved**. Running `bash scripts/check_evidence_staleness.sh .` today reports **20 STALE + 1 MISSING (`T-13`)**, cross-checked against `python3 .claude/monitor/check_fingerprint_drift.py` — every one of the 20 is tagged `DRIFT: SCOPE-OVERLAP` (a *later* commit touched a file inside that ticket's declared scope glob, not a regression in that ticket's own deliverable; the drift script's own footer: "design decision: fingerprint changed files, not the whole scope glob"). This is **expected, recurring noise**, not a queue of 20 things to fix — many tickets declare broad `docs/**`/`frontend/**` scopes, so it re-triggers on essentially every commit that touches those areas (this very edit will likely add `docs/SUBMISSION.md` to a few more tickets' drift lists). `MISSING: T-13` is the other failure, and it is **by design**: T-34's acceptance requires a receipt-less ticket to fail this gate loudly rather than be silently exempted, and T-13 (this submission ticket) cannot mint its own receipt until the submission actually happens — closing it is the literal last step of §2 below. **Needs**: nothing actionable today beyond finishing §2 and eventually closing T-13; do not chase the SCOPE-OVERLAP list, it is not a defect queue. |

## 2. Action list (do these in order)

1. ~~Top up / restore Cartesia TTS credits.~~ **DONE 2026-08-15.** Cartesia
   was upgraded and the deployed app demonstrably speaks — see the "Demo
   video file" row in §1 above for the evidence. Nothing left to do in this
   step; kept here (struck through) rather than deleted, since this file is
   also a record.
2. **Supply the missing PRD screenshot region** (`docs/SPEC.md` §Open
   questions item 1) — the required video content checklist and the
   Attachments section. Once that's in hand, acceptance 1's placeholder can
   be replaced with a real, human-approved checklist (a plan change, so per
   `CLAUDE.md` rule 1 that's a follow-up to hand to the build process, not
   something to improvise here). **Still blocked as of 2026-08-16.**
3. ~~Create/point the GitLab remote~~ **DONE** — both a `github` (backup)
   and a `gitlab` (`labs.gauntletai.com/hankholcomb/jarvisforge`, the real
   target) remote now exist, and `gitlab` has been pushed to. What's left
   is narrower than "create a remote": `scripts/verify_submission.sh`'s
   repo-private check calls the GitLab API and gets **HTTP 403** (verified
   live 2026-08-16) — the saved token can push (`write_repository`) but has
   no `read_api`/`api` scope, so it cannot confirm `visibility: private`.
   Either mint/save a token with `read_api` scope and re-run, or confirm
   visibility manually in the GitLab UI (Settings → General → Visibility)
   and treat that as sufficient.
4. **Record the 5–10 min demo video**, including GitHub Q&A against the
   chosen active mid-size OSS repo, once step 2 is done (step 1, Cartesia,
   is no longer a blocker). Save it to `submission/video/<name>.mp4` (or
   `.mov`/`.mkv`/`.webm`/`.avi`) — the verify script looks there.
   `submission/video/` is confirmed still empty as of 2026-08-16.
5. **Re-run `bash scripts/verify_submission.sh`.** It now reports 6 checks,
   `[1/6]`–`[6/6]` (T-31 added a memory-suite check after this row was
   originally written). As of 2026-08-16, `[4/6]` (TECHNICAL.md),
   `[5/6]` (zip build), and `[6/6]` (memory suite) already pass on their
   own; `[1/6]` (evidence freshness), `[2/6]` (repo-private), and `[3/6]`
   (video present) are the three still failing, for the reasons in §1
   above. Steps 2–4 close `[2/6]` and `[3/6]`; `[1/6]` is expected to keep
   failing until `T-13` itself closes (see the evidence-freshness row in
   §1) — do not treat a nonzero exit here as proof something is still
   broken once 2–4 are done.
6. ~~Re-run the 11 UNVERIFIABLE tickets' verify commands~~ **OBSOLETE —
   there is no "11 UNVERIFIABLE" list anymore.** That described the old
   `.pass`-era gate, which no longer exists (see the evidence-freshness row
   in §1). All 34 non-`T-13` tickets already carry fresh, fingerprinted
   `.json` receipts. The gate's remaining `STALE` entries are
   `SCOPE-OVERLAP` drift — expected, recurring, and explicitly *not* a
   defect per the drift script's own output — not a queue of verify
   commands to re-run. Nothing to do here.
7. **Rebuild the zip one last time right before sending**
   (`bash submission/build_zip.sh`, or just re-run
   `verify_submission.sh`, which rebuilds it as part of check `[5/6]`) so it
   captures the final state, including the video dropped into
   `submission/video/` in step 4.
8. **Send `submission/jarvis-submission.zip`** to both
   `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`, with
   `docs/TECHNICAL.md` (already inside the zip) called out in the email
   body. Then fill in §3 below.

**Operational note — corrected 2026-08-16:** this used to say the zip was
"not covered by any `.gitignore` entry." That is now wrong: `.gitignore:40`
has `submission/*.zip` (added since this note was written, per its own
comment: "the zip is a derived artifact — committing it would churn 1.6MB of
binary in every diff for no gain. The script that builds it IS tracked."),
confirmed with `git check-ignore -v submission/jarvis-submission.zip`. So it
is *intentionally* untracked, not merely uncovered by an oversight. This
does not change anything actionable — it never needed to be tracked in git
to be emailed — but the old claim was backwards, so it's corrected here.

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
- **R11 (wake word) is implemented and wired in as the default. Detection
  is unconfirmed — updated 2026-08-16, this is no longer "no one has tried
  it yet."** Picovoice discontinued free-tier Porcupine AccessKeys, so the
  default engine is openWakeWord's keyless "hey Jarvis" model running
  on-device via `onnxruntime-web` — no key, no signup, no env var, loading
  lazily only once passive mode is switched on. The 134 frontend tests
  (including `openWakeWordDetector.test.ts`) prove the orchestration around
  it, not that real speech triggers it. **Hank tested it on the deployed
  build tonight and it did not fire.** That is a real, reproduced result,
  not a theoretical gap — but it is not yet diagnosed as broken-and-dead
  either: it is under active investigation. Do not record the demo video's
  wake-word segment (if the eventual content checklist wants one) until
  this is resolved one way or the other. Also note the three vendored model
  weight files are CC BY-NC-SA 4.0 (NonCommercial), not the same license as
  the rest of the stack — see `docs/TECHNICAL.md` §5 and
  `frontend/public/wake-models/NOTICE.md`.
