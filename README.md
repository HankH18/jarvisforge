# Jarvis — grounded real-time voice assistant

A real-time voice assistant (LiveKit + Claude) that answers questions about GitHub repos and live weather **only** from data fetched in the current turn. It refuses with "I don't know" instead of guessing, remembers users across sessions (mem0 on pgvector), and can turn a GitHub issue into a pull request on voice command (Claude Agent SDK, allow-listed repos only, never merges).

- **Grounding eval:** 38 of 38 human-approved golden cases passed on live Claude Haiku 4.5 / Sonnet 5, with 0 fabrications and 0 wrong refusals ([`docs/evals/latest.json`](docs/evals/latest.json), run of 2026-08-15).
- **Latency, measured on the deployed stack on 2026-08-15:** no-lookup time-to-first-audio was 730 ms median and 1122 ms p95 (n = 6) against an 800 ms target. The six worker-side measurements behind these figures were stored on the Fly machine, which is no longer running, and were not kept, so the figures cannot be recomputed from this repo. The miss is documented, with every lever measured ([`docs/latency-report.md`](docs/latency-report.md)).
- **Run it locally:** `make install`, then `make test` (backend pytest + frontend Vitest). The voice loop needs LiveKit, Deepgram, Cartesia and Anthropic keys in `.env`; then `make dev-api`, `make dev-worker` and `make dev-web`.
- **Stack:** LiveKit Agents, Deepgram STT, Cartesia TTS, Claude Haiku 4.5 / Sonnet 5, mem0 + pgvector, FastAPI, React, Clerk, Fly.io.
- **Architecture and known limits:** [`docs/TECHNICAL.md`](docs/TECHNICAL.md)

## Repo layout

```
backend/src/jarvis/{pipeline,server,llm,tools,memory,cadence,auth,agents_pr}/
backend/tests/{pipeline,grounding,tools,memory,cadence,multiuser,agent_pr}/
backend/evals/            # golden.jsonl + runner
frontend/src/             # SPA (auth/, wake/ arrive with bonuses)
scripts/  infra: fly.toml, backend/Dockerfile  .github/workflows/ci.yml
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package/dependency manager)
- Node.js 22+
- Docker (for a local Postgres + pgvector instance)

## Quickstart

```bash
make install   # uv sync (backend) + npm ci (frontend)
make lint      # ruff check
make test      # backend pytest + frontend vitest --run
make build     # frontend production build
```

Copy `.env.example` to `.env` and fill in real values before running any
service locally — never commit `.env`.

## Docs

- [`docs/SPEC.md`](docs/SPEC.md) — intent: requirements and constraints
- [`docs/DESIGN.md`](docs/DESIGN.md) — pinned architecture and contracts
- [`docs/TASKS.md`](docs/TASKS.md) — human-readable task graph and merge order
- [`docs/TECHNICAL.md`](docs/TECHNICAL.md) — architecture as built, stack
  rationale, and an honest known-limits section (including the R1 latency
  miss)
- [`docs/SUBMISSION.md`](docs/SUBMISSION.md) — submission checklist and
  status at submission time
- [`CLAUDE.md`](CLAUDE.md) — build execution protocol
