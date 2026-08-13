# Jarvis — Spec

## Problem & intent

Gauntlet partner project for Frontier Audio (SILVER tier). Build "Jarvis": a
real-time voice assistant with persistent memory, graded on accuracy, latency,
clarity, and intuitiveness. The PRD's hardest requirement is grounded,
zero-fabrication answering over live data (GitHub repos + a real-time API
feed) while keeping conversation latency near-human. Greenfield build,
one-week window, solo builder executing via Claude Code.

## User-visible behavior

### MVP (all required)

- **R1 — Low-latency conversation.** WHEN the user finishes an utterance that
  requires no external tool call, THE SYSTEM SHALL begin audible reply
  playback within 800ms at p95, measured turn-end → first-audio over a logged
  sample of ≥50 turns. ("Near-zero" in the PRD is operationalized as this
  budget; anything lower is upside.)
- **R2 — Natural cadence.** WHEN a pending tool call is expected to exceed
  500ms, THE SYSTEM SHALL begin playing a spoken acknowledgment ("Let me
  check that…") within 300ms of turn end and run the tool behind it. THE
  SYSTEM SHALL never leave more than 2s of unexplained silence after a user
  turn.
- **R3 — Cross-session memory.** WHEN a user references a prior session's
  topic (e.g. "yesterday I asked about X — remind me"), THE SYSTEM SHALL
  retrieve and use that session's stored memory. Testable as a round-trip:
  fact stated in session A is recalled in a fresh session B for the same
  user.
- **R4 — Interruptibility.** WHEN the user speaks during assistant playback,
  THE SYSTEM SHALL pause playback within 300ms (barge-in). WHEN the user says
  a stop phrase ("Quiet, Jarvis" / "stop"), THE SYSTEM SHALL cancel the
  current turn entirely and return to listening.
- **R5 — Self-awareness.** WHEN asked what it can do or what its limits are,
  THE SYSTEM SHALL answer only from a versioned capabilities manifest file —
  never from model improvisation.
- **R6 — Grounded answering (zero hallucination).** Answers about GitHub or
  live-feed data SHALL derive exclusively from tool results returned in the
  current turn (plus memory hits for R3). WHEN no tool result supports an
  answer, THE SYSTEM SHALL say it doesn't know rather than guess. Checkable
  criterion: 0 fabrications and correct refusal behavior on the
  human-approved golden eval set (see R6a).
  - **R6a** — A golden eval set of ≥30 cases (grounded answers, forced
    refusals, memory recalls) exists, is approved by Hank, and runs
    automatically. "Zero hallucinations" as an absolute is unfalsifiable;
    this set plus refusal-first prompting is the enforceable proxy.
- **R7 — GitHub Q&A.** WHEN given any public GitHub URL, THE SYSTEM SHALL
  answer questions about the repository including open PRs, open issues, PR
  comments, and last merges, from live GitHub data. Repos whose packed code
  digest exceeds the configured token budget degrade to targeted queries
  (metadata/tree/per-file) — never to fabrication. Ingestion latency is
  masked per R2, not hidden.
- **R8 — Real-time feed Q&A (weather).** WHEN asked about current weather or
  forecast for a location, THE SYSTEM SHALL answer only from a live weather
  API response fetched in that turn, and SHALL state data recency when asked.

### Bonus (build in this order, only after R1–R8 are green)

- **R9 — Multi-user + personalization.** Users sign in (Clerk). Memory and
  preferences are isolated per user: user A's stored facts are never
  retrievable in user B's session. Standing preferences ("never mention X;
  always flag Y") are stored and honored in every subsequent session. 10
  simultaneous sessions pass a load smoke test.
- **R10 — Agentic issue→PR.** WHEN asked to analyze and fix a specific open
  issue in a repo the user controls, THE SYSTEM SHALL run a background coding
  agent that opens a PR attempting the fix, announcing start/progress/result
  audibly (per R2). It SHALL never merge.
- **R11 — Passive wake word.** In passive mode the client listens on-device
  (Porcupine, built-in "Jarvis" keyword) and publishes no audio upstream
  until woken. Waking activates a normal session.
- **R12 — Mobile.** DEFERRED. Explicit non-goal unless R1–R11 all ship early.

## Constraints

- **C1** Backend: Python, LiveKit Agents; media via LiveKit Cloud (free tier).
- **C2** STT: Deepgram streaming. TTS: Cartesia; Deepgram Aura-2 is the
  documented fallback and must be swappable by env var only.
- **C3** LLMs: conversation model = `$CONVO_MODEL` (default
  `claude-haiku-4-5`); tool/GitHub reasoning model = `$TOOL_MODEL` (default
  `claude-sonnet-4-6`). Swap point is env-only. Verify current model strings
  at build time.
- **C4** Memory: mem0 OSS, self-hosted, backed by Postgres + pgvector (one
  Postgres instance also holds preferences).
- **C5** Frontend: React + Vite SPA. **Next.js is prohibited.** Hosting:
  Fly.io (single app; web + worker process groups). **Vercel is prohibited.**
- **C6** Submission repo is **private**; R7 applies to *public* target repos
  (no contradiction — the feature ingests public repos, the deliverable repo
  stays private).
- **C7** CI: GitHub Actions, deliberately light (single job, lint + unit on
  pull_request only, no matrix, no long e2e) — cost-constrained.

## Non-goals

- Mobile apps (R12 deferred), telephony/SIP, multilingual support.
- Local/offline models, voice cloning, custom model training.
- Auto-merging agent PRs; any write action on repos the user doesn't control.
- Production security hardening beyond Clerk auth + per-user data isolation.
- Next.js, Vercel (C5). Pre-building embeddings indexes for arbitrary repos.

## Success criteria

1. R1–R8 acceptance all green via their ticket verify commands.
2. Eval harness (R6a): 0 fabrications, correct refusals, on the approved set.
3. Latency report from deployed instance shows R1 budget met.
4. Deployed URL live on Fly.io; smoke script passes against it.
5. Demo video (5–10 min) recorded, incl. GitHub Q&A against an active
   mid-size OSS repo; zip emailed to wduffy@ and pmoeckel@frontieraudio.com;
   technical documentation included.

## Open questions

1. **PRD screenshot gap (blocks T-13 acceptance only, not design/build):**
   the required contents of the 5–10 min video and the Attachments section
   were cut off in the provided screenshots. T-13 carries a placeholder;
   capture the missing region before finalizing the submission checklist.
