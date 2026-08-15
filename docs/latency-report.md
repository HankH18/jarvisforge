# Latency report: time-to-first-audio

## 2026-08-15 — FIRST REAL MEASUREMENT. R1 is NOT met, and preemptive TTS is not the reason.

Cartesia was upgraded on 2026-08-15, so for the first time the assistant could actually
speak and be measured. Two things were established.

**1. The product works end to end.** A live LiveKit conversation against a local worker
completed 3/3 turns: real speech in, correct STT (`"What can you actually do?"`), a
grounded manifest-based reply, and real synthesized audio out. Artifact:
`docs/first-live-conversation-2026-08-15.json`. Until this run, every test in this repo
passed against a product that had never been shown to talk.

**2. R1 is missed, in both configurations.** R1 wants no-lookup time-to-first-audio
**p95 < 800 ms**. Measured over 8 no-lookup turns per configuration (`tool_ms == 0`),
chat-only utterance corpus, worker-side `first_audio_ts - turn_end_ts`:

| config | n | median | p95 |
|---|---|---|---|
| preemptive TTS **off** | 8 | 820 ms | **1130 ms** |
| preemptive TTS **on**  | 8 | 867 ms | **1085 ms** |

Raw records: `docs/latency-local-2026-08-15-preemptive-ab.jsonl`.

**The two configurations are indistinguishable.** That matters, because preemptive TTS is
the lever that can cost up to 4× in Cartesia credits (it synthesizes speculatively on every
STT transcript revision and discards the result; Cartesia bills by seconds of *generated*
audio). On this evidence the lever buys no measurable latency and is not worth its cost —
which is why it now defaults off, opt-in via `$JARVIS_PREEMPTIVE_TTS`.

**Honesty about this measurement:** n=8 per configuration is small, and an earlier single
no-lookup sample suggested a ~1000 ms win for preemptive-on that **did not survive a larger
sample** — it was noise. This run also drove a **local** worker on a laptop against LiveKit
Cloud, not the deployed Fly worker in `iad`, so the absolute numbers are not the deployed
numbers. What it does establish: the pipeline works, R1 is missed by roughly 40% locally,
and preemptive TTS is not the fix. A deployed re-measurement is still owed.

---

## T-16 STATUS (2026-08-14): observability fixed + two latency levers landed; deployed re-measurement still blocked on Cartesia credits

T-9's redeploy (see below) found Cartesia TTS returning HTTP 402 ("Model credits limit
reached") on every call. That is still true as of this update -- this ticket did not touch
billing (out of scope: an ops/account fix, not a file in T-16's scope) and had no way to
verify it live. **Acceptance items 2 and 3 (a fresh >=50-turn deployed run, and the R1
p95<=800ms verdict on no-lookup turns) remain unmet, for the same reason as T-9: the deployed
app cannot produce any audio right now, so there is nothing to measure.** This is stated
plainly rather than worked around with old or estimated numbers.

What T-16 *did* do, all of it verifiable offline right now:

### 1. Fixed the observability gap (acceptance #1) -- fixed, tested, one narrow documented residual left (not "done and clean")

Root cause, found by reading the actual call sites (`jarvis.pipeline.session_wiring` /
`jarvis.pipeline.tools_wiring`), not assumed:

- **`tool_ms` always 0.0.** `LatencyLogger.first_audio()` (`backend/src/jarvis/pipeline/latency.py`)
  took a `tool_ms` parameter, but its only caller --
  `session_wiring._on_agent_state_changed`'s `"speaking"` handler -- never passed one:
  `latency.first_audio(ts=ev.created_at)`. There was no path at all from
  `tools_wiring._dispatch` (where a tool actually runs) back to the latency logger. Every
  record, tool-firing or not, got the parameter's hardcoded default: `0.0`.
- **`model` always CONVO_MODEL.** `worker.py`'s `entrypoint()` built the logger once, at
  session start, as `LatencyLogger(model=getattr(session.llm, "model", "unknown"))` --
  `session.llm` is the always-on CONVO_MODEL LLM. `LatencyLogger.model` is then read
  unconditionally by every `first_audio()` call for the rest of the session. A turn that
  fires a `heavy` tool and escalates to `$TOOL_MODEL` via `jarvis.llm.routing.route_model`
  (confirmed in `tools_wiring._dispatch`: `model = route_model([spec])`, passed to
  `synthesize_grounded`) had no way to tell the logger that happened -- the JSONL always
  showed the CONVO_MODEL id regardless.

Fix (`backend/src/jarvis/pipeline/latency.py`, `tools_wiring.py`, `agent.py`, `worker.py`):
`LatencyLogger` gained `note_tool_span(*, tool_ms, model)`, which records the real measured
span and the actually-routed model for the turn currently open; `tools_wiring._dispatch`
times its own `_run_tool_and_synthesize()` span (`time.monotonic()` around the handler +
`synthesize_grounded` call, in a `finally` so it's recorded on every branch: success, a caught
handler exception, or a caught synthesis exception) and calls `note_tool_span()` with that and
`route_model`'s actual decision, before the answer is spoken. `first_audio()` now reports
whatever was recorded for the turn, falling back to the old honest defaults (`0.0` /
`self.model`, the always-on CONVO_MODEL) only when nothing was -- i.e. a real no-tool turn.
`turn_ended()` clears any leftover span from the previous turn so nothing leaks across turns.

**Corrected below (this was under-stated in an earlier pass of this section):** a second bug
existed here, reported by an independent verifier who reproduced it LIVE rather than inferring
it -- and it is **not** a no-lookup-only corner case. Acceptance #1 is not scoped to no-lookup
turns, and this bug specifically broke the `heavy`-tool (lookup, escalated) case: a `heavy`
tool call that outlives `FILLER_THRESHOLD_MS` speaks a filler clip first via the same
`AgentSession.say()`/`"speaking"`-event mechanism the real answer uses (T-15). That transition
fired *before* `note_tool_span()` could possibly run (the filler only fires because the tool
is, by definition, still in flight) and closed the turn's record immediately with the pre-tool
defaults (`tool_ms=0.0`, CONVO_MODEL) -- the real answer's later speech then either found the
turn already closed (a first attempted fix, described below, actually made it worse: no record
at all) or never produced a second SDK event to catch in the first place.
`github_query`/`load_repo_digest` are the **only two `heavy` tools** -- the only ones that ever
escalate `model` -- and the real `github_query` span is measured at ~350ms+~2.7s (well past the
500ms threshold), so this defeated the fix for **essentially every escalated turn**, not a rare
edge case. It directly threatens R1: a misrecorded lookup turn is indistinguishable, from the
JSONL alone, from a fast no-lookup turn -- precisely the population R1's bar is measured over --
so it would have silently flattered any future no-lookup p95 computed from affected data.

Fix, verified against a LIVE reproduction of the exact reported scenario (real `JarvisAgent` +
the real `jarvis.pipeline.cadence_wiring.build_filler_gate`, i.e. production's actual wiring,
with the real pinned `FILLER_THRESHOLD_MS=500ms`, not a test-shortened one -- + the real
`github_query` `ToolSpec` with its HTTP response delayed ~900ms):
`tools_wiring._dispatch` now calls `latency.note_tool_dispatch_started()` the moment it begins
(before the handler even runs). While that flag is set, `first_audio()` recognizes a
"speaking" transition as premature (the filler) and, rather than finalizing the turn with the
pre-tool defaults, **remembers that timestamp** -- it genuinely is the turn's first audible
response (a spoken acknowledgment, R2), only the `tool_ms`/`model` aren't known yet. Once
`note_tool_span()` supplies them, if a filler's timestamp was remembered, it finalizes and
writes the record **right then**, using that remembered timestamp -- there is no reliable
second `"speaking"` transition to wait for (confirmed live: `agent_state_changed` fires on a
state *change*, not once per `session.say()` call, and the real answer's `say()` is queued
while the session is typically already `"speaking"` from the filler). If no filler ever spoke
(fast tools, non-`heavy` tools, or genuine no-tool turns), nothing changes from the first pass.

Actual JSONL record from the live reproduction (`backend/tests/pipeline/test_tool_wiring.py`'s
`test_heavy_tool_with_a_real_filler_still_records_the_real_tool_ms_and_model`, one filler
clip played, ~900ms delayed handler):
```
{"turn_id": 1, "turn_end_ts": 1786739153.952266, "first_audio_ts": 1786739154.47392, "tool_ms": 917.2884169965982, "model": "claude-tool-test"}
```
`first_audio_ts - turn_end_ts` ≈ 522ms (the filler's genuine start, just past the 500ms
threshold, as expected) with the real ~917ms tool span and the escalated `claude-tool-test`
model both correctly captured -- not the filler's own (tool_ms=0.0, CONVO_MODEL) numbers.

Residual, narrower risk (stated in `latency.py`'s docstring, not solved): `note_tool_span()`
clears the in-flight flag as soon as the tool's own span is known, which happens before
`FillerGate.guard()`'s own `finally` finishes awaiting/cancelling its timer task. If the SDK's
delivery of the filler's `"speaking"` event were somehow delayed past that point -- not
observed, and narrower than the original bug by orders of magnitude (that one fired on
literally every real `github_query` call past 500ms) -- the now-cleared flag could let a late
event finalize early. Not fixable without either widening `tools_wiring`'s role or reaching
into `jarvis.cadence.gate` (out of this ticket's file scope -- a genuine plan defect if
required; not required for the fix above). This residual risk is exactly why
`scripts/latency_report.py` (below) still treats a `tool_ms`-only inference as unverified
rather than trusting it blindly.

**Third bug, also found live by an independent verifier reproducing it rather than inferring
it: cancellation during the handler await could still silently drop a turn even after the
filler fix above.** The `finally` that called `note_tool_span()` wrapped only the SECOND
`await` (`synthesize_grounded`), not the first (`spec.handler`). `asyncio.CancelledError` is a
`BaseException`, not caught by `except Exception` -- so a cancellation while still inside the
handler's `asyncio.to_thread` await propagated straight out of `_run_tool_and_synthesize()`
with **no record ever written at all**, even for a turn where a filler had already spoken (a
real, audible response the user genuinely heard). A silently missing turn is worse for R1 than
a wrong one: it removes a slow sample from the population entirely rather than merely
mis-describing it -- exactly the class of failure the section above claims to have eliminated.
Reachability, as established by the verifier: the SDK runs each dispatch as a plain
`asyncio.create_task` independent of `SpeechHandle._tasks`; explicit tool cancellation needs
`ToolFlag.CANCELLABLE`, which `livekit_tools` does not set; and a graceful `session.aclose()`
does **not** reproduce it (`drain()` awaits non-cancellable tasks to completion -- verified: a
3s handler mid-flight still recorded `tool_ms≈3021ms`). The realistic trigger is an external
force-cancellation of the whole task tree, e.g. a shutdown grace-period timeout -- narrow, but
real, unguarded, and untested before this fix.

Fix (`tools_wiring._dispatch`'s `_run_tool_and_synthesize()`): `model = route_model([spec])` is
now resolved up front, before either `await` -- it is a pure function of `spec.heavy` + env
vars, independent of the handler's outcome, so there was no reason to defer it. Both awaits are
now inside ONE outer `try/finally`: `finally` always runs on every exit (normal return, a
caught `Exception` from either inner block, or an uncaught `CancelledError` propagating
through) and calls `note_tool_span()` unconditionally; a bare `finally` never swallows the
exception it is unwinding from, so cancellation still reaches the caller exactly as before --
T-17's cancellation semantics are untouched, nothing here catches or suppresses
`CancelledError`. A cancelled turn's `tool_ms` is therefore a REAL partial span (however long
the tool ran before being cut off) and `model` is the real routed decision -- never a
fabricated completion (per instruction: honest partial data, not an invented completion).
**Whether a record belongs to a cancelled turn is not expressible in the pinned 5-field
schema** (`turn_id, turn_end_ts, first_audio_ts, tool_ms, model`) -- there is no field to mark
it, and neither adding one (`docs/DESIGN.md` §Data models is outside this ticket's scope, as
established earlier in this document) nor overloading `tool_ms`/`model` with a sentinel (which
would corrupt their real meaning for `scripts/latency_report.py`'s legitimate aggregation) is
acceptable. Stated plainly rather than worked around: a cancelled-but-recorded turn is
indistinguishable, in the JSONL, from an ordinary slow completed one.

Bound by `backend/tests/pipeline/test_cadence_wiring.py`'s
`test_cancellation_during_the_handler_await_still_records_a_real_tool_span`: a heavy tool's
handler is force-started on a worker thread (synchronized via a `threading.Event`, not a fixed
sleep), a filler "speaks" for the turn (`latency.first_audio()`, mirroring what the real
`FillerGate` triggers), the dispatch task is then cancelled while still inside the handler
await, and the test asserts (1) a record IS written, with a real `tool_ms > 0` and the real
routed `model`, and (2) `CancelledError` still propagates to the caller (`pytest.raises`).
Verified to FAIL against the pre-fix code first: `(tmp_path / "latency.jsonl").exists()` was
`False` -- the file was never even created, proving the record was dropped, not just wrong --
then verified to pass after the fix.

Tests (`backend/tests/pipeline/test_latency.py`, new; `test_tool_wiring.py` and
`test_cadence_wiring.py`, extended): 9 unit tests drive `LatencyLogger` directly (recorded span
reported, no-leak across turns, no-op with no open turn, explicit-argument override, a
"speaking" transition mid-dispatch declines to finalize, the deferred timestamp finalizes
correctly once the tool span is known, a fast tool with no filler still finalizes on its own
transition, the new flags don't leak across turns either), 3 integration tests drive the REAL
`tools_wiring._dispatch` end-to-end (github_query = heavy/no gate, get_weather = non-heavy/no
gate, and github_query = heavy WITH the real production `FillerGate`) through a real
`AgentSession`, and 1 more integration test drives `_make_dispatcher` directly through a
force-cancelled task. All were verified to FAIL against the pre-fix code first: the initial 4/5
unit-test failures were `AttributeError: no attribute 'note_tool_span'`; the initial 2
integration tests failed with `TypeError: unexpected keyword argument 'latency'`; the
real-`FillerGate` integration test, run against the code as of this ticket's *first* pass
(`note_tool_span` existed but not the dispatch-pending/deferred-timestamp fix), failed with
`AssertionError: ... got 0.0`; the cancellation test, run against the code as of this ticket's
*second* pass (the filler race was fixed but the handler-await `finally` gap was not), failed
with a missing JSONL file entirely. All 13 pass after the fixes above.

`scripts/latency_report.py`'s "By lookup status" section still reports a `tool_ms`-inferred
lookup/no-lookup split whenever `--driver-json` isn't given, but -- per the residual risk above,
and because the record schema is pinned to exactly `{turn_id, turn_end_ts, first_audio_ts,
tool_ms, model}` in docs/DESIGN.md §Data models (outside this ticket's file scope, so no
`lookup` field was added to make the distinction machine-readable) -- that section is now
**diagnostic-only and never emits an R1 PASS/FAIL verdict**: an unverified `tool_ms<=0` must
not masquerade as a confirmed fast sample. `--driver-json` (ground truth: which utterance was
actually asked) is required for a certified no-lookup-only R1 verdict; the script also prints a
loud stderr note naming how many turns were classified by inference alone. Verified with new
tests (`scripts/tests/test_latency_report.py`) driving `_render_report()` directly: a mix
including a filler-raced-looking record (`tool_ms=0.0`, fast `ttfa`) is still shown in the
diagnostic breakdown but never certifies a PASS/FAIL verdict without `--driver-json`; with
`--driver-json`, a real verdict is still produced.

### 2. Two latency levers landed, each with evidence -- code + offline tests only, not a live measurement

**a) LiveKit preemptive TTS** (`backend/src/jarvis/pipeline/worker.py`): originally landed as
`AgentSession` built unconditionally with
`turn_handling=TurnHandlingOptions(preemptive_generation={"preemptive_tts": True})`.
Evidence: read directly from the installed SDK (`livekit-agents` 1.6.10,
`livekit/agents/voice/turn.py`). Preemptive generation is already on by default
(`enabled: True`), but with `preemptive_tts: False` (the default) only the LLM call starts
early, on a not-yet-final transcript -- TTS still waits for endpointing to officially confirm
the turn. `preemptive_tts: True` lets TTS *also* start synthesizing before confirmation, so by
the time `latency.turn_ended()` opens the record (the moment endpointing actually confirms the
turn), synthesis may already be underway or done. This only affects the no-tool path (a
fired tool bypasses the SDK's own TTS scheduling via `tools_wiring._dispatch`'s direct
`session.say()`), i.e. exactly R1's population. Not independently measurable right now (would
require a live session).

**2026-08-15 UPDATE (preemptive change only -- measurements above and below are untouched):**
this lever is now OFF by default. It was one of the causes of a Cartesia credit-burn incident
(`.claude/NEEDS_HUMAN.md`, "ANSWERED -- where the free Cartesia credits went"): Cartesia bills
by seconds of *generated* audio, and `preemptive_tts: True` re-synthesizes on every not-yet-final
STT transcript revision, discarding-but-still-paying-for whatever gets cancelled -- worst case up
to the SDK's `max_retries` (default 3) discarded syntheses plus the one real one per turn. Since
this remains a genuine, real R1 lever, it was made env-driven rather than deleted:
`$JARVIS_PREEMPTIVE_TTS=1` re-enables it (with the SDK's speculative-retry cap also lowered from
3 to 1 in code, halving the worst case), so it stays available for a future A/B measurement once
Cartesia credits allow one. Locked down by
`test_entrypoint_disables_preemptive_tts_by_default` and
`test_entrypoint_enables_preemptive_tts_via_env_var`
(`backend/tests/pipeline/test_worker_entrypoint.py`, replacing the old
`test_entrypoint_enables_preemptive_tts`). No new latency measurement was taken for this change
(still blocked on Cartesia credits, same as everything else in this report).

**b) Cartesia: stop requesting unused word-level timestamps**
(`backend/src/jarvis/pipeline/providers.py`): `build_tts()`'s Cartesia branch now passes
`word_timestamps=False`. The installed plugin defaults `word_timestamps=True`, which asks
Cartesia to compute and stream word-level alignment data alongside every audio chunk --
including the first one, on the exact critical path this ticket is trying to shrink. This repo
never consumes that data: `AgentSession`'s own `use_tts_aligned_transcript` (the one setting
that would read it) is left at its default, which the installed SDK's own docstring states is
disabled ("When NOT_GIVEN, it's disabled"), and the assistant transcript published on the data
channel comes from the chat_ctx item's text (`session_wiring._on_conversation_item_added`), not
a TTS-provided transcript. This is "stop paying for work nothing uses", not a claimed speed
number -- Cartesia is unmeasurable right now, so the actual ms saved cannot be verified until
credits are restored. Locked down by
`test_cartesia_does_not_request_unused_word_timestamps` (`backend/tests/pipeline/test_tts_selection.py`).

**What was investigated and NOT changed, stated honestly rather than invented:**

- *"Removing any avoidable round-trip on no-tool turns"*: no such round-trip was found.
  CONVO_MODEL's tool schemas are attached to the same single streamed Anthropic call that
  either emits a tool call or plain text (Anthropic's tool-use API does this in one
  round trip, not two); a no-tool turn never touches `tools_wiring._dispatch` at all. Checked
  and ruled out as the reason `scripts/drive_turns.py`'s 55 turns might look artificially slow:
  `TurnDriver` connects to the room ONCE and drives all 55 turns through that one session (see
  `scripts/drive_turns.py`), so this is not 55x cold-start/connection-setup cost hiding in the
  historical numbers.
- *Cartesia model swap (e.g. to `sonic-turbo`)*: considered, not done. Cartesia's current
  public docs (checked live during this ticket) describe latency only for `sonic-3.5`, a model
  the installed plugin (`livekit-plugins-cartesia`, `TTSModels` literal) does not even support;
  no latency comparison between the plugin-supported models (`sonic`, `sonic-2`, `sonic-turbo`,
  `sonic-3`) could be found in Cartesia's docs or the plugin's own source. Changing the model id
  without evidence would be exactly the "claimed a latency win with no measurement" mistake this
  ticket was warned against.
- *Endpointing (`min_delay`/turn-detection) tuning*: not touched. The installed SDK already
  defaults to `inference.TurnDetector()` (its own streaming semantic turn detector) unless
  overridden, which resolves to the tighter streaming endpointing defaults
  (`min_delay=0.3s`, vs `0.5s` for the legacy/non-streaming default) -- this repo passes no
  `turn_detection` override, so it already gets the tighter defaults. Tuning it further blind,
  with TTS down and no way to verify against real barge-in behavior, risks a correctness
  regression (the agent cutting users off mid-sentence) for an unverified latency gain -- not a
  "cheap lever," a live-tuning exercise that needs the pipeline working end-to-end.

### 3. Local (non-deployed) proxy measurement, per the ticket's explicit allowance

TTS_PROVIDER supports a non-Cartesia path (`aura2`, Deepgram Aura-2 -- SPEC C2's documented
fallback), so a real, unmocked local measurement is possible even with Cartesia down:
`scripts/local_ttfb_proxy.py` (new, committed) makes real network calls -- a streaming
Anthropic Messages call to `$CONVO_MODEL` (time to first content delta) and a real Deepgram
Aura-2 TTS call (time to first response byte) -- and reports their sum as a proxy for
turn_end -> first-audio on a no-lookup turn. **This is explicitly NOT the R1 deployed figure**:
it runs from this development machine (not the Fly worker), has no LiveKit hop, no STT, no
preemptive-generation overlap, and uses Aura-2 instead of Cartesia (SPEC's actual TTS pick) --
see the script's own docstring for the full disclaimer, repeated in its own console output.

Two runs, `uv run python scripts/local_ttfb_proxy.py --samples 15` and `--samples 25`,
2026-08-14, from this development sandbox:

| run | leg | p50 (ms) | p95 (ms) | max (ms) |
| --- | --- | --- | --- | --- |
| n=15 | LLM time-to-first-token | 634.3 | 5005.5 | 7589.0 |
| n=15 | TTS (Aura-2) time-to-first-byte | 263.8 | 301.0 | 309.5 |
| n=15 | sum | 960.0 | 5267.2 | 7827.8 |
| n=25 | LLM time-to-first-token | 1162.1 | 6766.4 | 8152.6 |
| n=25 | TTS (Aura-2) time-to-first-byte | 255.9 | 324.2 | 327.6 |
| n=25 | sum | 1404.3 | 7031.4 | 8480.2 |

**Read the p50s, not the p95s/max.** Both runs show a handful of multi-second LLM
time-to-first-token outliers (e.g. 7.5-8.5s) with no equivalent in the historical PRODUCTION
data below (chat p95=1447.9ms, nothing near multi-second) -- almost certainly this
development sandbox's own network egress to `api.anthropic.com` (shared/proxied/throttled in a
way the Fly-hosted worker's network path is not), not a property of CONVO_MODEL or the
pipeline. The p95/max columns are reported anyway, unfiltered, rather than dropped as
inconvenient.

**What the stable part of this local proxy DOES support, as a directional (not authoritative)
signal:** the TTS leg is small and consistent (~220-330ms) across both runs; the LLM leg is
larger and the dominant cost even at p50 (634-1162ms, already comparable to or exceeding R1's
entire 800ms budget on its own, before any TTS time is added). This is directionally
consistent with the historical deployed data below, where "chat" (no-lookup) turns already ran
p50=821.4ms overall -- i.e. the CONVO_MODEL round trip, not TTS, looks like the larger no-lookup
cost, for both this local proxy and the last real deployed sample. Nothing here proves that on
its own (small sample, one uncontrolled network path) -- it is a reason to look at LLM-call
latency (connection reuse, streaming setup, `max_tokens`/no-`thinking` config) as the next
thing to measure once Cartesia is back, not a conclusion.

### Exactly what to re-run once Cartesia credits are restored

1. Confirm Cartesia works: `flyctl ssh console -a jarvisforge --machine <worker-machine-id>` and
   watch `flyctl logs -a jarvisforge` during one live turn for the absence of the `402` seen
   below, or just run step 2 and check it produces `>0` completions.
2. `uv run python scripts/drive_turns.py --turns 55 --token-url https://jarvisforge.fly.dev
   --utterances scripts/utterances --timeout 30 --json /tmp/drive-turns-out.json` (from
   `backend/`, or adjust `uv run` accordingly) against the live deployment.
3. Pull the worker's JSONL before the machine restarts/redeploys (it is NOT persisted --
   no `[mounts]` in `fly.toml`, see "Where deployed latency records actually come from" below):
   `flyctl ssh sftp get /app/var/latency.jsonl ./deployed-latency.jsonl -a jarvisforge`
4. `python3 scripts/latency_report.py --input ./deployed-latency.jsonl --driver-json
   /tmp/drive-turns-out.json --out docs/latency-report.md`. With `--driver-json` given, the
   report's "By question type" table is the one that matters -- it is ground-truth-based and
   the only one that certifies an R1 PASS/FAIL verdict (see "2. Two latency levers..." above
   for why `scripts/latency_report.py`'s `tool_ms`-inferred fallback table, shown only when
   `--driver-json` is *not* given, deliberately never does). Sanity check: with the `tool_ms`
   fix, a driver-json-less run of the SAME input (`--input ./deployed-latency.jsonl` alone, no
   `--driver-json`) should show a similar (not necessarily identical -- it's an inference, not
   ground truth) no-lookup count/p95 in its diagnostic "By lookup status" table -- worth
   comparing the two, not just trusting either blindly.
5. Replace this whole T-16 section (and the T-9 section below it) with the fresh report; keep
   the "Historical measurement" section below only if the new run also fails R1, for trend
   continuity, otherwise it can finally be retired.

### R1 status (T-16, current code): CANNOT BE MEASURED on the deployed pipeline (Cartesia still down); local proxy p50 (960-1404ms, sum) suggests the gap is still real, not proof either way

Acceptance #3 asks this report to state the achieved figure and the specific bottleneck with
evidence when the bar can't be verified met. There is no *deployed* achieved figure --
Cartesia's 402 makes that impossible to produce right now (0 completions, exactly like T-9's
attempt below). The best available evidence is: (a) the historical last-valid deployed sample
(below) already failed R1 on no-lookup turns at p95=1447.9ms/p50=821.4ms, n=20; (b) today's two
local, non-Cartesia proxy runs put the LLM-call leg alone at p50 634-1162ms already, with TTS
adding a further ~250-330ms on top in the stable case -- consistent with (a) rather than
contradicting it. The specific remaining bottleneck, by elimination and by (b): **the CONVO_MODEL
round trip itself**, not TTS and not an avoidable pipeline round-trip (both investigated above).
Neither of this ticket's two levers targets that leg directly (preemptive TTS overlaps TTS with
confirmation, not the LLM call's own latency; the Cartesia change removes unused alignment
work, not model latency) -- so even with both landed, closing R1 fully likely needs a next step
aimed at the LLM leg specifically (e.g. prompt caching, connection warm-start, or confirming
CONVO_MODEL is actually the fastest available id) -- named here as the next lever, not
attempted, since it cannot be verified without Cartesia working end-to-end anyway.

---

## T-9 STATUS (2026-08-14, redeploy of current HEAD at the time): NO VALID MEASUREMENT -- TTS IS DOWN

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
and is **not attempted here** -- flagged for immediate human attention instead. **Still true as
of T-16** -- not re-attempted or re-verified live during this ticket either, for the same
reason (ops/billing, not a code file).

**No ttfa sample exists from this fresh attempt.** Per instruction, this is reported as a
stated gap, not estimated or backfilled from the historical run below.

### R1 status (T-9 deployment): CANNOT BE MEASURED (TTS non-functional; separately, still FAIL on the last valid data)

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

R1 pins 800ms for replies that need **no external lookup**. At the time this was written,
`tool_ms` was 0.0 even when a tool fired (the T-16 bug, later found to be worse than first
understood -- see "1. Fixed the observability gap" above), so this split came from the
driver's utterance categories instead. T-16 fixed `tool_ms`, but `--driver-json` (ground
truth) is still required for a certified R1 verdict either way -- `scripts/latency_report.py`'s
`tool_ms`-inferred fallback (shown only when `--driver-json` isn't given) is diagnostic-only
on purpose, never a verdict on its own. Kept as originally written here since this section is
historical.

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

Note (T-16): this table shows every one of the 55 historical turns attributed to
`claude-haiku-4-5` -- exactly the `model`-always-CONVO_MODEL bug this ticket fixed. Some of
those 55 turns were `github`/heavy-tool turns that should have escalated to `$TOOL_MODEL`; this
table is retained unedited as the historical record of what the bug actually produced, not
corrected retroactively (there is no way to recover the real per-turn model from this old data).

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
