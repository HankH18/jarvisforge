# Jarvis

Jarvis is a real-time, low-latency voice assistant with persistent
cross-session memory. It answers strictly from grounded, live data — a
weather API and GitHub repos it can query on demand — refusing rather than
fabricating when the data doesn't support an answer, while holding
conversation latency close to human turn-taking speed.

This repo is **private** (see `docs/SPEC.md` §C6).

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
- [`CLAUDE.md`](CLAUDE.md) — build execution protocol
