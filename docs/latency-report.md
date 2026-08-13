# Latency report: time-to-first-audio

**STATUS: PLACEHOLDER -- NO REAL DATA. This file does not report a measurement.**

## What R1 requires

`docs/SPEC.md` R1 requires **p95 time-to-first-audio <= 800ms, measured over
at least 50 turns of real, deployed usage** (LiveKit voice pipeline, real TTS
provider, real model calls).

## Current state

Zero deployed turns have been recorded. This environment has:

- `flyctl` installed but **not authenticated** -- nothing has been deployed to
  Fly.io. There is no running `web`/`worker` process pair and no public URL.
- Placeholder (`changeme`) values for `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
  and `CARTESIA_API_KEY` / `DEEPGRAM_API_KEY`. The worker cannot connect to a
  real LiveKit room or synthesize real audio with these values, so it cannot
  produce a single genuine turn, let alone 50.

Because of this, `backend/var/latency.jsonl` does not exist (or, if present
locally, holds only ad hoc dev/test runs, not deployed-usage data), and
`scripts/latency_report.py` will correctly refuse to produce a report against
it -- it exits non-zero below 50 valid turns by design (see T-9 contract and
the tool's own `--min-turns` guard). **No latency number appears anywhere in
this document because inventing one would be a fabrication**, and this
repo's entire R6 thesis is that reported numbers are real measurements, not
guesses.

## Prerequisites before a real report can be generated

1. A human runs `fly deploy` (or equivalent) to actually stand up the `web`
   and `worker` process groups on Fly.io -- this is explicitly **not**
   something an agent may do in this environment (no `fly deploy`, `fly apps
   create`, `fly auth`, `fly secrets set`).
2. Real secrets are set on the Fly app (`fly secrets set ...`) for at least:
   `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `CARTESIA_API_KEY`
   (or `DEEPGRAM_API_KEY`, per `TTS_PROVIDER`), `ANTHROPIC_API_KEY`,
   `CONVO_MODEL`, `TOOL_MODEL`. See the secrets comment block in `fly.toml`
   for the exact list.
3. At least 50 real conversational turns are run against the deployed worker
   so it appends 50+ records to its latency JSONL (`var/latency.jsonl` in the
   deployed container, per `jarvis.pipeline.latency.LatencyLogger`).
4. That JSONL is retrieved from the deployed instance (e.g. `fly ssh sftp
   get`, a mounted volume, or shipping it to log storage -- mechanism is out
   of scope for T-9) to a local path.

## The exact command that generates the real report

Once a real `latency.jsonl` with >= 50 turns is available locally:

```bash
uv run --directory backend python ../scripts/latency_report.py \
  --input path/to/retrieved/latency.jsonl \
  --min-turns 50 \
  --out docs/latency-report.md
```

(Or, from the repo root, with a suitable `python3` on `PATH`:
`python3 scripts/latency_report.py --input path/to/retrieved/latency.jsonl --min-turns 50 --out docs/latency-report.md`.)

This will overwrite this file with a real report: overall p50/p95/max
time-to-first-audio in milliseconds, the turn count, and a breakdown by
model -- and it will refuse (exit non-zero) if fewer than 50 valid turns are
found, so this placeholder can only ever be replaced by a report backed by
real, sufficient data.
