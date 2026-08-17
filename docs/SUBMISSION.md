# Jarvis — Submission checklist (T-13)

Human-facing status and action list for closing out submission. Run
`bash scripts/verify_submission.sh` to re-check live state — it exits
non-zero until the items in §2 are done: sending the email, filling the
missing PRD region, and closing T-13 itself are outside what an agent can
do.

## 1. Status, item by item

| Item | Status | Detail |
|---|---|---|
| `docs/TECHNICAL.md` (architecture, stack rationale, known limits, AI-usage note) | **DONE** | Written against the code as it exists now; see the file for full detail, including an honest limits section. |
| `scripts/verify_submission.sh` | **DONE** | Scripts `[1/4]` evidence freshness, `[2/4]` `docs/TECHNICAL.md` present, `[3/4]` zip built, `[4/4]` memory suite (T-31). Repo-private and video-present are satisfied by the two rows below instead of a script check — see the removal-point comments at the top of the script. |
| `submission/build_zip.sh` + a verified zip | **DONE (mechanism)** | Builds a fresh zip from an explicit file list, excluding `.env`, `.git`, `backend/.venv`, `frontend/node_modules`, `backend/var`, `.claude/claims`, `.claude/evidence`, `.claude/evidence-v1`, `heartbeat.jsonl`, `.codegraph`, caches, and `frontend/dist`. Rebuilds on every `verify_submission.sh` run, so it always reflects current repo state. Run `unzip -l submission/jarvis-submission.zip` for the current file count and size. |
| Acceptance 1 — video content checklist | **PLACEHOLDER, BLOCKED** | `docs/SPEC.md` §Open questions item 1: the required contents of the video and the Attachments section were cut off in the provided PRD screenshots. No checklist has been invented to fill the gap. **Needs**: the missing PRD screenshot region from Hank. |
| Demo video file | **Recorded** | https://www.loom.com/share/b30d03307bd147aca97e9c64bd123d5a |
| Repo-private confirmation | **DONE** | Confirmed manually by Hank in the GitLab UI (Settings → General → Visibility: private). The saved PAT lacks `api`/`read_api` scope, so `verify_submission.sh` cannot check this itself — see the removal-point comment at the top of the script. |
| Zip emailed to both addresses | **BLOCKED — human action** | Sending email is the human's call; no agent has sent anything. **Needs**: Hank sends `submission/jarvis-submission.zip` to `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`. Leave §3 blank until it happens. |
| Evidence-freshness gate (T-22, wraps `check_evidence_staleness.sh`) | **FAILING** | `bash scripts/check_evidence_staleness.sh .` reports **20 STALE + 1 MISSING (`T-13`)** against 34 fingerprinted receipts in `.claude/evidence/`. Every STALE finding is a different ticket's commit touching a shared scope glob, not a regression in that ticket's own deliverable — expected, recurring, not a defect queue. `MISSING: T-13` is by design: this ticket cannot mint its own receipt until submission happens, the last step of §2. |

## 2. Action list (do these in order)

1. **Supply the missing PRD screenshot region** (`docs/SPEC.md` §Open
   questions item 1) so acceptance 1's placeholder can be replaced with a
   real, human-approved checklist.
2. **Rebuild the zip right before sending** (`bash submission/build_zip.sh`,
   or just re-run `verify_submission.sh`, which rebuilds it as part of
   `[3/4]`).
3. **Send `submission/jarvis-submission.zip`** to both
   `wduffy@frontieraudio.com` and `pmoeckel@frontieraudio.com`, calling out
   `docs/TECHNICAL.md` (already inside the zip) and the Loom demo link in
   the email body. Then fill in §3 below.
4. **Re-run `bash scripts/verify_submission.sh`.** `[2/4]`–`[4/4]` already
   pass; `[1/4]` (evidence freshness) is expected to keep failing until
   T-13 itself closes (see the evidence-freshness row in §1) — a nonzero
   exit here is not proof something is still broken.

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
  with `repo` scope — capable of merge/force-push across every repo its
  owner can push to, not just the intended test repo. No code change can
  fix this; only a fine-grained, single-repo token (exact spec in
  `docs/agent-pr-demo.md` §"Token-scope finding") closes it.
- **R11 (wake word) is met.** Default engine is openWakeWord's keyless "hey
  Jarvis" model, running on-device via `onnxruntime-web` — no key, no
  signup, no env var. Confirmed live against the deployed build: score
  0.998 (threshold 0.5) on the spoken phrase, and a wake-triggered room
  join in the Fly worker logs (job `AJ_H5pTAkq853Hf`). The 166 frontend
  tests (including `openWakeWordDetector.test.ts`) separately cover the
  orchestration around it. The three vendored model weight files are CC
  BY-NC-SA 4.0 (NonCommercial), not the same license as the rest of the
  stack — see `docs/TECHNICAL.md` §5 and
  `frontend/public/wake-models/NOTICE.md`.
