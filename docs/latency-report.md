# Latency report: time-to-first-audio

Source: `/tmp/deployed-latency.jsonl`
Turns analyzed: 55 (minimum required: 50)

## Overall

| metric | ms |
| --- | --- |
| p50 | 974.9 |
| p95 | 5957.4 |
| max | 14388.3 |
| count | 55 |

R1 requires p95 <= 800ms over >= 50 turns.
R1 status (all turns): FAIL (p95=5957.4ms, n=55)

## By question type

R1 pins 800ms for replies that need **no external lookup**. The worker log cannot distinguish those on its own (`tool_ms` is 0.0 even when a tool fires), so the split below comes from the driver's utterance categories.

| category | count | p50 (ms) | p95 (ms) | max (ms) | external lookup |
| --- | --- | --- | --- | --- | --- |
| capability | 10 | 2794.4 | 11682.1 | 14388.3 | yes |
| chat | 20 | 821.4 | 1447.9 | 2082.4 | no |
| github | 9 | 1648.0 | 2988.0 | 3183.1 | yes |
| memory | 8 | 365.1 | 6693.0 | 7428.5 | yes |
| weather | 8 | 2662.7 | 4693.2 | 4803.0 | yes |

**R1 status (no-lookup turns only): FAIL** (p95=1447.9ms, p50=821.4ms, n=20).

## By model

| model | count | p50 (ms) | p95 (ms) | max (ms) |
| --- | --- | --- | --- | --- |
| claude-haiku-4-5 | 55 | 974.9 | 5957.4 | 14388.3 |

## Raw data (T-26)

The 55-turn JSONL this report was generated from (`Source` above) lived only
at `/tmp/deployed-latency.jsonl` on the machine that ran the report --
`/tmp` is not durable or committed, so that measurement history would have
been lost the next time the machine rebooted or the file got cleaned up.
T-26 copied it byte-for-byte into this repo at
[`docs/latency-deployed-2026-08-14.jsonl`](./latency-deployed-2026-08-14.jsonl)
(55 lines, same content that produced the stats above) so the raw evidence
behind R1's FAIL verdict survives independent of any one machine's `/tmp`.
To regenerate this report from the committed copy:

```
uv run python scripts/latency_report.py --input ../docs/latency-deployed-2026-08-14.jsonl
```

(`backend/var/latency.jsonl`, `scripts/latency_report.py`'s *default*
`--input`, is a from-a-local-dev-run file, not this data -- see "Where
deployed latency records actually come from" below.)

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
local dev run happened to write.

`fly.toml` declares no `[mounts]` for either the `worker` or `web` process
group, so `/app/var/latency.jsonl` lives on the machine's ephemeral local
disk only: it is **not** persisted across a machine restart or redeploy, and
there is no automatic export path. To collect it, pull it off the running
worker machine before it restarts, e.g.:

```
fly ssh sftp get /app/var/latency.jsonl ./deployed-latency.jsonl -a jarvisforge
```

(or `fly ssh console -a jarvisforge --pty` into the `worker` process group
and `cat`/copy it manually). That retrieval step -- not anything
`scripts/latency_report.py` does automatically -- is how
`/tmp/deployed-latency.jsonl` (and now
`docs/latency-deployed-2026-08-14.jsonl`) was produced for this report; the
script's own default `--input` never sees deployed data unless it is
pointed explicitly at wherever that pulled file ends up.
