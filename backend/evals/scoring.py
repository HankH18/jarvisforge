"""Golden-case dataclasses, loader, and pass/fail scoring for the T-7
grounding eval harness (`.claude/scratch/t7-contract.md`). Split out of
`run.py` to keep the CLI readable.

Deliberately has no `jarvis` import at module scope: `evals/run.py` is the
one file responsible for putting `backend/src` on `sys.path` before any
`jarvis` name is touched (see its module docstring), and this module is
imported by `run.py` immediately after that happens. The one runtime
`jarvis` symbol this module needs (`REFUSAL`) is imported lazily inside
`score()` so this file never depends on import order itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.llm.types import ToolResult

_VALID_KINDS = frozenset({"grounded", "refusal", "memory"})
_VALID_EXPECT = frozenset({"answer", "refuse"})


@dataclass(frozen=True)
class GoldenCase:
    id: str
    query: str
    kind: str
    expect: str
    fixtures: list[ToolResult]
    prefs: list[str]
    must_include: list[str]
    must_not_include: list[str]
    note: str


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    kind: str
    expect: str
    answer: str
    refused: bool
    verdict: str  # "pass" | "fabrication" | "wrong_refusal" | "missing_include" | "error"
    detail: str  # empty when verdict == "pass"


def load_golden(path: str | Path) -> list[GoldenCase]:
    """Parse `path` (one JSON object per line). Skips the `{"id": "_meta", ...}`
    header line. Raises `ValueError` naming the offending case id for any
    structurally malformed case: a missing required key, an unknown `expect`
    or `kind`, or a fixture missing `source`/`data`.
    """
    cases: list[GoldenCase] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"golden set line {lineno}: invalid JSON ({exc})") from exc
        if raw.get("id") == "_meta":
            continue
        cases.append(_parse_case(raw))
    return cases


def _parse_case(raw: dict[str, Any]) -> GoldenCase:
    case_id = raw.get("id") or "<missing id>"

    # kind/prefs/note are the contract's "permitted optional additions" to the
    # base pinned schema -- prefs and note default sensibly when absent, but
    # `kind` is required here because it drives CaseOutcome.kind and the
    # per-kind coverage report, and its value is explicitly validated below
    # ("unknown kind" is a separate malformation from "missing key").
    required = ("id", "query", "kind", "expect", "fixtures", "must_include", "must_not_include")
    for key in required:
        if key not in raw:
            raise ValueError(f"golden case {case_id!r}: missing required key {key!r}")

    expect = raw["expect"]
    if expect not in _VALID_EXPECT:
        raise ValueError(
            f"golden case {case_id!r}: unknown expect {expect!r} "
            f"(must be one of {sorted(_VALID_EXPECT)})"
        )
    kind = raw["kind"]
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"golden case {case_id!r}: unknown kind {kind!r} "
            f"(must be one of {sorted(_VALID_KINDS)})"
        )

    fixtures: list[ToolResult] = []
    for i, fixture in enumerate(raw["fixtures"]):
        if "tool" not in fixture or "result" not in fixture:
            raise ValueError(f"golden case {case_id!r}: fixtures[{i}] missing 'tool' or 'result'")
        result = fixture["result"]
        if "source" not in result or "data" not in result:
            raise ValueError(
                f"golden case {case_id!r}: fixtures[{i}].result missing 'source' or 'data'"
            )
        fixtures.append(result)

    return GoldenCase(
        id=case_id,
        query=raw["query"],
        kind=kind,
        expect=expect,
        fixtures=fixtures,
        prefs=list(raw.get("prefs", [])),
        must_include=list(raw["must_include"]),
        must_not_include=list(raw["must_not_include"]),
        note=raw.get("note", ""),
    )


def score(case: GoldenCase, answer: str) -> CaseOutcome:
    """Score `answer` (the real `synthesize_grounded` reply) against `case`.

    | case `expect` | model refused? | verdict |
    |---|---|---|
    | refuse | yes | pass (unless must_not_include fires) |
    | refuse | no  | fabrication |
    | answer | yes | wrong_refusal |
    | answer | no  | pass unless must_not_include (fabrication) or a
    |               missing must_include (missing_include) |

    `must_not_include` is checked FIRST and unconditionally, ahead of every
    other branch: a forbidden term anywhere in the answer is always scored
    `fabrication`, refused or not -- the contract states this check applies
    "for EVERY case regardless of expect/refusal", and a refusal that still
    smuggles in an invented fact is exactly the failure mode this harness
    exists to catch.
    """
    from jarvis.llm.types import REFUSAL

    refused = answer.startswith(REFUSAL)
    lowered = answer.lower()

    forbidden = [s for s in case.must_not_include if s.lower() in lowered]
    if forbidden:
        return CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            expect=case.expect,
            answer=answer,
            refused=refused,
            verdict="fabrication",
            detail=f"answer contains forbidden term(s): {forbidden}",
        )

    if case.expect == "refuse":
        if refused:
            return CaseOutcome(case.id, case.kind, case.expect, answer, refused, "pass", "")
        return CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            expect=case.expect,
            answer=answer,
            refused=refused,
            verdict="fabrication",
            detail="expected a refusal (no supporting evidence) but the model answered",
        )

    # case.expect == "answer"
    if refused:
        return CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            expect=case.expect,
            answer=answer,
            refused=refused,
            verdict="wrong_refusal",
            detail="expected a grounded answer but the model refused",
        )
    missing = [s for s in case.must_include if s.lower() not in lowered]
    if missing:
        return CaseOutcome(
            case_id=case.id,
            kind=case.kind,
            expect=case.expect,
            answer=answer,
            refused=refused,
            verdict="missing_include",
            detail=f"missing required substring(s): {missing}",
        )
    return CaseOutcome(case.id, case.kind, case.expect, answer, refused, "pass", "")
