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
