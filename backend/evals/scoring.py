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

import hashlib
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


def load_meta(path: str | Path) -> dict[str, Any] | None:
    """Return the `{"id": "_meta", ...}` header object from `path`, or
    `None` if the file has no such line. Mirrors `load_golden`'s line-by-line
    JSON parsing (same `ValueError` shape for malformed JSON) so callers get
    a consistent error surface whether they ask for the cases or the header.

    Raises `ValueError` if MORE THAN ONE line has `"id": "_meta"`. Silently
    picking the first (or last) would let a forged approved `_meta` line
    placed ahead of the genuine one make an unapproved set look approved --
    a golden set with two headers is malformed, not a judgment call, so this
    refuses rather than picking a winner (T-25).
    """
    text = Path(path).read_text(encoding="utf-8")
    meta: dict[str, Any] | None = None
    meta_lineno: int | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"golden set line {lineno}: invalid JSON ({exc})") from exc
        if raw.get("id") == "_meta":
            if meta is not None:
                raise ValueError(
                    f"golden set line {lineno}: duplicate _meta header (first seen at "
                    f"line {meta_lineno}) -- a golden set must have exactly one _meta "
                    "line; refusing to silently pick one over the other"
                )
            meta = raw
            meta_lineno = lineno
    return meta


def _is_real_approval_value(value: Any) -> bool:
    """True iff `value` is a plain `str` containing at least one
    alphanumeric character.

    Two separate holes this closes at once:
    - Non-`str` shapes (`0`, `False`, `True`, `[]`, `{}`, `3.14`, `None`,
      a nested object) must never read as "approved" just because
      `str(value)` happens to stringify to something non-empty (`str(0) ==
      "0"`, `str(False) == "False"`) -- only an actual `str` counts, and
      note `bool` is deliberately excluded even though `isinstance(True,
      int)` is `True` in Python: `isinstance(value, str)` alone already
      excludes every bool, so there is no separate bool special-case to
      get wrong.
    - A `str` with no alphanumeric content at all -- `""`, whitespace-only
      (`"   "`, `"\\t\\n "`), or punctuation-only (`"---"`) -- is not a
      human's name or a date any more than an empty string is; requiring
      at least one alphanumeric character catches all three with one rule
      instead of three separate blank-string special cases.
    """
    return isinstance(value, str) and any(ch.isalnum() for ch in value)


def missing_approval_fields(meta: dict[str, Any] | None) -> list[str]:
    """Which of `approved_by`/`approved_at` are absent or not a real
    approval value in `meta` (T-7 acceptance 2 / SPEC R6a: a human's review
    of the golden set, recorded in the `_meta` header, is what makes this an
    external ground truth rather than a set an agent signed off on itself).

    A field counts as missing whenever `_is_real_approval_value` is False --
    see that function for the full enumeration (non-`str` shapes, blank
    strings, whitespace-only strings, punctuation-only strings), plus:
    - `meta is None` (no `_meta` line in the file at all) -> both fields
    - the key absent from `_meta` (`meta.get(key)` returns `None`, which
      `_is_real_approval_value` already rejects)
    """
    if meta is None:
        return ["approved_by", "approved_at"]
    return [
        key for key in ("approved_by", "approved_at") if not _is_real_approval_value(meta.get(key))
    ]


# ---------------------------------------------------------------------------
# T-29: bind approval to the bytes it approved.
#
# T-25 (`missing_approval_fields` above) proves a human's NAME and DATE are
# recorded -- not that their review covers the golden set's CURRENT content.
# Demonstrated live: T-19 added a case and tightened another's pins after the
# 2026-08-13 approval, and `missing_approval_fields` stayed satisfied the
# whole time because `approved_by`/`approved_at` were never touched. Every
# gate that only checks those two fields is structurally blind to "the set
# changed since a human looked at it".
#
# The fix: `_meta.approved_sha256` binds the approval to a canonical content
# hash. `canonical_approval_bytes`/`golden_content_sha256` are the ONE
# definition of that hash -- `evals/print_approval_hash.py` (the human-facing
# helper) and `evals/run.py` (the gate) both call `golden_content_sha256`
# directly, so they cannot drift apart by one computing it differently than
# the other.
# ---------------------------------------------------------------------------


def canonical_approval_bytes(text: str) -> bytes:
    """The exact bytes T-29's `approved_sha256` is a hash OF, given the full
    text of a golden.jsonl file.

    Canonicalization, precisely:
    - Split `text` into lines with `str.splitlines()` (accepts `\\n` or
      `\\r\\n` input; the line CONTENT itself, not the terminator, is what's
      hashed, so re-saving the file with different line endings does not
      change the hash).
    - Each line is stripped of surrounding whitespace; blank lines are
      dropped entirely -- this mirrors `load_golden`'s own blank-line skip,
      so adding/removing a purely blank line never changes the hash.
    - Any line that JSON-parses to an object with `"id": "_meta"` (the same
      test `load_golden`/`load_meta` use) is EXCLUDED -- the header carries
      the approval itself (including `approved_sha256`), so it can never be
      part of what the hash covers; a golden set with more than one such
      line is a `load_meta` error before this function is ever reached in
      the gate, so this function itself does not special-case duplicates.
    - The remaining lines are kept VERBATIM (not re-serialized -- reordering
      keys or changing a case's own JSON whitespace DOES change the hash,
      because those are real bytes a human approved) in their original file
      order, rejoined with a single `\\n` between each, plus exactly one
      trailing `\\n` -- so the presence or absence of a trailing newline at
      end-of-file never changes the hash.
    - Encoded as UTF-8.

    Raises `ValueError` (matching `load_golden`/`load_meta`'s error shape)
    if any non-blank line is not valid JSON.
    """
    lines: list[str] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"golden set line {lineno}: invalid JSON ({exc})") from exc
        if isinstance(raw, dict) and raw.get("id") == "_meta":
            continue
        lines.append(line)
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def golden_content_sha256(path: str | Path) -> str:
    """sha256 hex digest of `canonical_approval_bytes` for the file at
    `path`. THE canonical hash `_meta.approved_sha256` binds to -- see
    `canonical_approval_bytes` for the exact normalization. Both
    `evals/print_approval_hash.py` and the gate in `evals/run.py` call this
    exact function so they cannot compute it two different ways.
    """
    text = Path(path).read_text(encoding="utf-8")
    return hashlib.sha256(canonical_approval_bytes(text)).hexdigest()


def _is_well_formed_sha256_hex(value: Any) -> bool:
    """True iff `value` is a `str` shaped like a sha256 hex digest (64
    lowercase-or-uppercase hex characters). Mirrors `_is_real_approval_value`
    in spirit: a value that isn't even shaped like a hash can never verify,
    so it is unverifiable the same as an absent one -- no need to hash the
    file just to reject `0`, `True`, `[]`, or a typo'd short string.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    return len(candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in candidate)


def approval_hash_problem(meta: dict[str, Any] | None, golden_path: str | Path) -> str | None:
    """Whether `meta["approved_sha256"]` actually verifies against
    `golden_path`'s CURRENT content (T-29). Returns `None` when it does;
    otherwise a short machine-stable reason:

    - `"missing"` -- `meta` is `None`, the key is absent, or its value isn't
      a well-formed sha256 hex digest (`_is_well_formed_sha256_hex`).
      Per the acceptance text, "unverifiable is never current": an
      otherwise-fully-approved set (valid `approved_by`/`approved_at`) with
      no verifiable hash is treated exactly like a mismatch, not waved
      through as some lesser problem.
    - `"mismatch"` -- a well-formed hash is present but does not equal
      `golden_content_sha256(golden_path)` -- the content changed (or was
      never what this hash was minted for) since a human approved it.

    Comparison is case-insensitive (a human may paste either case of hex).
    Callers are expected to call this ONLY after confirming
    `missing_approval_fields(meta) == []` -- this function does not itself
    re-check `approved_by`/`approved_at`.
    """
    if meta is None or not _is_well_formed_sha256_hex(meta.get("approved_sha256")):
        return "missing"
    candidate = meta["approved_sha256"].strip().lower()
    if candidate != golden_content_sha256(golden_path):
        return "mismatch"
    return None


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
