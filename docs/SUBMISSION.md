# Jarvis — Submission checklist (T-13)

Human-facing status and action list for closing out submission. Run
`bash scripts/verify_submission.sh` any time to re-check live state — it is
expected to (and currently does) exit non-zero until the items in §2 below
are done. That is correct behavior, not a bug in the script: several items
are genuinely outside what an agent can do (sending email, a missing PRD
region, and recording the demo video; see §1 below).
**Updated 2026-08-16**: the remote now exists and Cartesia is fixed — both
used to be listed here as agent-unreachable blockers and no longer are.
**Also updated 2026-08-16**: the repo-private *script check* is gone (see
its own row in §1) — GitLab visibility is confirmed, just by Hank manually
in the GitLab UI rather than by `verify_submission.sh`, because the saved
token can push but has no `api`/`read_api` scope to read it back. That is
no longer listed as an agent-unreachable blocker either; it is done.

## 1. Status, item by item

| Item | Status | Detail |
|---|---|---|
| `docs/TECHNICAL.md` (architecture, stack rationale, known limits, AI-usage note) | **DONE** | Written against the code as it exists now; see the file for full detail, including an honest limits section. |
| `scripts/verify_submission.sh` extended with the acceptance-3 checks | **DONE** | Originally added all 4 of acceptance 3's checks (repo-private, video-present, TECHNICAL.md-present, zip-built) to the existing T-22 evidence-freshness gate. Each check fails loudly with a reason when it can't be performed — none silently skip. **Update 2026-08-16 (a):** T-31 (a later, unrelated ticket) added a memory-suite check. **Update 2026-08-16 (b):** the repo-private check was removed from the script entirely (see the dedicated row below) — it could never pass with the scoped token available, and Hank confirmed visibility manually in the GitLab UI instead. Net effect of both updates: the script now scripts 3 of acceptance 3's 4 checks plus the memory suite, reporting `[1/5]`..`[5/5]`; repo-private is satisfied by human confirmation, not by a `[/5]` line. |
| `submission/build_zip.sh` + a verified zip | **DONE (mechanism)** | Builds a fresh zip from an explicit file list (not a fragile glob), excludes `.env`, `.git`, `backend/.venv`, `frontend/node_modules`, `backend/var`, `.claude/claims`, `.claude/evidence`, `.claude/evidence-v1` (the old `.pass`-era receipts, superseded by `.claude/evidence`), `heartbeat.jsonl` (watchdog liveness pings, no reader value), `.codegraph`, caches, and `frontend/dist`. **Rebuilt and re-measured 2026-08-16: 284 files / ~4.6 MB** (`unzip -l submission/jarvis-submission.zip`), includes `docs/TECHNICAL.md`. The `250 files / ~1.6 MB` this row previously quoted is stale — rebuild-derived numbers move as the repo grows; treat this as a snapshot, not a pinned fact, and re-run `unzip -l` yourself before trusting a number here. Rebuilds every time `verify_submission.sh` runs, so it always reflects current repo state — including whatever ends up in `submission/video/` (see below). |
| Acceptance 1 — video content checklist | **PLACEHOLDER, BLOCKED** | `docs/SPEC.md` §Open questions item 1: the required contents of the 5–10 min video and the Attachments section were cut off in the provided PRD screenshots. No checklist has been invented to fill this gap — that would defeat the point of a human-approved checklist. **Needs**: the missing PRD screenshot region from Hank. |
| Demo video file | **UNBLOCKED 2026-08-15 — nothing prevents recording it now** | ~~Cartesia TTS returning HTTP 402~~ — **stale, and this row was the last thing saying otherwise.** Cartesia was upgraded on 2026-08-15 and the deployed assistant demonstrably speaks. Two corroborating pieces of evidence, both in `docs/latency-report.md`, precisely as recorded there (an earlier pass of this row overstated this as "8/8 driven turns completed", which the report does not actually support — corrected 2026-08-16): (1) the deployed worker (Fly app `jarvisforge` v8, `iad`) produced real synthesized audio with worker-side `latency.jsonl` records for **6 of 8** driven turns (the other 2: one lookup turn excluded by definition, one that logged no record at all); (2) a separate **local** run completed a full **3/3**-turn live LiveKit conversation end-to-end with real speech in and real synthesized audio out (`docs/first-live-conversation-2026-08-15.json`). TTS time-to-first-byte measured from inside the container: `sonic-3` median 162 ms. So a demo video of a working voice assistant is now possible; `submission/video/` is simply still empty (confirmed empty 2026-08-16). **Needs**: Hank to record it. Only the *content checklist* above remains genuinely blocked, on the missing PRD region — the recording itself is not. |
| Repo-private confirmation | **DONE — confirmed manually by Hank in the GitLab UI; script check removed 2026-08-16** | This was scripted as `verify_submission.sh` check `[2/6]`, calling the GitLab API (`GET /api/v4/projects/hankholcomb%2Fjarvisforge`). It could not pass by running it, in any state of the world: the saved PAT in the macOS keychain for `labs.gauntletai.com` authenticates fine (`git push` works) but lacks `api`/`read_api` scope, so **every** call to the GitLab API — including the minimal `GET /api/v4/user` — returns HTTP 403 with body `{"error":"insufficient_scope","error_description":"The request requires higher privileges than provided by the access token.","scope":"ai_workflows api read_api"}` (verified live 2026-08-16, not inferred). Minting a token with `read_api` scope is a human action this repo's rules don't ask an agent to do unprompted, so instead: **Hank has manually confirmed in the GitLab UI (Settings → General → Visibility) that the project is private.** Per his instruction, the check was then removed from `scripts/verify_submission.sh` entirely (comment + logic + helper variables), rather than left as a permanent, unfixable red line — see the removal-point comment at the top of that script for the full record. **This does not mean the item stopped mattering**: T-13's acceptance criterion 3 lists "repo private" as one of its four required checks, same as before; it is satisfied here by this row's human confirmation instead of by a script line. |
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
   target) remote now exist, and `gitlab` has been pushed to.
   ~~Confirm repo-private via the GitLab API~~ **DONE, differently than
   planned** — `scripts/verify_submission.sh`'s repo-private check got
   **HTTP 403** every time (verified live 2026-08-16; the saved token can
   push but has no `read_api`/`api` scope, so it structurally cannot read
   visibility back). Rather than leave a permanently red, unfixable check
   in the gate, Hank confirmed visibility manually in the GitLab UI
   (Settings → General → Visibility: private) and had the check removed
   from the script entirely — see the "Repo-private confirmation" row in
   §1 above and the removal-point comment in the script itself. Nothing
   left to do here.
4. **Record the 5–10 min demo video**, including GitHub Q&A against the
   chosen active mid-size OSS repo, once step 2 is done (step 1, Cartesia,
   is no longer a blocker). Save it to `submission/video/<name>.mp4` (or
   `.mov`/`.mkv`/`.webm`/`.avi`) — the verify script looks there.
   `submission/video/` is confirmed still empty as of 2026-08-16.
5. **Re-run `bash scripts/verify_submission.sh`.** It now reports 5 checks,
   `[1/5]`–`[5/5]` (T-31 added a memory-suite check after this row was
   originally written; the repo-private check that used to be `[2/6]` was
   removed 2026-08-16, see §1 above, which is why the total dropped from
   6 to 5 rather than growing to 7). As of 2026-08-16, `[3/5]`
   (TECHNICAL.md), `[4/5]` (zip build), and `[5/5]` (memory suite) already
   pass on their own; `[1/5]` (evidence freshness) and `[2/5]` (video
   present) are the two still failing, for the reasons in §1 above. Step 4
   closes `[2/5]`; `[1/5]` is expected to keep failing until `T-13` itself
   closes (see the evidence-freshness row in §1) — do not treat a nonzero
   exit here as proof something is still broken once step 4 is done.
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
   `verify_submission.sh`, which rebuilds it as part of check `[4/5]`) so it
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
