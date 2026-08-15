# Committed eval reports (T-33)

`docs/evals/latest.json` is the current committed artifact from a real
grounding eval run. This directory exists because commit-message prose is
not verifiable evidence: T-18 and T-19 both cited real-Anthropic eval
results ("PASS: 37 cases", "PASS: 38 cases", 0 fabrications) that existed
nowhere but a commit body -- nothing in the repo backed either claim up,
and there was no way to re-check them without re-spending real API money.

## The convention

**Every real eval run (`--client anthropic`, the default -- never
`--client echo`, and never `--draft-unapproved-not-evidence`) writes a
JSON report with `--json`, and that report is committed alongside the
change it evidences.**

```
cd backend
uv run python -m evals.run --golden evals/golden.jsonl \
    --max-fabrications 0 --require-approved \
    --json ../docs/evals/latest.json
```

- `--json PATH` and the report writer (`_write_json_report`) already
  existed in `evals/run.py` before this ticket -- they were simply never
  wired into a committed path. This directory makes committing the output
  the default, not an opt-in extra step.
- `docs/evals/latest.json` always reflects the most recent real run cited
  as evidence for a ticket. If a later ticket re-runs the eval (a golden-set
  change, a synthesis/prompt change, a model-routing change), the new
  report **overwrites** `latest.json` in the same commit as the change it
  evidences -- there is deliberately no `latest-<date>.json` archive; git
  history already keeps every prior version.
- The report is produced by an unmodified `evals.run --json`. Nothing in
  this repo hand-writes or hand-edits a report; a report that didn't come
  out of a real run is exactly what `scripts/check_eval_report.py`
  (below) exists to catch.
- Model ids are never hardcoded into the run or the report. **T-34: `evals.run`
  now resolves a model PER CASE via `jarvis.llm.routing.route_model`, the
  same function production's tool dispatcher calls** -- a case whose
  fixture(s) fired a `heavy`-tagged tool (`github_query`, `load_repo_digest`)
  scores on `tool_model()` ($TOOL_MODEL); every other case scores on
  `convo_model()` ($CONVO_MODEL). `--model` still exists to pin every case
  to one explicit id instead (an A/B run against a single model). The report
  carries a `provenance` block (see below) recording both resolved ids, plus
  every case's actual routed model, read at runtime from the same
  `jarvis.llm.routing` functions and the real `jarvis.pipeline.tools_wiring`
  registry -- never from env vars re-read independently, and never
  hardcoded.
- **This changes what the numbers mean, compared to every report committed
  before T-34.** Before this change, a default run scored every case --
  including cases whose fixtures came from a `heavy` tool -- against
  `$CONVO_MODEL` only; `$TOOL_MODEL` was resolved and recorded in
  `provenance` but never actually issued an API call, regardless of the
  golden set's content. **The prior committed report's "0 fabrications"
  claim therefore covered `$CONVO_MODEL` only** -- it said nothing about the
  model production actually escalates real heavy-tool turns to. A report
  committed under T-34 or later scores real `$TOOL_MODEL` turns on
  `$TOOL_MODEL`, so its zero-fabrication claim is broader and means
  something a pre-T-34 report's claim did not.

## The `provenance` block

Every report `_write_json_report` writes now has a top-level `provenance`
object alongside `summary`/`cases`, populated at runtime from the same
state the run itself used:

```json
"provenance": {
  "timestamp_utc": "2026-08-15T18:39:10Z",
  "client": "anthropic",
  "convo_model": "claude-haiku-4-5",
  "tool_model": "claude-sonnet-5",
  "model_used": ["claude-haiku-4-5", "claude-sonnet-5"],
  "case_models": {
    "G-001": "claude-haiku-4-5",
    "G-010": "claude-sonnet-5",
    "...": "..."
  },
  "golden_path": "evals/golden.jsonl",
  "golden_approved_sha256": "<64-hex sha256>",
  "golden_content_sha256": "<64-hex sha256>"
}
```

- `client` -- what `--client` resolved to (`anthropic` for a real run,
  `echo` for the no-network self-test). `scripts/check_eval_report.py`
  rejects any committed report whose `client` isn't `anthropic`.
- `convo_model` / `tool_model` -- both resolved via
  `jarvis.llm.routing.convo_model()` / `.tool_model()` regardless of which
  one(s) actually drove this run's cases, so a reader always has both ids;
  either is `null` if its env var was unset when the run happened.
- `case_models` / `model_used` (T-34) -- **`evals.run` now resolves a model
  PER CASE via `jarvis.llm.routing.route_model`, the same function
  production's tool dispatcher calls, mirroring real per-turn escalation
  exactly.** `case_models` is the full `case_id -> model_id` map: every case
  this run scored, and exactly which model (`tool_model()` if the case's
  fixture(s) fired a `heavy`-tagged tool -- `github_query`/`load_repo_digest`
  -- else `convo_model()`; or the single `--model` override id, pinned to
  every case, if one was given) actually scored it. `model_used` is the
  sorted list of distinct values appearing in `case_models` -- a single
  scalar can no longer honestly describe "the model this run used" now that
  a run can legitimately use more than one, so this is a real list, not a
  value that silently means "the first one" (`scripts/check_eval_report.py`
  rejects a report whose `model_used` and `case_models` disagree in either
  direction: a model claimed as used that no case's entry backs up, or a
  case's model that summary never declares). Before T-34, a default run
  resolved ONE model for the whole run and never routed per-case -- see
  "What's currently committed" below for what that means for reports
  committed before this change.
- `golden_approved_sha256` / `golden_content_sha256` -- the golden set's
  `_meta.approved_sha256` and the freshly recomputed content hash
  (`evals.scoring.golden_content_sha256`, imported not reimplemented).
  These are guaranteed equal on an unmodified report from a real run
  (the T-29 approval gate already required that equality before the run
  was allowed to proceed) -- a mismatch in a *committed* report is proof
  it was hand-edited after the fact, and `scripts/check_eval_report.py`
  rejects on exactly that basis.

## Validating a committed report

```
python3 scripts/check_eval_report.py docs/evals/latest.json
```

`scripts/check_eval_report.py` is **stdlib-only** (no third-party imports,
no import from `backend/`) so it runs under bare `python3`, outside the
`uv` environment -- it has to be checkable by something that doesn't
depend on the code it's checking. It rejects a report that is:

- **schema-invalid** -- missing required `summary`/`cases` keys, an
  unknown `verdict`/`kind`/`expect` value, wrong types;
- **internally inconsistent** -- `summary.total_cases` doesn't match the
  actual length of `cases`, a duplicate `case_id`, or
  `summary.totals_by_verdict`/`totals_by_kind` don't match what's
  recomputed by counting the `cases` array itself (this is what catches a
  hand-edited counter, in either direction: the summary edited alone, or a
  case's verdict edited alone while the summary was left stale);
- **truncated or malformed JSON**;
- **missing or invalid `provenance`** -- the block is absent, missing a
  required key, wrong-typed, `timestamp_utc` isn't a UTC ISO-8601
  timestamp, `golden_approved_sha256`/`golden_content_sha256` aren't
  well-formed sha256 hex or don't agree with each other, or `client` isn't
  `anthropic` -- rejected on this basis alone, so a committed `--client
  echo` self-test fails on the provenance field itself, not incidentally
  because echo happens to trip refusal cases;
- **inconsistent per-case routing (T-34)** -- `case_models` is missing, not
  an object, or doesn't cover EXACTLY the report's own `cases` (a missing
  entry, an extra one for a case that doesn't exist); or `model_used` isn't
  a non-empty array of unique non-empty strings; or the set of models named
  in `model_used` doesn't exactly match the set of distinct values in
  `case_models` (a model claimed as used that no case backs up, or a case's
  model the summary never declares) -- either direction is rejected;
- **not a clean run** -- any of fabrications / wrong_refusals /
  missing_include / errors is nonzero, `exit_code != 0`,
  `fail_reasons` is non-empty, `draft` is not `false`, or `evidence` is
  not `true`.

Exit 0 only when the report is both schema-valid and clean. This is also
wired into T-33's own verify command, so a hand-edited or truncated
`docs/evals/latest.json` fails the gate on its own, without needing a live
API call to notice.

## What's currently committed

`docs/evals/latest.json` is a real `--client anthropic` run (no
`--draft-unapproved-not-evidence`), **the first run under T-34's per-case
routing**, against the re-approved golden set (`backend/evals/golden.jsonl`,
`_meta.approved_by` "Hank Holcomb", content-bound via `_meta.approved_sha256`,
T-29). Summary: **38 cases, 0 fabrications, 0 wrong refusals, 0 missing
includes, 0 errors** -- all 38 cases scored `pass` (17 grounded, 14 refusal,
7 memory). Per `provenance` (`--model` not passed, so every case routed
through `jarvis.llm.routing.route_model`): `client` "anthropic",
`convo_model` "claude-haiku-4-5", `tool_model` "claude-sonnet-5",
`model_used` `["claude-haiku-4-5", "claude-sonnet-5"]` -- **both models
actually issued API calls for this run.** `provenance.case_models` shows
exactly which: 16 of the 38 cases (`G-010`-`G-024` -- every `github_query`-
and `github_qa`-labeled case -- plus `G-037`, a mixed weather+github_query
case) fired a `heavy` tool and scored on `claude-sonnet-5`; the remaining 22
scored on `claude-haiku-4-5`. All 16 `claude-sonnet-5` cases passed clean,
alongside all 22 `claude-haiku-4-5` cases -- 0 fabrications on either model.

**This is a materially broader result than every report committed before
T-34.** Before this change, `evals.run` resolved ONE model for the whole
run and never routed per-case through `jarvis.llm.routing.route_model`, so
a default run scored every case -- including the 16 cases above, all of
which fire a real `heavy`-tagged tool in production -- against
`$CONVO_MODEL` only; `$TOOL_MODEL` was resolved and recorded in
`provenance` but never exercised, regardless of the golden set's content.
**The prior committed report's "0 fabrications, 38 cases" claim therefore
covered `$CONVO_MODEL` only** -- it said nothing about the model production
actually escalates real heavy-tool turns to, which is the heaviest,
most fabrication-prone path in the system. (One further correction, found
while implementing this: an earlier assessment of this gap, recorded in
`.claude/NEEDS_HUMAN.md`, said only the 3 `github_qa`-labeled cases fired
the one `heavy` tool. In the actual implementation there are TWO `heavy`
tools, `github_query` and `load_repo_digest` -- `github_qa` is DESIGN.md's
original conceptual name for the combined T-4 capability, not a real
registered tool name -- so 16 cases fire a `heavy` tool, not 3; see
`backend/evals/README.md`'s "Model routing mirrors production" section for
the full mapping.) The current report closes that gap: it is the first
committed result that actually scores real `$TOOL_MODEL` turns on
`$TOOL_MODEL`, so its zero-fabrication claim covers what production
actually does.

This is also the re-run T-18's acceptance 3 asks for ("the eval is
re-run and docs record that no case regressed to `missing_include` for
truncation reasons") -- T-18's own scope had no docs path to record it
in, so it is recorded here instead: `missing_include` is 0 across all 38
cases in `docs/evals/latest.json`, including every `grounded` case whose
answer would truncate mid-sentence and lose a required fact if the
adaptive-thinking token budget were undersized.

## Self-test reports are never committed here

`--client echo` produces a report shaped identically to a real one except
for one field: `provenance.client`. Before the `provenance` block
existed, `summary.draft`/`summary.evidence` alone could NOT distinguish an
echo self-test from a real Anthropic run against an *approved* golden set
(both came back `"draft": false, "evidence": true`; `draft`/`evidence`
only ever detect an *unapproved* golden set, not the client) -- the only
thing that incidentally kept an echo report out of this directory was the
golden set's own content (`EchoModelClient` never emits the model's
`REFUSAL` literal, so every `expect: "refuse"` case scored `fabrication`
under echo, and `scripts/check_eval_report.py` rejected it on that basis
like any other nonzero-failure report). That was a property of this
golden set having real refusal cases, not a guarantee the schema itself
enforced.

That gap is now closed: `provenance.client` records what `--client`
actually resolved to, read at runtime, and `scripts/check_eval_report.py`
rejects any report whose `provenance.client` isn't `"anthropic"` --
directly, regardless of counters. An echo report committed here is now
rejected on the provenance field itself, not incidentally because echo
happens to trip refusal cases. **Still never commit an echo/self-test
report here** -- the checker now enforces that instead of leaving it to a
side effect of this golden set's content; use `--json` against a scratch
path, never a path under `docs/evals/`, for local plumbing checks.
