# Jarvis — Technical Documentation

T-13 deliverable: architecture, stack rationale, and known limits, written
against the code as it exists today (2026-08-14), not as the plan imagined
it. Companion to `docs/SPEC.md` (intent) and `docs/DESIGN.md` (pinned
contracts) — this document reports what was actually built and where it
falls short, including things that are currently broken in production.

## 1. What Jarvis is

A real-time voice assistant (LiveKit) with persistent cross-session memory
(mem0/pgvector) that answers questions about GitHub repos and live weather
strictly from tool results fetched in the current turn — refusing rather
than guessing when the data doesn't support an answer — while trying to
keep conversational latency close to human turn-taking.

## 2. Architecture

```
Browser SPA (React+Vite) --WebRTC--> LiveKit Cloud <--rooms/audio--> Agent worker (LiveKit Agents)
       |                                                                    |
       +-- POST /api/token --> FastAPI server (Fly "web" process)          +--> Deepgram STT
                                                                             +--> Cartesia TTS (fallback: Deepgram Aura-2)
                                                                             +--> CONVO_MODEL (fast, every turn) / TOOL_MODEL (heavy tools)
                                                                             +--> weather tool (Open-Meteo)
                                                                             +--> github tools (gitingest + githubkit GraphQL)
                                                                             +--> mem0 (self-hosted) -> Postgres + pgvector
                                                                             +-- (bonus) --> agent-PR job runner (Claude Agent SDK)
```

Two independent processes share one Fly app (`fly.toml`):

- **`web`** — `uvicorn scripts.serve_web:app` — serves the built SPA and the
  `POST /api/token` LiveKit-grant endpoint (`backend/src/jarvis/server/app.py`).
  The only process with a public port/service.
- **`worker`** — `python -m jarvis.pipeline.worker start` — the LiveKit
  Agents worker that actually joins rooms and runs the voice turn loop. No
  public port; it connects out to LiveKit Cloud. Sized at `shared-cpu-2x` /
  2GB after the default 256MB OOM-killed it loading
  `livekit-agents` + the Anthropic/Cartesia/Deepgram plugins + mem0's
  embedding stack, and a single shared vCPU couldn't keep up with concurrent
  VAD + resampling + streaming STT/TTS (see `fly.toml`'s inline comments).

Repo layout:

```
backend/src/jarvis/{pipeline,server,llm,tools,memory,cadence,auth,agents_pr}/
backend/tests/{pipeline,grounding,tools,memory,cadence,multiuser,agent_pr}/
backend/evals/            # golden.jsonl (R6a) + runner
frontend/src/             # SPA; auth/ (Clerk) and wake/ (openWakeWord) are bonus additions
scripts/                  # ops, deploy smoke, latency reporting, filler build
```

### 2.1 Voice pipeline (STT -> LLM -> TTS)

`backend/src/jarvis/pipeline/providers.py` builds every provider purely
from env vars (SPEC C2/C3) so the turn loop never hardcodes a backend:

- **STT**: Deepgram, no swap point (not pinned as env-swappable).
- **TTS**: `TTS_PROVIDER` selects `cartesia` (default) or `aura2`
  (Deepgram Aura-2, the documented fallback) — a single `if/elif`, no
  abstraction layer. The Cartesia branch pins `word_timestamps=False`
  (T-16): the plugin defaults to streaming word-level alignment data the
  app never consumes, and cutting it was a real first-audio latency win.
- **LLM**: `CONVO_MODEL` (fast, runs every turn with tool schemas attached)
  and `TOOL_MODEL` (escalation model). Model ids come from those two env
  vars only — never hardcoded (SPEC C3); an unset var is a hard `RuntimeError`
  from `jarvis.llm.routing._require_env`, not a silent default.

`backend/src/jarvis/pipeline/session_wiring.py::wire_session_events`
subscribes to the LiveKit `AgentSession`'s events and is the one place that
turns SDK state into the product's wire contract: `agent_state_changed` ->
`{"type":"agent_state","state":...}` + latency-log timestamps (`thinking`
opens a turn's clock, `speaking` closes it); `user_input_transcribed` /
`conversation_item_added` -> `{"type":"transcript",...}` events on the
LiveKit data channel, which the SPA (`frontend/src/hooks/useJarvisSession.ts`)
renders live. Barge-in relies on the SDK's own `on_start_of_speech`-driven
interrupt (no VAD implemented in this repo); `TurnController`
(`cadence/interrupt.py`) is bookkeeping on top of that, not the interrupt
mechanism itself.

### 2.2 Grounded tool-calling and ANSWER_POLICY (R6)

The core anti-hallucination mechanism is structural, not prompt-only:
`synthesize_grounded` (`backend/src/jarvis/llm/synthesis.py`) refuses
*before ever calling a model* when no tool result has usable evidence
(`usable_results` — no `error`, non-empty `data`); only when there's at
least one usable `ToolResult` does it call the model at all, with
`ANSWER_POLICY` (`backend/src/jarvis/llm/policy.py`) as the system prompt:
answer strictly from `results[].data`, never cite training knowledge for
GitHub/weather facts, refuse with the exact prefix `"I don't know"` when
unsupported, honor user preferences verbatim. A fact the data establishes
*negatively* ("no, Jarvis doesn't support Spanish — English only") is
pinned as a supported answer, not a refusal — this distinction is
exercised directly by golden case `G-038` (see §5).

Every tool returns the same `ToolResult` shape
(`source`, `fetched_at`, `data`, `error`) — the single channel an answer is
allowed to draw from. Tools are tagged `heavy: bool`
(`backend/src/jarvis/tools/registry.py`); `route_model`
(`backend/src/jarvis/llm/routing.py`) escalates the turn to `$TOOL_MODEL`
iff a heavy tool fired this turn (`github_qa`, `agent_pr`), otherwise the
turn stays on `$CONVO_MODEL` (`weather`, `memory`, `self_manifest`).

GitHub Q&A (R7) has two tools: `load_repo_digest` (gitingest, cached by
`(repo, head_sha)` with a 15-minute TTL) and `github_query` (githubkit
GraphQL for `open_prs`/`open_issues`/`pr_comments`/`last_merges`/`repo_meta`).
Repos whose packed digest exceeds the token budget degrade to targeted
`github_query` calls rather than fabricating — no embedding index is built
for arbitrary repos (a deliberate DESIGN.md decision: index builds mid-turn
would violate the latency budget).

### 2.3 Cadence / filler system (R2, R4)

`backend/src/jarvis/cadence/`: `FILLER_THRESHOLD_MS = 500` and
`FILLER_START_DEADLINE_MS = 300` (`policy.py`, pinned, not tunable without
a DESIGN.md change) govern when a spoken filler ("Let me check that…")
plays behind a slow tool call. `TurnController` (`interrupt.py`) is a small
state machine (`idle -> speaking -> paused/cancelled`) driving barge-in
(any speech during playback pauses it) and stop phrases (`"quiet jarvis"`,
`"jarvis quiet"`, `"stop"` — cancel the turn and skip generating a reply,
via `AgentSession.commit_user_turn(skip_reply=True, ...)`). Filler audio
clips are pre-synthesized at build time by `scripts/build_fillers.py`
rather than synthesized live, so playing one never adds TTS latency to the
critical path.

### 2.4 Memory (R3) — mem0 on self-hosted pgvector

`backend/src/jarvis/memory/store.py`'s `Mem0MemoryStore` wraps the mem0 OSS
library with a self-hosted `MemoryConfig` (Postgres + pgvector as the
vector store, `BAAI/bge-small-en-v1.5` embeddings, 384 dims) — no managed
mem0 API. One Postgres instance holds both mem0's own tables and this
repo's `preferences(user_id, rule, created_at)` table. `MemoryStore` is a
`Protocol` (`add_turns`, `search`, `get_preferences`, `add_preference`) so
the turn loop and the eval harness depend on the interface, not the
concrete mem0 wiring.

### 2.5 Frontend (SPA)

React + Vite (Next.js and Vercel are both explicitly prohibited, SPEC C5).
`useJarvisSession.ts` owns the LiveKit room lifecycle and the data-channel
event stream; `TranscriptView`/`AgentStateIndicator`/`Controls` render it.
Bonus additions live in `auth/` (Clerk sign-in) and `wake/` (openWakeWord
wake detector, see §4).

### 2.6 Deployment shape (Fly.io)

One Fly app (`jarvisforge`), two process groups defined in one `fly.toml`
(§2 above): `web` (public, health-checked at `/healthz`) and `worker`
(no public port, connects out to LiveKit). Secrets are set via
`fly secrets set` (never committed); `DATABASE_URL` is injected
automatically by `fly postgres attach` rather than hand-set, because Fly's
managed Postgres offering has no pgvector, so the Postgres instance backing
mem0 is self-hosted rather than a Fly add-on (see `docs/fly-deploy-decisions`
in project memory for the fuller reasoning).

## 3. Stack rationale (from docs/DESIGN.md §Decisions)

- **Cascaded STT->LLM->TTS over speech-to-speech**: the tool-call
  chokepoint is where R6 grounding is enforced; an S2S model would bury
  that boundary inside an opaque model. Rejected gpt-realtime-style S2S
  (grounding opacity) and a hybrid of both (two systems in one week).
- **LiveKit over Pipecat**: barge-in, rooms, and multi-user concurrency
  come from the media server essentially for free (R4, R9).
- **Cartesia TTS, Aura-2 fallback**: time-to-first-audio is the dominant
  perceived-latency lever; the fallback is an env-var swap only, not a
  parallel abstraction layer.
- **On-demand repo ingestion (gitingest) over pre-indexing**: arbitrary
  GitHub URLs arrive mid-conversation; building an index would violate
  R1/R2's latency budget. Oversize repos degrade to targeted
  `github_query` calls instead.
- **mem0 on pgvector, self-hosted**: one Postgres serves both memory and
  preferences; rejected Zep/Graphiti (graph overkill for this scope) and a
  DIY vector store (re-solves an already-solved problem).
- **Open-Meteo**: keyless, free, live — lowest-friction grounding demo for
  R8.
- **Deliberately light CI (C7)**: lint + unit only, PR-triggered, no
  matrix/e2e — a stated cost constraint, not an oversight. It is no longer
  unproven: PR #1 ran it on 2026-08-15 (run `31901208728`, success in 48s).
  See §5 for what a green run does and does not cover.

## 4. Bonuses that landed

- **R9 — Clerk multi-user.** `jarvis.auth.clerk.resolve_identity` runs in
  one of two modes decided purely by whether `CLERK_SECRET_KEY` /
  `CLERK_JWT_KEY` is set server-side (never by anything in the request):
  **authenticated mode** verifies a Clerk session JWT and derives identity
  server-side (a client cannot impersonate another user's memory namespace
  by editing the request body); **anonymous mode** falls back to the
  original, pre-Clerk contract. `CLERK_SECRET_KEY` is set on the deployed
  Fly app and `VITE_CLERK_PUBLISHABLE_KEY` is baked into the frontend
  bundle at build time (`backend/Dockerfile`), so the live deployment runs
  authenticated: `POST /api/token` 401s an unauthenticated caller, and the
  SPA gates the console behind an opaque, centred, full-screen Clerk
  sign-in modal (`role=dialog`, `aria-modal`, the app behind it
  `aria-hidden` + `inert`). A signed-out anonymous visitor still gets
  cross-session memory (R3): `frontend/src/lib/deviceId.ts` persists a
  pseudonymous device id in `localStorage`
  (`anon-<uuid>::<per-join-uuid>`), so a reload reuses the same stable
  memory namespace instead of a fresh random one. `scripts/load_smoke.sh`
  drives 10 concurrent real Clerk-shaped identities through the real
  auth-gated `/api/token` and holds 10 real LiveKit room connections open
  simultaneously — deliberately scoped to the transport layer, with no
  silent SKIP on any missing credential.
- **R10 — Agentic issue->PR.** `run_fix_job` (`backend/src/jarvis/agents_pr/`)
  runs the Claude Agent SDK headless in an isolated workdir, authenticated
  via `GIT_ASKPASS` (the token never touches argv), behind a repo
  allow-list (`repo_allowlist.py`, refuses any repo URL not explicitly
  authorized) and a Bash-command allow-list PreToolUse hook
  (`guard.py`, only `git`/`gh` read/write subcommands the job actually
  needs — confirmed live to refuse `ls`, multi-line/`$(...)` commit
  messages, and non-allow-listed `gh` subcommands during a real recorded
  run). It announces start/progress/PR-opened over both the data channel
  and TTS, and never merges. See §5 for the real, unresolved gap in this
  feature (token scope).
- **R11 — Passive wake word.** A client-side passive/active state machine
  (`frontend/src/state/passiveActiveMachine.ts`,
  `wakeSessionController.ts`) gates microphone publishing: in passive mode
  no audio is published upstream until the wake detector fires. The default
  detector (`frontend/src/wake/openWakeWordDetector.ts`) runs openWakeWord's
  pretrained "hey jarvis" ONNX model chain entirely on-device via
  `onnxruntime-web`'s WASM execution provider — keyless, no signup, no env
  var, loaded lazily only once passive mode is switched on. See §5 for the
  license terms on the vendored model weights and how detection was
  confirmed working end-to-end.

## 5. Known limits — honest state as of 2026-08-14

**R1 (low-latency conversation) is unmet.** Deployed p95 time-to-first-audio
on a no-lookup turn is **1122 ms** against the 800 ms target — a ~322 ms
gap. Every lever was measured, not estimated: region co-location with the
APIs, a faster Cartesia voice model, Anthropic prompt caching, STT
endpointing, preemptive TTS — none closes it. The bottleneck is the LLM
call's own time-to-first-token (~630 ms mean), not TTS or network. The
decision was to ship the honest measurement rather than mask the gap with
filler audio. Full breakdown and every ruled-out lever, with evidence: see
`docs/latency-report.md`.

**The R6a golden eval set is comparatively small.** It has 38 cases (17
grounded, 14 refusal, 7 memory) — enough to clear R6a's ≥30 floor and it
has been reviewed and re-approved by Hank in-repo (`backend/evals/golden.jsonl`
`_meta.approved_by`/`approved_at`), but 38 hand-built cases is a thin
statistical sample next to the space of real user questions; "zero
fabrications" is only as strong as this set's coverage, not a
population-level guarantee.

**The agent-PR (R10) token is broader than R10's own acceptance wants.**
The `GITHUB_TOKEN` actually used for the one live recorded run
(`docs/agent-pr-demo.md`) is a classic PAT with OAuth scope `repo` —
confirmed via the `x-oauth-scopes` response header — which genuinely grants
merge and force-push capability across every repository its owner has push
access to, not just the one designated test repo. The Bash allow-list in
this repo's own code is a real, tested control on what the agent will
*attempt*, but it is not the same thing as the credential itself being
incapable of merging — that requires replacing the classic PAT with a
fine-grained, single-repo, "Contents: read/write + Pull requests:
read/write, no Administration" token, which only Hank can mint (exact spec
in `docs/agent-pr-demo.md` §"Token-scope finding"). This is unresolved as
of this writing.

**The wake word (R11) engine changed after Picovoice discontinued the
free tier it was built against; end-to-end detection is now confirmed.**
Picovoice pulled free-tier Porcupine AccessKeys
entirely and moved to enterprise-only licensing, which made the original
plan unobtainable regardless of engineering effort — not a scope cut, an
external one. `frontend/src/wake/porcupineWakeDetector.ts` still exists
(real, tested orchestration code, kept for the record) but is dead: nothing
imports it, and it was never the default even before this — see that
file's own header. The default detector is now
`frontend/src/wake/openWakeWordDetector.ts`, running openWakeWord's
pretrained "hey jarvis" ONNX chain (three vendored files under
`frontend/public/wake-models/`) through `onnxruntime-web`'s WASM runtime,
entirely on-device, entirely offline once loaded. It needs **no key, no
signup, no env var**: `isWakeDetectorConfigured()` (`createWakeDetector.ts`)
now checks genuine browser capability (`WebAssembly` +
`navigator.mediaDevices.getUserMedia`) instead of an env var fixed at build
time, because there is no longer a build-time fact to check. The spoken
phrase is two words, "hey Jarvis" (openWakeWord's model), not the single
word "Jarvis" Porcupine used.

Payload is real but lazy. The onnxruntime-web WASM binary is 13,479,978
bytes raw (3,422,994 bytes gzipped over the wire, confirmed against a real
build and serve), plus ~3.5 MB across the three `.onnx` files
(`frontend/public/wake-models/NOTICE.md`). None of it ships in the app's
main JS bundle: `loadRealSessions()` reaches the ONNX runtime only through a
dynamic `import()`, itself reached only from `start()`, itself called only
once passive mode is switched on — confirmed in the built `dist/` output,
where the runtime lives in its own chunk separate from the 206 KB main
bundle. A page load with passive mode off issues zero wake-related
requests; enabling the toggle is what triggers the five fetches (the
runtime chunk, the wasm binary, three model files).

**Detection is confirmed working end to end.** Two real bugs blocked it
initially, found by reading openWakeWord's own upstream source rather than
guessing: Web Audio's `getChannelData` returns float32 samples in
`[-1, 1]`, but the melspectrogram model is calibrated on int16-scale
magnitudes, so the detector was feeding it audio ~32768x too quiet; and
openWakeWord applies an explicit `x/10 + 2` rescale to the melspectrogram
output before the embedding model sees it, which this port had not
applied. `WAKE_THRESHOLD` (`openWakeWordDetector.ts`, `0.5`) was left
untouched through both fixes — lowering it would have hidden the bugs
rather than fixed them. With both landed, a live test against the deployed
build scored **0.998** on the spoken phrase "hey Jarvis" and produced a
real wake-triggered room join, observed in the Fly worker logs for job
`AJ_H5pTAkq853Hf`. 166 frontend tests pass (19 files, `cd frontend && npm
run -s test -- --run`), including `openWakeWordDetector.test.ts`, covering
the buffering/windowing math, the threshold/cooldown gating, and that
`start()` rejects loudly on a model-load or microphone-permission failure,
against fake ONNX sessions — the live test above is what covers real
audio through the real `hey_jarvis_v0.1.onnx` weights. `createWakeDetector.ts`
still fails loudly rather than silently degrading when the browser lacks
the required capability.

**The three pretrained model weight files are CC BY-NC-SA 4.0, not
Apache-2.0 like the rest of the stack.** Per openWakeWord's own README,
quoted in full in `frontend/public/wake-models/NOTICE.md`: the framework
code is Apache-2.0, but the pretrained models are Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 International, "due to the
inclusion of datasets with unknown or restrictive licensing as part of the
training data." For these three files specifically that means attribution
is required (the NOTICE file serves that purpose), non-commercial use only,
and any redistribution must carry the same license. That is accepted here —
an educational cohort submission, not a commercial product — but it is a
real constraint, not a footnote: it would need resolving (clearing rights,
or swapping in a permissively-licensed wake model) before any commercial
use of this app.

**Clerk auth is live on the deployed app.** `/api/token` requires a
verified Clerk session token and 401s everyone else; the switch is the
same server-side-only mechanism described in §4 — nothing in the request
can move a deployed process between modes.

**The filler assets shipped only after a packaging defect was found and
fixed post hoc (T-26).** The original filler `.wav` clips lived in
`backend/assets/fillers/`, a sibling of `backend/src` outside the
`jarvis` package tree that `backend/pyproject.toml` actually ships — they
were never going to appear in a built wheel or the Docker image no matter
how the runtime resolution logic in `cadence/fillers.py` was written. The
fix copies the same assets into `backend/src/jarvis/cadence/assets/fillers/`
(a packaged location) rather than moving or deleting the original; the two
copies are guarded against drifting apart by
`backend/tests/cadence/test_asset_resolution.py`. This is the concrete
instance of a broader pattern in this project: work can be built and unit
tested while never actually being connected to what ships.

**CI runs, and is proven green — as of 2026-08-15.**
`.github/workflows/ci.yml` runs three jobs on `pull_request`: `backend`
(lint + unit tests), `frontend` (lint, build, unit tests), and `harness`
(the four shell suites under `scripts/tests/` — evidence receipts, scope
guard, verify_submission DB wiring, smoke exit codes). It runs against the
`github` remote (https://github.com/HankH18/jarvisforge; the `gitlab`
submission remote is self-hosted, not GitHub — see `CLAUDE.md`).

The `harness` job was added 2026-08-15. Before it, no workflow referenced
anything under `scripts/tests/`, so those suites ran only when a ticket
closed on a developer's laptop and never again. It deliberately omits
`run_claim_tests.sh`, which is 36/38 — the workflow comment records why
and what has to land before it can be added.

PR #1 was the first pull request this repo ever had, and its first run
**failed in 4 seconds**: `astral-sh/setup-uv@v10` does not resolve — that
action ships releases `v10.0.0`/`v10.0.1` but its moving major tag stops
at `v7`, so the backend job could never have run on any commit. The pin
survived unnoticed precisely because nothing had ever executed it. Fixed
to `@v10.0.1`; `actions/checkout` and `actions/setup-node` bumped v4 → v5.
Re-run `31901208728`: **success in 48s**, both jobs real — backend
`503 passed, 13 skipped` plus `All checks passed!` (ruff), frontend
`16 files / 105 tests` plus build.

Note the `503 passed, 13 skipped`, against 516 locally: the 13 skipped are
the memory suite, because CI has no Postgres, and T-31's banner says so
loudly in the CI log. The database-required gate is
`scripts/verify_submission.sh`, by design — **so a green CI here does not
mean the memory path was verified.** The local gate story also changed:
the `cc-factory: harness sync` deleted `.claude/hooks/verify_gate.sh`, so
there is no longer a single local regression-gate script run at every
ticket boundary (see `docs/HARNESS-BLOCKER-2026-08-14.md`).

**Evidence staleness.** `scripts/check_evidence_staleness.sh` reports one
of three verdicts per ticket against `.claude/evidence/*.json` receipts:
`CURRENT` (scope content matches the receipt's fingerprint), `STALE`
(a later commit touched a file in that ticket's scope glob — typically
`SCOPE-OVERLAP`, a different ticket touching a shared directory, not a
regression in that ticket's own deliverable), or `MISSING` (no receipt at
all). Run `bash scripts/verify_submission.sh` for the live count. See
`docs/LEDGER-PROVENANCE.md` for what a receipt does and does not attest,
and `docs/SUBMISSION.md` for what this means for submission.

**The submission gate is the one place that requires a reachable memory
database (T-31).** `backend/tests/memory/conftest.py`'s `require_db`
fixture defaults to *skipping* (loudly, per T-23) the mem0/pgvector
integration tests when Postgres is unreachable, rather than failing —
necessary so a plain `cd backend && uv run pytest tests/memory -q` stays
closable on an offline dev machine or in CI (which SPEC C7 forbids adding
a Postgres service to). `scripts/verify_submission.sh`'s check `[4/4]` is
the single context that sets `JARVIS_MEMORY_TESTS_REQUIRE_DB=1`, which
turns that skip into a hard `pytest.fail()` — so a submission can no
longer go out having silently never exercised the memory integration
layer. It needs a reachable Postgres+pgvector at `$JARVIS_TEST_DATABASE_URL`
/ `$DATABASE_URL`, defaulting to
`postgresql://jarvis:jarvis_dev_local_only@localhost:55432/jarvis` (start
it with `cd backend && docker compose up -d`); see `docs/ops-smoke-test.md`
for the full skip/hard-fail contract.

## 6. AI-usage note

This project was built solo, one week, via Claude Code executing against a
pre-approved plan (`docs/SPEC.md` intent, `docs/DESIGN.md` pinned
contracts, `docs/tickets.json` the authoritative task graph — see
`CLAUDE.md` §Build Execution Protocol). Each ticket was implemented by an
agent restricted to that ticket's declared file scope, with a verify
command that had to exit 0 (enforced by a completion hook) before the
ticket could be marked done, and a git commit at both the start and the
close of every ticket so failed attempts could be reverted cleanly rather
than iterated on indefinitely. Product-side, the Claude Agent SDK is used
at *runtime* by the R10 issue->PR feature (`backend/src/jarvis/agents_pr/`)
— a background coding agent the deployed app itself invokes — which is
distinct from, and not restricted by, the interactive-only rule that
governed how this repository itself was built.

Both AI usages in this project are disclosed above in full: (1) Claude Code
as the build tool for this entire repository, and (2) the Claude Agent SDK
as a runtime product feature (R10). Several of the "known limits" in §5 —
the token-scope gap, the filler-asset packaging defect, the evidence
receipts predating fingerprinting — were themselves found by this same
build process auditing its own prior output; they are reported here rather
than silently fixed or omitted because an undisclosed limitation found
later by a reader would undermine trust in everything else in this
document.
