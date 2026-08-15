"""CLI: score `jarvis.llm.synthesis.synthesize_grounded` against the T-7
approved golden set (SPEC R6/R6a). See `backend/evals/README.md`.

Run as `uv run python -m evals.run --golden evals/golden.jsonl` from
`backend/`. `jarvis` is not an installed package -- pytest gets away with
importing it only because pyproject.toml sets `pythonpath = ["src"]`, but
`python -m` gets no such help, so this file puts `backend/src` on
`sys.path` itself below, resolved from `Path(__file__).resolve()` so it
works regardless of the process's cwd.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

# backend/evals/run.py -> parents[1] == backend/. Resolved from __file__, not
# cwd (pinned in the T-7 contract). Must happen before any `jarvis` import.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_SRC = _BACKEND_ROOT / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

# The ticket's verify command is a bare `uv run python -m evals.run ...`, so it
# has to work without the caller having sourced anything -- including when the
# TaskCompleted hook runs it with an otherwise empty environment. Load the repo
# .env if one exists; an already-exported value always wins, so CI and one-off
# overrides (CONVO_MODEL=... uv run ...) still behave as written.
_DOTENV = _BACKEND_ROOT.parent / ".env"
if _DOTENV.is_file():
    from dotenv import load_dotenv

    load_dotenv(_DOTENV, override=False)

from evals.scoring import (  # noqa: E402
    CaseOutcome,
    GoldenCase,
    approval_hash_problem,
    golden_content_sha256,
    load_golden,
    load_meta,
    missing_approval_fields,
    score,
)

if TYPE_CHECKING:
    from jarvis.llm.synthesis import ModelClient
    from jarvis.tools.registry import ToolRegistry, ToolSpec

_SELFTEST_BANNER = "*** SELF-TEST MODE: results are NOT evidence of grounding (SPEC R6a) ***"

# T-25: the golden set is SPEC R6a's external ground truth precisely because
# a human -- not an agent -- reviewed it (T-7 acceptance 2, _meta.approved_by/
# approved_at). Printed at both the start and end of a draft run so it can't
# scroll out of view, mirroring the existing _SELFTEST_BANNER pattern above.
_DRAFT_BANNER = (
    "*** DRAFT RUN: golden set is NOT APPROVED (T-7 acceptance 2, SPEC R6a) -- "
    "_meta.approved_by/approved_at is missing or blank. This result is NOT "
    "EVIDENCE of grounding. Do not cite it to close T-7 or any gate that "
    "depends on it. ***"
)

# T-29: approved_by/approved_at (above) prove a human's NAME and DATE are
# recorded -- not that their review covers the CURRENT bytes of the golden
# set. This is the remedy printed when _meta.approved_sha256 is missing or
# does not match golden_content_sha256(args.golden): run the helper, have a
# human confirm the content, paste the value themselves. No code path here
# ever writes approved_sha256 into golden.jsonl (T-25's no-signing non-goal
# carried forward).
_HASH_REMEDY = (
    "run `uv run python -m evals.print_approval_hash --golden <path>` to print "
    "the current canonical content hash, have a human REVIEW the content and "
    "confirm it's what they mean to approve, then paste the printed value into "
    "golden.jsonl's _meta.approved_sha256 by hand -- no agent may write this "
    "field (see backend/evals/README.md)."
)


# T-34 (post-build hardening): mirror production's per-turn model escalation
# instead of scoring the whole golden set on `convo_model()`.
#
# `jarvis.llm.routing.route_model(fired_tools)` decides this by inspecting
# each fired `ToolSpec.heavy` -- and production (`jarvis.pipeline.
# tools_wiring._make_dispatcher`) always calls it as `route_model([spec])`
# for the ONE ToolSpec it just dispatched. This eval has no live dispatch to
# read a fired ToolSpec off of, only golden.jsonl's static fixtures, so it
# has to reconstruct "which real tool produced this fixture" some other way.
#
# It can't be `fixture["tool"]`, golden.jsonl's free-text authoring label:
# `evals.scoring._parse_case` never preserves it on `GoldenCase` at all (only
# `fixture["result"]` survives), and -- confirmed by inspection -- it isn't
# even reliably the real `ToolSpec.name` to begin with. `github_qa` is
# DESIGN.md/`jarvis.llm.routing`'s own docstring's ORIGINAL conceptual name
# for the T-4 github capability; the concrete implementation split it into
# TWO real tools, `github_query` (source `github:graphql`) AND
# `load_repo_digest` (source `github:gitingest`), both `heavy=True`
# (`jarvis.tools.github.__init__`'s docstring: "Two tools, both heavy=True").
# Every golden case labeled `github_qa` actually carries source
# `github:gitingest` -- i.e. `load_repo_digest` -- while cases labeled
# `github_query` carry `github:graphql`, the OTHER heavy tool. Routing only
# the literally-`github_qa`-labeled cases to $TOOL_MODEL would silently miss
# every `github_query`-labeled case, which fires an equally `heavy` tool.
#
# `ToolResult.source` (pinned in `jarvis.llm.types`: "weather:open-meteo" |
# "github:graphql" | "github:gitingest" | "memory:mem0" | "self:manifest")
# IS preserved on `GoldenCase` (it's part of `result`, not `tool`), and it's
# a structural fact about which handler produced the fixture, not free-text.
# This maps it back to the real, registered `ToolSpec` so `.heavy` is read
# from the SAME object production reads it from -- never re-derived or
# hardcoded here.
_SOURCE_TO_TOOL_NAME: dict[str, str] = {
    "weather:open-meteo": "get_weather",
    "github:graphql": "github_query",
    "github:gitingest": "load_repo_digest",
    "self:manifest": "self_manifest",
    "memory:mem0": "recall_memory",
}


def _build_eval_registry() -> ToolRegistry:
    """The real production `ToolRegistry` (`jarvis.pipeline.tools_wiring.
    build_registry`), used here ONLY to read each real `ToolSpec.heavy` flag
    -- no handler is ever called through it. `LazyMemoryStore(database_url=
    None)` degrades to "unavailable" without a network touch (same
    construction `backend/tests/pipeline/test_tool_wiring.py`'s
    `_build_agent` uses for tests that never call the memory tool), which is
    fine here: this eval never calls `.get()`/`.search()` on it either, only
    reads the memory `ToolSpec`'s `.heavy` (hardcoded `False`,
    `tools_wiring._memory_tool_spec`) off the registry it's registered into.
    """
    from jarvis.pipeline.tools_wiring import LazyMemoryStore, build_registry

    return build_registry(memory=LazyMemoryStore(database_url=None), user_id_getter=lambda: "")


def _fired_specs(case: GoldenCase, registry: ToolRegistry) -> list[ToolSpec]:
    """The real `ToolSpec`(s) that produced `case`'s fixtures, resolved via
    `_SOURCE_TO_TOOL_NAME` + `registry`. Feeding this to `route_model` is
    exactly how this eval determines "which tools fired this turn" -- the
    same question production answers by calling `route_model([spec])` for
    the ToolSpec its dispatcher just ran (`tools_wiring._make_dispatcher`).

    Raises `ValueError` naming the case and the unmapped source if a fixture
    carries a `source` this eval doesn't know how to attribute to a real
    tool -- a golden set is a fixed, approved artifact (T-29's content hash),
    so this should never fire against the current approved set; it exists so
    an eval-set change nobody taught this mapping about fails LOUDLY, before
    any API call, rather than silently under- or over-escalating.
    """
    specs: list[ToolSpec] = []
    for fixture in case.fixtures:
        source = fixture.get("source")
        name = _SOURCE_TO_TOOL_NAME.get(source)  # type: ignore[arg-type]
        if name is None:
            raise ValueError(
                f"golden case {case.id!r}: fixture source {source!r} has no known "
                "production-tool mapping in evals.run._SOURCE_TO_TOOL_NAME, so this "
                "eval cannot tell whether the fixture's real tool is `heavy` (see "
                "jarvis.llm.routing.route_model) -- add the mapping there, or confirm "
                "this is genuinely a new tool and update this comment's enumeration"
            )
        specs.append(registry.get(name))
    return specs


class EchoModelClient:
    """No-network self-test `ModelClient`. Mirrors the exact prompt
    `synthesize_grounded` built -- question, the usable fixtures' `data`,
    and prefs -- back as the reply, verbatim, inventing nothing.

    Because it echoes real fixture data, a well-formed grounded case's
    `must_include` facts (literally present in that data, per the golden-set
    convention) show up in the reply and the case passes. But it also has no
    judgment: a SEMANTIC-refusal case (fixture data present but irrelevant to
    the question) gets "answered" anyway, which is exactly why this mode is
    not evidence of grounding -- see the banner and README.md.
    """

    def complete(self, *, model: str, system: str, user: str) -> str:
        return f"[echo self-test reply, no model called]\n{user}"


def _run_case(case: GoldenCase, *, model: str, client: ModelClient | None) -> CaseOutcome:
    from jarvis.llm.synthesis import synthesize_grounded

    try:
        answer = synthesize_grounded(
            case.query, case.fixtures, case.prefs, model=model, client=client
        )
    except Exception as exc:
        # One bad case must not abort the run; an error still forces a
        # nonzero exit (checked in _summarize).
        return CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            expect=case.expect,
            answer="",
            refused=False,
            verdict="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
    return score(case, answer)


def _summarize(
    outcomes: list[CaseOutcome], args: argparse.Namespace, *, draft: bool = False
) -> tuple[int, dict]:
    totals = Counter(o.verdict for o in outcomes)
    per_kind = Counter(o.kind for o in outcomes)

    print()
    print(f"totals ({len(outcomes)} cases):")
    for verdict in ("pass", "fabrication", "wrong_refusal", "missing_include", "error"):
        if totals.get(verdict):
            print(f"  {verdict:16s} {totals[verdict]}")

    print()
    print("coverage by kind:")
    for kind, count in sorted(per_kind.items()):
        print(f"  {kind:10s} {count}")

    fabrications = totals.get("fabrication", 0)
    wrong_refusals = totals.get("wrong_refusal", 0)
    missing_includes = totals.get("missing_include", 0)
    errors = totals.get("error", 0)

    reasons = []
    if fabrications > args.max_fabrications:
        reasons.append(f"fabrications {fabrications} > max {args.max_fabrications}")
    if wrong_refusals > args.max_wrong_refusals:
        reasons.append(f"wrong_refusals {wrong_refusals} > max {args.max_wrong_refusals}")
    if missing_includes:
        reasons.append(f"missing_include {missing_includes} > 0")
    if errors:
        reasons.append(f"errors {errors} > 0")

    exit_code = 1 if reasons else 0

    print()
    if draft:
        # Never print a bare PASS/FAIL for an unapproved golden set -- a
        # clean scoring result here is plumbing-only and must not be
        # mistakable for T-7 acceptance-2 evidence (SPEC R6a). A reader (or
        # a script grepping for "PASS:") will not find that string here.
        scoring_would_say = "FAIL" if reasons else "PASS"
        print(
            f"DRAFT-NOT-EVIDENCE (scoring would say {scoring_would_say}): "
            f"{len(outcomes)} cases, {fabrications} fabrications, "
            f"{wrong_refusals} wrong refusals, {missing_includes} missing "
            f"includes, {errors} errors -- golden set is UNAPPROVED "
            "(_meta.approved_by/approved_at missing or blank). This run does "
            "NOT satisfy T-7 acceptance 2 and is NOT EVIDENCE of grounding."
        )
    elif reasons:
        print(f"FAIL: {'; '.join(reasons)}")
    else:
        print(
            f"PASS: {len(outcomes)} cases, {fabrications} fabrications, "
            f"{wrong_refusals} wrong refusals, 0 missing includes, 0 errors"
        )

    summary = {
        "total_cases": len(outcomes),
        "totals_by_verdict": dict(totals),
        "totals_by_kind": dict(per_kind),
        "max_fabrications": args.max_fabrications,
        "max_wrong_refusals": args.max_wrong_refusals,
        "exit_code": exit_code,
        "fail_reasons": reasons,
        "draft": draft,
        "evidence": not draft,
    }
    return exit_code, summary


def _build_provenance(
    args: argparse.Namespace, case_models: dict[str, str], meta: dict[str, Any] | None
) -> dict:
    """Runtime-only provenance for a committed JSON report (T-33 follow-up,
    T-34 per-case routing): what actually ran, never hardcoded.

    `convo_model`/`tool_model` are read from the exact `jarvis.llm.routing`
    functions the run itself uses -- not from re-reading env vars
    independently -- so a value here can never silently drift from what
    `evals.run` would actually resolve. Both are recorded regardless of
    which one(s) drove THIS run's cases (see `case_models`/`model_used`)
    because a reader needs to know both the routed convo id and the routed
    tool-escalation id to judge coverage, not just whichever one happened
    to be active.

    `case_models`/`model_used` (T-34): `evals.run` now resolves a model PER
    CASE via `jarvis.llm.routing.route_model` (or pins every case to
    `--model` when given -- see `main()`), so one scalar can no longer
    honestly describe "the model this run used". `case_models` is the full
    case_id -> model_id map (every case this run actually scored, whether or
    not `synthesize_grounded` ended up calling the client -- e.g. a
    structural no-evidence refusal still has a routed model, it just never
    dispatches); `model_used` is the sorted set of distinct values in
    `case_models`, so a reader gets an honest plural summary instead of a
    single value that silently used to mean "the first one" (which, before
    T-34, was actually "the only one" -- this run-wide resolution is what
    T-34 replaced).

    `golden_approved_sha256`/`golden_content_sha256` bind the report to the
    exact golden.jsonl bytes it scored (T-29): the approved hash comes
    straight from the `_meta` header already loaded by `main()`, and the
    content hash is recomputed via `evals.scoring.golden_content_sha256`
    (imported, never reimplemented) so the two can be compared by a
    checker that never has to touch golden.jsonl itself.
    """
    from jarvis.llm.routing import ModelRoutingError, convo_model, tool_model

    def _resolve(fn):
        try:
            return fn()
        except ModelRoutingError:
            return None

    approved_sha256 = None
    if meta is not None:
        raw = meta.get("approved_sha256")
        if isinstance(raw, str) and raw.strip():
            approved_sha256 = raw.strip().lower()

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client": args.client,
        "convo_model": _resolve(convo_model),
        "tool_model": _resolve(tool_model),
        "model_used": sorted(set(case_models.values())),
        "case_models": dict(case_models),
        "golden_path": str(args.golden),
        "golden_approved_sha256": approved_sha256,
        "golden_content_sha256": golden_content_sha256(args.golden),
    }


def _write_json_report(
    path: Path, outcomes: list[CaseOutcome], summary: dict, provenance: dict
) -> None:
    report = {
        "summary": summary,
        "provenance": provenance,
        "cases": [
            {
                "case_id": o.case_id,
                "kind": o.kind,
                "expect": o.expect,
                "answer": o.answer,
                "refused": o.refused,
                "verdict": o.verdict,
                "detail": o.detail,
            }
            for o in outcomes
        ],
    }
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score synthesize_grounded against the T-7 golden set (SPEC R6/R6a)."
    )
    parser.add_argument("--golden", required=True, type=Path, help="path to golden.jsonl")
    parser.add_argument(
        "--max-fabrications", type=int, default=0, help="fabrications allowed before FAIL"
    )
    parser.add_argument(
        "--max-wrong-refusals", type=int, default=0, help="wrong_refusals allowed before FAIL"
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "pin EVERY case to this exact model id, overriding per-case routing "
            "(useful for an A/B run against one model only). Default: route each "
            "case through jarvis.llm.routing.route_model, exactly like production -- "
            "a case whose fixture(s) fire a `heavy` tool scores on tool_model(), "
            "every other case scores on convo_model()."
        ),
    )
    parser.add_argument(
        "--client",
        choices=("anthropic", "echo"),
        default="anthropic",
        help="anthropic: the real grounding eval. echo: no-network plumbing self-test only.",
    )
    parser.add_argument("--limit", type=int, default=None, help="only run the first N cases")
    parser.add_argument(
        "--json", type=Path, default=None, dest="json_out", help="write the full report as JSON"
    )
    parser.add_argument("--verbose", action="store_true", help="print each case's full answer")
    parser.add_argument(
        "--draft-unapproved-not-evidence",
        action="store_true",
        help=(
            "T-25/SPEC R6a: run against an UNAPPROVED golden set anyway, for "
            "local plumbing/dev use ONLY. The result is explicitly NOT "
            "EVIDENCE of grounding and is loudly labeled as such in the "
            "output (never PASS/FAIL). Never pass this to satisfy T-7's gate "
            "or any acceptance criterion that depends on this run."
        ),
    )
    parser.add_argument(
        "--require-approved",
        action="store_true",
        help=(
            "Hard-refuse to run against an unapproved golden set even if "
            "--draft-unapproved-not-evidence is also given. Verify/CI "
            "commands should set this so approval enforcement can never be "
            "silently combined away."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    # T-25: gate FIRST, before the API-key check or any case is loaded --
    # a golden set nobody reviewed shouldn't even spend an API call. T-7
    # acceptance 2 requires a human's review of golden.jsonl, recorded in
    # its _meta header; this is what makes that acceptance criterion
    # machine-checkable instead of merely documented.
    try:
        meta = load_meta(args.golden)
    except (ValueError, OSError) as exc:
        print(f"error: failed to load {args.golden}: {exc}", file=sys.stderr)
        return 2

    missing = missing_approval_fields(meta)
    draft = False
    if missing:
        reason = f"_meta.{' and '.join(missing)} missing or blank in {args.golden}"
        if args.require_approved or not args.draft_unapproved_not_evidence:
            refused_draft = (
                " (refused: --require-approved was also set)"
                if args.require_approved and args.draft_unapproved_not_evidence
                else ""
            )
            print(
                "error: golden set is NOT APPROVED for evaluation (T-7 "
                f"acceptance 2, SPEC R6a): {reason}. A human must review "
                "golden.jsonl and record approved_by/approved_at in its "
                "_meta header before a run counts as evidence -- no agent "
                "may fill these in (see backend/evals/README.md). For a "
                "local plumbing/dev run only, NOT evidence of grounding, "
                f"pass --draft-unapproved-not-evidence.{refused_draft}",
                file=sys.stderr,
            )
            return 3
        draft = True
        print(_DRAFT_BANNER, file=sys.stderr)
    else:
        # T-29: approved_by/approved_at are present and non-blank -- now
        # confirm that approval actually covers THIS content. Only reached
        # on an otherwise-approved set (an unapproved set already exited or
        # went to draft above); "unverifiable is never current", so a
        # missing/malformed hash blocks exactly like a mismatch does.
        hash_problem = approval_hash_problem(meta, args.golden)
        if hash_problem == "missing":
            print(
                "error: golden set is APPROVED BY NAME but that approval is NOT "
                "BOUND TO ITS CONTENT (T-29 -- SPEC R6a): _meta.approved_sha256 is "
                f"missing, blank, or not a well-formed sha256 hex digest in "
                f"{args.golden}. An approval that cannot be verified against the "
                f"current bytes is not current -- {_HASH_REMEDY}",
                file=sys.stderr,
            )
            return 4
        if hash_problem == "mismatch":
            print(
                "error: golden set's content has CHANGED SINCE APPROVAL (T-29 -- "
                f"SPEC R6a): _meta.approved_sha256 in {args.golden} does not match "
                "the file's current content hash -- the 2026-08-14 (or later) "
                f"human review no longer covers what is on disk -- {_HASH_REMEDY}",
                file=sys.stderr,
            )
            return 4

    if args.client == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: --client anthropic requires ANTHROPIC_API_KEY to be set, and no key "
            "was found in the environment. Export it (see .env.example), or pass "
            "--client echo for a no-network plumbing self-test -- NOT evidence of "
            "grounding, see backend/evals/README.md.",
            file=sys.stderr,
        )
        return 2

    try:
        cases = load_golden(args.golden)
    except (ValueError, OSError) as exc:
        print(f"error: failed to load {args.golden}: {exc}", file=sys.stderr)
        return 2

    if args.limit is not None:
        cases = cases[: args.limit]

    from jarvis.llm.routing import ModelRoutingError, route_model

    # T-34: resolve every case's model BEFORE running any of them -- a full
    # pre-pass, not lazily inside the loop below -- so a routing/env-var
    # problem (an unmapped fixture source, an unset $TOOL_MODEL) is a fast,
    # zero-API-calls failure instead of surfacing after N-1 cases already
    # spent real money. `--model` pins every case to one explicit id
    # (unchanged A/B-override behavior); the default path routes each case
    # through the real `route_model`, mirroring production per-turn
    # escalation exactly (see `_fired_specs`/`_SOURCE_TO_TOOL_NAME` above).
    case_models: dict[str, str] = {}
    if args.model is not None:
        for case in cases:
            case_models[case.id] = args.model
    else:
        registry = _build_eval_registry()
        try:
            for case in cases:
                case_models[case.id] = route_model(_fired_specs(case, registry))
        except ModelRoutingError as exc:
            print(f"error: {exc}. Pass --model explicitly to override.", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    client: ModelClient | None = EchoModelClient() if args.client == "echo" else None

    if args.client == "echo":
        print(_SELFTEST_BANNER, file=sys.stderr)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        case_model = case_models[case.id]
        outcome = _run_case(case, model=case_model, client=client)
        outcomes.append(outcome)
        line = f"  {outcome.case_id:8s} {outcome.kind:9s} {outcome.verdict} [{case_model}]"
        if outcome.detail and (args.verbose or outcome.verdict != "pass"):
            line += f" -- {outcome.detail}"
        if args.verbose:
            line += f"\n      answer: {outcome.answer!r}"
        print(line)

    exit_code, summary = _summarize(outcomes, args, draft=draft)

    if args.json_out is not None:
        provenance = _build_provenance(args, case_models, meta)
        _write_json_report(args.json_out, outcomes, summary, provenance)

    if args.client == "echo":
        print(_SELFTEST_BANNER, file=sys.stderr)
    if draft:
        print(_DRAFT_BANNER, file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
