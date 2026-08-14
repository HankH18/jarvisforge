# Latency report: time-to-first-audio

## CURRENT STATUS (2026-08-14, T-9 redeploy of current HEAD): NO VALID MEASUREMENT -- TTS IS DOWN

T-9 redeployed the app to `jarvisforge:deployment-01M00RH4J2ZX03G09XVZHME4KW` (Fly release
v7, all 4 machines confirmed on version 7), carrying T-14 through T-27 (including T-26's
filler-bank packaging fix, independently confirmed loaded on the live worker: `ASSETS_DIR`
resolves to `/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers`,
`FillerBank.from_manifest()` returns 6 clips).

An attempt was made to capture a fresh >=50-turn deployed latency sample per this ticket's
acceptance item 3, using `scripts/drive_turns.py --turns 55 --token-url
https://jarvisforge.fly.dev --utterances scripts/utterances --timeout 30`.

**Result: 0 of 55 turns completed. Every turn timed out waiting for first audio.** Three
follow-up single-turn diagnostics reproduced this (one with `--timeout 120` -- 120s, not a
slow tail, a hang), and a direct `flyctl ssh console -a jarvisforge --machine 2862ee5bd19558`
onto the running worker found `/app/var/latency.jsonl` -- the file
`jarvis.pipeline.latency.LatencyLogger` only creates on its first successful write of a
`first_audio_ts` -- **does not exist at all** on that container. That confirms the failure is
not a client-side detection gap in the test harness: the worker itself never recorded a single
completed turn.

**Root cause, found via `flyctl logs -a jarvisforge` live-tailed during a diagnostic turn:**
Cartesia TTS synthesis fails immediately on every turn with

```
livekit.agents._exceptions.APIStatusError: message='Invalid response status', status_code=402, retryable=False
  File ".../livekit/plugins/cartesia/tts.py", line 232, in _connect_ws
    raise APIStatusError(...)
```

HTTP 402 = Payment Required. This is thrown while opening the Cartesia synthesis websocket,
before any audio is produced, so no `agent_state: speaking` event is ever published and no
audio track is ever fed -- the agent registers, joins the room, transcribes speech (STT) and
in some turns even produces LLM response text (e.g. "Let me check my capabilities for you."),
but can never speak it. This reproduced 6 times across the diagnostic turns alone.

This reads as **Cartesia account credit/billing exhaustion, not a code regression** from
T-14..T-27 -- there is no code path in this repo that could produce an upstream 402. The
previous measurement below (13+ hours earlier, same Cartesia key) completed all 55 turns with
real audio, so whatever exhausted the account happened sometime after that run and before this
one; the timing looks incidental to this deploy rather than caused by it, but that can't be
proven from application logs alone.

**Practical severity: this is a production outage of the entire spoken-response path, not
merely a latency shortfall.** With TTS returning 402 on every call, the deployed app cannot
speak any response right now, regardless of how fast the rest of the pipeline is. Fixing it
(refilling/repairing the Cartesia account's billing, or rotating `CARTESIA_API_KEY` to an
account with credit) is outside this ticket's scope (ops/billing, not a file in T-9's scope)
and is **not attempted here** -- flagged for immediate human attention instead.

**No ttfa sample exists from this fresh attempt.** Per instruction, this is reported as a
stated gap, not estimated or backfilled from the historical run below.

### R1 status (current deployment): CANNOT BE MEASURED (TTS non-functional; separately, still FAIL on the last valid data)

## Historical measurement (superseded -- kept for reference only, NOT current evidence)

The following is the last *valid* time-to-first-audio data this repo has. It was captured
~13-24 hours before the T-9 redeploy above, against an older deployed image (pre T-14..T-27),
when Cartesia TTS was still working. It no longer describes the current deployment's
performance and is retained only so R1's historical trend isn't lost; do not cite it as
current.

Source: `docs/latency-deployed-2026-08-14.jsonl` (committed by T-26; originally
`/tmp/deployed-latency.jsonl`)
Turns analyzed: 55 (minimum required: 50)

### Overall (historical)

| metric | ms |
| --- | --- |
| p50 | 974.9 |
| p95 | 5957.4 |
| max | 14388.3 |
| count | 55 |

R1 requires p95 <= 800ms over >= 50 turns.
R1 status (all turns, historical data): FAIL (p95=5957.4ms, n=55)

### By question type (historical)

R1 pins 800ms for replies that need **no external lookup**. The worker log cannot distinguish those on its own (`tool_ms` is 0.0 even when a tool fires), so the split below comes from the driver's utterance categories.

| category | count | p50 (ms) | p95 (ms) | max (ms) | external lookup |
| --- | --- | --- | --- | --- | --- |
| capability | 10 | 2794.4 | 11682.1 | 14388.3 | yes |
| chat | 20 | 821.4 | 1447.9 | 2082.4 | no |
| github | 9 | 1648.0 | 2988.0 | 3183.1 | yes |
| memory | 8 | 365.1 | 6693.0 | 7428.5 | yes |
| weather | 8 | 2662.7 | 4693.2 | 4803.0 | yes |

**R1 status (no-lookup turns only, historical data): FAIL** (p95=1447.9ms, p50=821.4ms, n=20).

### By model (historical)

| model | count | p50 (ms) | p95 (ms) | max (ms) |
| --- | --- | --- | --- | --- |
| claude-haiku-4-5 | 55 | 974.9 | 5957.4 | 14388.3 |

## Bottom line for this ticket (T-9)

- Acceptance item 3 (p95 <= 800ms over >= 50 deployed turns) is **NOT MET**, and as of this
  redeploy it is **not currently re-measurable** until the Cartesia TTS 402 is resolved
  (separate from this ticket's scope).
- R1 was already known-FAIL on the last valid data (p95=5957.4ms overall, p95=1447.9ms even
  restricted to no-lookup turns) -- **T-16 ("Meet R1: cut time-to-first-audio below 800ms
  p95") owns closing that gap.**
- The newly-discovered Cartesia 402 is a distinct, more urgent problem (nothing can be spoken
  at all right now) that blocks even collecting the data T-16 would need, and should be
  escalated/fixed before T-16's latency work can be usefully measured against production again.

## Where deployed latency records actually come from

`jarvis.pipeline.latency.LatencyLogger.path` defaults to the *relative*
path `var/latency.jsonl` (`backend/src/jarvis/pipeline/latency.py`), and
`scripts/latency_report.py --input` defaults to the local repo path
`backend/var/latency.jsonl` -- both assume whatever process runs the worker
has its cwd inside a checkout of this repo. That is true for a developer's
`uv run` from `backend/`, but **not** for the deployed worker: per
`fly.toml`, the `worker` process group runs `python -m
jarvis.pipeline.worker start` inside the image built from
`backend/Dockerfile`, whose runtime stage sets `WORKDIR /app` (and does not
override the cwd). So on Fly, `LatencyLogger` writes to **`/app/var/latency.jsonl`
inside the worker machine's own container filesystem** -- never to
`backend/var/latency.jsonl` in this repo, which only ever holds whatever a
local dev run happened to write. This is also exactly the file this ticket
checked for directly (via `flyctl ssh console --machine <id>`) to confirm the
fresh run above produced zero completions server-side, not just client-side.

`fly.toml` declares no `[mounts]` for either the `worker` or `web` process
group, so `/app/var/latency.jsonl` lives on the machine's ephemeral local
disk only: it is **not** persisted across a machine restart or redeploy, and
there is no automatic export path. To collect it, pull it off the running
worker machine before it restarts, e.g.:

```
flyctl ssh sftp get /app/var/latency.jsonl ./deployed-latency.jsonl -a jarvisforge
```

(or `flyctl ssh console -a jarvisforge --machine <worker-machine-id> --pty` and
`cat`/copy it manually -- `flyctl ssh console` without `--machine` can land you
on either process group, which matters for this file since only the worker
writes it). That retrieval step -- not anything `scripts/latency_report.py`
does automatically -- is how the historical data above was produced; the
script's own default `--input` never sees deployed data unless it is pointed
explicitly at wherever that pulled file ends up.
