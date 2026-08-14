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
from pathlib import Path
from typing import TYPE_CHECKING

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
    load_golden,
    load_meta,
    missing_approval_fields,
    score,
)

if TYPE_CHECKING:
    from jarvis.llm.synthesis import ModelClient

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


def _write_json_report(path: Path, outcomes: list[CaseOutcome], summary: dict) -> None:
    report = {
        "summary": summary,
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
        "--model", default=None, help="model id (default: jarvis.llm.routing.convo_model())"
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

    if args.client == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: --client anthropic requires ANTHROPIC_API_KEY to be set, and no key "
            "was found in the environment. Export it (see .env.example), or pass "
            "--client echo for a no-network plumbing self-test -- NOT evidence of "
            "grounding, see backend/evals/README.md.",
            file=sys.stderr,
        )
        return 2

    from jarvis.llm.routing import ModelRoutingError, convo_model

    if args.model is not None:
        model = args.model
    else:
        try:
            model = convo_model()
        except ModelRoutingError as exc:
            print(f"error: {exc}. Pass --model explicitly to override.", file=sys.stderr)
            return 2

    try:
        cases = load_golden(args.golden)
    except (ValueError, OSError) as exc:
        print(f"error: failed to load {args.golden}: {exc}", file=sys.stderr)
        return 2

    if args.limit is not None:
        cases = cases[: args.limit]

    client: ModelClient | None = EchoModelClient() if args.client == "echo" else None

    if args.client == "echo":
        print(_SELFTEST_BANNER, file=sys.stderr)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcome = _run_case(case, model=model, client=client)
        outcomes.append(outcome)
        line = f"  {outcome.case_id:8s} {outcome.kind:9s} {outcome.verdict}"
        if outcome.detail and (args.verbose or outcome.verdict != "pass"):
            line += f" -- {outcome.detail}"
        if args.verbose:
            line += f"\n      answer: {outcome.answer!r}"
        print(line)

    exit_code, summary = _summarize(outcomes, args, draft=draft)

    if args.json_out is not None:
        _write_json_report(args.json_out, outcomes, summary)

    if args.client == "echo":
        print(_SELFTEST_BANNER, file=sys.stderr)
    if draft:
        print(_DRAFT_BANNER, file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
