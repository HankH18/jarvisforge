# Grounding eval harness (T-7)

Scores the real `jarvis.llm.synthesis.synthesize_grounded` against an
approved golden set of grounding questions (SPEC R6). Structural
no-evidence refusal is deterministic and already unit-tested; SEMANTIC
relevance -- whether data that *is* present actually answers *this*
question -- is a model judgment, and this golden set is its enforceable
proxy (SPEC R6a). Every case runs through the unmodified
`synthesize_grounded`; this harness only scores the string that comes back.

## Running it for real

```
cd backend
export ANTHROPIC_API_KEY=...   # required; there is no fallback
uv run python -m evals.run --golden evals/golden.jsonl --max-fabrications 0
```

Exit 0 only when fabrications/wrong_refusals stay within their `--max-*`
budgets (default 0) AND `missing_include == 0` AND no case raised; exit 1
otherwise. If `ANTHROPIC_API_KEY` is unset, the run refuses to start and
exits 2 with an actionable message -- it never silently falls back.

Other flags: `--limit N`, `--model M` (pin every case to this exact id,
overriding routing -- see "Model routing mirrors production" below), `--json
PATH` (full report), `--verbose` (print each case's full answer).

## Model routing mirrors production (T-34)

**This changed what a clean result means.** Before T-34, every case in a run
scored on one model -- `jarvis.llm.routing.convo_model()` (`$CONVO_MODEL`)
by default -- regardless of what that case's fixtures actually fired.
Production never works that way: `jarvis.pipeline.tools_wiring`'s dispatcher
calls `jarvis.llm.routing.route_model([spec])` for the ToolSpec it just ran,
and any `heavy`-tagged tool (`github_query`, `load_repo_digest` -- both
`heavy=True`, see `jarvis.tools.github`) escalates that turn's synthesis
call to `$TOOL_MODEL`. A default run used to score EVERY case -- including
every case whose fixture came from a `heavy` tool -- against `$CONVO_MODEL`
only, so a clean result never actually said anything about `$TOOL_MODEL`'s
grounding behavior, even though production routes real heavy-tool turns to
it.

`evals.run` now resolves a model **per case**, through the same
`jarvis.llm.routing.route_model` production calls (imported, never
reimplemented, never a hardcoded model id): a case whose fixture(s) came
from a `heavy` tool scores on `tool_model()`; every other case scores on
`convo_model()`. Which real tool produced a fixture is identified by the
fixture's `result.source` (the one pinned, structural field -- see
`evals.run._SOURCE_TO_TOOL_NAME`), not by golden.jsonl's free-text `"tool"`
fixture label, which `evals.scoring` never even preserves onto `GoldenCase`
and which is not reliably the real registered tool name to begin with (a
golden case labeled `"github_qa"` -- DESIGN.md's original conceptual name
for the T-4 github capability -- actually carries source `github:gitingest`,
i.e. the real `load_repo_digest` tool).

`--model` still exists as an explicit override: pass it to pin **every**
case to one exact model id (for an A/B run against a single model), same as
before. Omit it and the run routes each case exactly like a live turn would.

**Concretely, on the current golden set:** 16 of 38 cases (every
`github_query`- and `github_qa`-labeled case, plus one mixed case whose
fixtures include a `github_query` result) fire a `heavy` tool and score on
`$TOOL_MODEL`; the remaining 22 score on `$CONVO_MODEL`. Run with `--json`
and inspect `provenance.case_models` to see exactly which model scored which
case for any given report -- see `docs/evals/README.md`.

## Self-test (no API key, no network)

```
uv run python -m evals.run --golden evals/golden.jsonl --client echo --model any-id
```

`--client echo` mirrors the prompt (question + fixture data + prefs) back
verbatim instead of calling a model -- it invents nothing but also can't
judge relevance, so it is **plumbing verification only, never evidence of
grounding**. It prints a loud `SELF-TEST MODE` banner to stderr at start and
end. Use it to confirm the harness itself works; never cite an echo run as
a passing eval.

## Verdicts

- `pass` -- correct refusal, or correct answer with all `must_include`
  present and no `must_not_include`.
- `fabrication` -- answered when it should have refused, or the answer
  (refusal or not) contains a `must_not_include` term.
- `wrong_refusal` -- refused when the fixture data supported an answer.
- `missing_include` -- answered, no forbidden term, but a required fact is
  absent.
- `error` -- `synthesize_grounded` raised; caught so one bad case never
  aborts the run, but any `error` forces a nonzero exit.

`must_not_include` is checked first and unconditionally on every case.

## The approval header

`golden.jsonl` line 1 is a `{"id": "_meta", ...}` object the loader skips.
Its `approved_by`/`approved_at` fields start `null` and must stay that way
until Hank reviews the case set and fills them in. No agent may fill them
in; doing so forges an approval that was never given.

## Approval binds to content (T-29)

`approved_by`/`approved_at` prove WHO approved the set and WHEN -- not that
their review still covers what's currently on disk. `_meta.approved_sha256`
closes that gap: it's a sha256 hash of the file's content (every line
except `_meta` itself -- see `evals.scoring.canonical_approval_bytes` for
the exact byte-for-byte definition). The gate (`evals/run.py`) refuses to
run an otherwise-approved set whose `approved_sha256` is missing, malformed,
or doesn't match the file's current content -- **unverifiable is never
current**, so this fails exactly like a wrong hash would, with exit code 4
and a remedy naming the fix.

After a human reviews an edited golden set and updates `approved_by`/
`approved_at`, mint the new hash and paste it in by hand:

```
cd backend
uv run python -m evals.print_approval_hash --golden evals/golden.jsonl
```

This PRINTS ONLY -- it never writes `golden.jsonl` itself, same as
`approved_by`/`approved_at` above: no agent may fill in `approved_sha256`
either, since doing so would forge a binding between an approval and
content nobody actually reviewed together.
