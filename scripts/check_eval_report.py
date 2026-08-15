#!/usr/bin/env python3
"""T-33 (+ follow-up): validate a committed eval report (`evals.run --json`
output).

Committed JSON eval reports (`docs/evals/latest.json` and any future one
alongside a change it evidences) are now the eval-evidence convention --
see `docs/evals/README.md`. A report that merely LOOKS like JSON is not
enough: a hand-edited counter, a truncated file, a case list that doesn't
match what the summary claims, or a report with no way to tell WHAT
produced it (which client, which model, when, against which exact golden
bytes) must all be rejected, or committing a report proves nothing.

This script is invoked as bare `python3` OUTSIDE the project's `uv`
environment (see the ticket's verify command), so it is **stdlib-only** --
no third-party imports, and deliberately no import from `backend/` either
(a schema checker that has to import the code under test to validate its
own output could be fooled by the same bug in both places).

Usage:
    python3 scripts/check_eval_report.py <path-to-report.json>

Exit codes:
    0  report is well-formed, carries a valid provenance block naming the
       REAL client (`anthropic`), AND its summary shows zero failures
       (0 fabrications, 0 wrong_refusals, 0 missing_include, 0 errors,
       exit_code 0, not a draft run)
    1  report is well-formed but fails the zero-failure check, OR is
       schema-invalid (missing/malformed keys, internal counter mismatch,
       case-count mismatch), OR its `provenance` block is missing,
       malformed, internally inconsistent (golden hash mismatch), or names
       a non-real client (e.g. a committed `--client echo` self-test)
    2  usage error (bad args, file not found, unreadable, invalid JSON)

Prints one issue per line to stderr on failure; prints a one-line summary
to stdout on success.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from typing import Any

# Mirrors backend/evals/scoring.py's CaseOutcome.verdict values and
# GoldenCase's kind/expect values exactly -- duplicated here (not imported;
# see the module docstring) because this checker must survive independent
# of the backend package it is validating output FROM.
_KNOWN_VERDICTS = frozenset({"pass", "fabrication", "wrong_refusal", "missing_include", "error"})
_FAILURE_VERDICTS = ("fabrication", "wrong_refusal", "missing_include", "error")
_KNOWN_KINDS = frozenset({"grounded", "refusal", "memory"})
_KNOWN_EXPECT = frozenset({"answer", "refuse"})

_REQUIRED_SUMMARY_KEYS = (
    "total_cases",
    "totals_by_verdict",
    "totals_by_kind",
    "max_fabrications",
    "max_wrong_refusals",
    "exit_code",
    "fail_reasons",
    "draft",
    "evidence",
)
_REQUIRED_CASE_KEYS = ("case_id", "kind", "expect", "answer", "refused", "verdict", "detail")

# T-33-followup: every committed report must carry a `provenance` block
# stating what actually produced it (client, resolved model ids, a
# timestamp, and the golden-set binding) -- see docs/evals/README.md. This
# checker duplicates the sha256-hex shape check from
# backend/evals/scoring.py's `_is_well_formed_sha256_hex` rather than
# importing it (this file must stay stdlib-only and independent of the
# backend package it validates output from -- see module docstring).
_REQUIRED_PROVENANCE_KEYS = (
    "timestamp_utc",
    "client",
    "convo_model",
    "tool_model",
    "model_used",
    "golden_path",
    "golden_approved_sha256",
    "golden_content_sha256",
)
_TIMESTAMP_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REAL_CLIENT = "anthropic"


class ReportInvalid(Exception):
    """Collected list of schema/consistency problems -- never a single early exit,
    so one run of this script names every problem at once."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print(f"usage error: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"usage error: {path} is not valid JSON (truncated or malformed): {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    if not isinstance(data, dict):
        print(f"usage error: {path} top level must be a JSON object, got {type(data).__name__}",
              file=sys.stderr)
        raise SystemExit(2)
    return data


def _check_schema(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate structure and internal consistency. Returns (summary, cases)
    on success; raises ReportInvalid with every problem found otherwise."""
    problems: list[str] = []

    if "summary" not in report:
        problems.append("missing top-level key 'summary'")
    if "cases" not in report:
        problems.append("missing top-level key 'cases'")
    if problems:
        raise ReportInvalid(problems)

    summary = report["summary"]
    cases = report["cases"]

    if not isinstance(summary, dict):
        problems.append(f"'summary' must be an object, got {type(summary).__name__}")
    if not isinstance(cases, list):
        problems.append(f"'cases' must be an array, got {type(cases).__name__}")
    if problems:
        raise ReportInvalid(problems)

    for key in _REQUIRED_SUMMARY_KEYS:
        if key not in summary:
            problems.append(f"summary missing required key {key!r}")
    if problems:
        raise ReportInvalid(problems)

    if not isinstance(summary["total_cases"], int) or isinstance(summary["total_cases"], bool):
        problems.append(f"summary.total_cases must be an int, got {summary['total_cases']!r}")
    if not isinstance(summary["totals_by_verdict"], dict):
        problems.append("summary.totals_by_verdict must be an object")
    if not isinstance(summary["totals_by_kind"], dict):
        problems.append("summary.totals_by_kind must be an object")
    if not isinstance(summary["fail_reasons"], list):
        problems.append("summary.fail_reasons must be an array")
    if not isinstance(summary["exit_code"], int) or isinstance(summary["exit_code"], bool):
        problems.append(f"summary.exit_code must be an int, got {summary['exit_code']!r}")
    if not isinstance(summary["draft"], bool):
        problems.append(f"summary.draft must be a bool, got {summary['draft']!r}")
    if not isinstance(summary["evidence"], bool):
        problems.append(f"summary.evidence must be a bool, got {summary['evidence']!r}")
    if problems:
        raise ReportInvalid(problems)

    # Per-case shape.
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append(f"cases[{i}] must be an object, got {type(case).__name__}")
            continue
        for key in _REQUIRED_CASE_KEYS:
            if key not in case:
                problems.append(f"cases[{i}] (id={case.get('case_id', '?')!r}) missing key {key!r}")
        if "verdict" in case and case["verdict"] not in _KNOWN_VERDICTS:
            problems.append(
                f"cases[{i}] id={case.get('case_id', '?')!r} has unknown verdict "
                f"{case['verdict']!r} (must be one of {sorted(_KNOWN_VERDICTS)})"
            )
        if "kind" in case and case["kind"] not in _KNOWN_KINDS:
            problems.append(
                f"cases[{i}] id={case.get('case_id', '?')!r} has unknown kind "
                f"{case['kind']!r} (must be one of {sorted(_KNOWN_KINDS)})"
            )
        if "expect" in case and case["expect"] not in _KNOWN_EXPECT:
            problems.append(
                f"cases[{i}] id={case.get('case_id', '?')!r} has unknown expect "
                f"{case['expect']!r} (must be one of {sorted(_KNOWN_EXPECT)})"
            )
        if "refused" in case and not isinstance(case["refused"], bool):
            problems.append(f"cases[{i}] id={case.get('case_id', '?')!r} .refused must be a bool")
        if "case_id" in case and not isinstance(case["case_id"], str):
            problems.append(f"cases[{i}] .case_id must be a string")
    if problems:
        raise ReportInvalid(problems)

    # Case-count consistency: the summary's claimed total must match the
    # actual number of cases the report carries -- a truncated `cases`
    # array (or a total_cases inflated by hand) is exactly what this catches.
    if summary["total_cases"] != len(cases):
        problems.append(
            f"summary.total_cases ({summary['total_cases']}) does not match "
            f"len(cases) ({len(cases)}) -- the report is truncated, padded, "
            "or its counters were hand-edited"
        )

    # Duplicate case ids would let a truncated run pad its count back up by
    # repeating a case -- total_cases could match len(cases) while real
    # coverage silently shrank.
    ids = [c.get("case_id") for c in cases if isinstance(c, dict)]
    dupes = sorted({cid for cid, n in Counter(ids).items() if n > 1})
    if dupes:
        problems.append(f"duplicate case_id(s) in cases: {dupes}")

    # Recompute totals_by_verdict / totals_by_kind straight from the case
    # list and require an exact match against the summary's claimed
    # counters -- this is what catches a hand-edited summary counter (e.g.
    # flipping fabrication to a nonzero value) that the per-case records
    # don't actually back up, in EITHER direction (summary edited alone, or
    # a case's verdict edited alone while the summary was left stale).
    recomputed_verdict = dict(Counter(c["verdict"] for c in cases if isinstance(c, dict) and "verdict" in c))
    recomputed_kind = dict(Counter(c["kind"] for c in cases if isinstance(c, dict) and "kind" in c))
    if recomputed_verdict != summary["totals_by_verdict"]:
        problems.append(
            "summary.totals_by_verdict does not match counts recomputed from "
            f"cases: summary says {summary['totals_by_verdict']}, cases say "
            f"{recomputed_verdict}"
        )
    if recomputed_kind != summary["totals_by_kind"]:
        problems.append(
            "summary.totals_by_kind does not match counts recomputed from "
            f"cases: summary says {summary['totals_by_kind']}, cases say "
            f"{recomputed_kind}"
        )

    if problems:
        raise ReportInvalid(problems)

    return summary, cases


def _is_well_formed_sha256_hex(value: Any) -> bool:
    """True iff `value` is a `str` shaped like a sha256 hex digest (64
    hex characters, either case). Mirrors
    `backend/evals/scoring.py::_is_well_formed_sha256_hex` in spirit but is
    reimplemented here (not imported -- see module docstring)."""
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    return len(candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in candidate)


def _check_provenance(report: dict[str, Any]) -> list[str]:
    """Validate the `provenance` block a committed report must now carry
    (T-33 follow-up): this is what lets a reader tell WHAT produced a
    report -- which client, which resolved model ids, when, and which
    exact golden-set bytes -- without needing commit-message prose (the
    thing T-33 set out to retire). Returns a list of problems; empty means
    valid. A missing/malformed provenance block is its own rejection
    class, separate from `_check_schema` and `_check_zero_failure`, so a
    `--client echo` self-test is rejected ON THE PROVENANCE FIELD ITSELF
    -- not incidentally because echo happens to trip refusal cases.
    """
    problems: list[str] = []

    if "provenance" not in report:
        problems.append(
            "missing top-level key 'provenance' -- every committed report must carry a "
            "provenance block (client, resolved convo/tool model ids, timestamp, golden-set "
            "binding); see docs/evals/README.md"
        )
        return problems

    provenance = report["provenance"]
    if not isinstance(provenance, dict):
        problems.append(f"'provenance' must be an object, got {type(provenance).__name__}")
        return problems

    for key in _REQUIRED_PROVENANCE_KEYS:
        if key not in provenance:
            problems.append(f"provenance missing required key {key!r}")
    if problems:
        return problems

    if not isinstance(provenance["timestamp_utc"], str) or not _TIMESTAMP_UTC_RE.match(
        provenance["timestamp_utc"]
    ):
        problems.append(
            "provenance.timestamp_utc must be a UTC ISO-8601 timestamp shaped like "
            f"'2026-08-14T20:15:30Z', got {provenance['timestamp_utc']!r}"
        )

    if not isinstance(provenance["client"], str) or not provenance["client"]:
        problems.append(f"provenance.client must be a non-empty string, got {provenance['client']!r}")

    for key in ("convo_model", "tool_model"):
        value = provenance[key]
        if value is not None and (not isinstance(value, str) or not value):
            problems.append(
                f"provenance.{key} must be a non-empty string or null, got {value!r}"
            )

    if not isinstance(provenance["model_used"], str) or not provenance["model_used"]:
        problems.append(
            f"provenance.model_used must be a non-empty string, got {provenance['model_used']!r}"
        )

    if not isinstance(provenance["golden_path"], str) or not provenance["golden_path"]:
        problems.append(
            f"provenance.golden_path must be a non-empty string, got {provenance['golden_path']!r}"
        )

    if not _is_well_formed_sha256_hex(provenance["golden_approved_sha256"]):
        problems.append(
            "provenance.golden_approved_sha256 must be a well-formed sha256 hex digest, got "
            f"{provenance['golden_approved_sha256']!r}"
        )

    if not _is_well_formed_sha256_hex(provenance["golden_content_sha256"]):
        problems.append(
            "provenance.golden_content_sha256 must be a well-formed sha256 hex digest, got "
            f"{provenance['golden_content_sha256']!r}"
        )

    if problems:
        return problems

    # T-29 binding: a real run only reaches _write_json_report after the
    # approval gate confirmed approved_sha256 == golden_content_sha256 for
    # the bytes it scored, so these two provenance fields are TAUTOLOGICALLY
    # equal in an unmodified report. A disagreement here means the
    # committed report was hand-edited after the fact (or a golden hash
    # from a different run/file was pasted in) -- reject regardless of the
    # rest of the report's counters.
    if provenance["golden_approved_sha256"].strip().lower() != provenance[
        "golden_content_sha256"
    ].strip().lower():
        problems.append(
            "provenance.golden_approved_sha256 does not match provenance.golden_content_sha256 -- "
            "this report's golden-set binding is inconsistent (hand-edited after the run, or "
            "pasted from a different run/file); a real run's approval gate (T-29) guarantees "
            "these agree at write time, so a mismatch here is proof of tampering"
        )

    # The whole point of this follow-up: a committed report's client MUST
    # be the real one. `--client echo` is a no-network plumbing self-test
    # (see backend/evals/run.py's EchoModelClient) and must never be
    # committed to docs/evals/ regardless of its counters (see
    # docs/evals/README.md).
    if provenance["client"] != _REAL_CLIENT:
        problems.append(
            f"provenance.client is {provenance['client']!r} (must be {_REAL_CLIENT!r}) -- a "
            "--client echo self-test (or any non-real client) is NOT eval evidence and must "
            "never be committed under docs/evals/; see docs/evals/README.md"
        )

    return problems


def _check_zero_failure(summary: dict[str, Any]) -> list[str]:
    """The convention this report must satisfy to count as clean evidence
    (T-33 acceptance 1): 0 fabrications, 0 wrong_refusals, 0 missing_include,
    0 errors, a real (non-draft) run, and the run's own exit_code agrees."""
    problems: list[str] = []
    totals = summary["totals_by_verdict"]
    for verdict in _FAILURE_VERDICTS:
        count = totals.get(verdict, 0)
        if count:
            problems.append(f"{count} case(s) scored {verdict!r} (must be 0)")
    if summary["exit_code"] != 0:
        problems.append(f"summary.exit_code is {summary['exit_code']!r} (must be 0)")
    if summary["fail_reasons"]:
        problems.append(f"summary.fail_reasons is non-empty: {summary['fail_reasons']!r}")
    if summary["draft"] is not False:
        problems.append(
            "summary.draft is not False -- a draft run (unapproved golden set) is "
            "never evidence (SPEC R6a), regardless of its counters"
        )
    if summary["evidence"] is not True:
        problems.append("summary.evidence is not True")
    if summary["total_cases"] <= 0:
        problems.append("summary.total_cases is 0 -- an empty run proves nothing")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path-to-report.json>", file=sys.stderr)
        return 2

    path = argv[1]
    report = _load(path)

    try:
        summary, cases = _check_schema(report)
    except ReportInvalid as exc:
        print(f"REJECTED: {path} fails schema/consistency validation:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    provenance_problems = _check_provenance(report)
    if provenance_problems:
        print(f"REJECTED: {path} fails provenance validation:", file=sys.stderr)
        for problem in provenance_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    failures = _check_zero_failure(summary)
    if failures:
        print(f"REJECTED: {path} is schema-valid but is not a clean (zero-failure) report:",
              file=sys.stderr)
        for problem in failures:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"OK: {path} is schema-valid and clean -- {summary['total_cases']} cases, "
        "0 fabrications, 0 wrong refusals, 0 missing includes, 0 errors, exit_code 0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
