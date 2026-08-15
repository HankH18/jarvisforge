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
- Model ids are never hardcoded into the run or the report. `evals.run`
  resolves the model via `jarvis.llm.routing.convo_model()` unless
  `--model` overrides it -- i.e. `$CONVO_MODEL` (and, when a run
  deliberately exercises the `$TOOL_MODEL` escalation path via `--model
  $TOOL_MODEL`, that instead). The report now carries a `provenance` block
  (see below) recording both resolved ids at runtime, read from those same
  `jarvis.llm.routing` functions -- never from env vars re-read
  independently, and never hardcoded.

## The `provenance` block

Every report `_write_json_report` writes now has a top-level `provenance`
object alongside `summary`/`cases`, populated at runtime from the same
state the run itself used:

```json
"provenance": {
  "timestamp_utc": "2026-08-14T21:03:47Z",
  "client": "anthropic",
  "convo_model": "claude-haiku-4-5",
  "tool_model": "claude-sonnet-5",
  "model_used": "claude-haiku-4-5",
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
  one actually drove this run's cases, so a reader always has both ids;
  either is `null` if its env var was unset when the run happened.
- `model_used` -- the model id `evals.run` actually passed to
  `synthesize_grounded` for every case in this run (`--model` if given,
  else `convo_model()`). **`evals.run` resolves one model for the whole
  run and does not route per-case through `jarvis.llm.routing.route_model`**
  -- so even though the golden set has `github_qa` fixtures (the one tool
  DESIGN.md marks `heavy`, i.e. the escalation path to `$TOOL_MODEL`), a
  default run never actually calls the Anthropic API with `$TOOL_MODEL`;
  it scores every case, including the `github_qa` ones, against
  `$CONVO_MODEL`. `tool_model` is still recorded above for completeness,
  not because this run exercised it.
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
`--draft-unapproved-not-evidence`) against the re-approved golden set
(`backend/evals/golden.jsonl`, `_meta.approved_by` "Hank Holcomb",
content-bound via `_meta.approved_sha256`, T-29). Summary: **38 cases, 0
fabrications, 0 wrong refusals, 0 missing includes, 0 errors** -- all 38
cases scored `pass` (17 grounded, 14 refusal, 7 memory). Per
`provenance` (`--model` not passed): `client` "anthropic", `convo_model`
"claude-haiku-4-5", `tool_model` "claude-sonnet-5", `model_used`
"claude-haiku-4-5" -- i.e. `$CONVO_MODEL` is the only model that actually
issued API calls for this run. `evals.run` resolves ONE model for the
whole run rather than routing per-case through
`jarvis.llm.routing.route_model`, so `$TOOL_MODEL` is resolved and
recorded but not exercised here, even though the golden set has
`github_qa` fixtures (the tool DESIGN.md marks `heavy`); see the
`provenance` section above.

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
