"""T-16 follow-up (verifier-reported defect D): scripts/latency_report.py's
"By lookup status" split must never let an unverified tool_ms==0 masquerade
as a confirmed no-lookup sample.

Before this fix, that section inferred lookup status purely from `tool_ms`
and printed an R1 PASS/FAIL verdict from that inference alone. A filler-
raced lookup turn (see jarvis.pipeline.latency.LatencyLogger's docstring)
could log tool_ms==0.0 exactly like a genuine no-lookup turn, landing in
that bucket and flattering its p95 -- a real record's own worker-log bug
silently corrupting the R1 verdict computed from it. The fix makes this
section diagnostic-only: it still shows the tool_ms-inferred breakdown, but
never certifies an R1 verdict without --driver-json (ground truth, not
inference). These tests drive `_render_report` directly (pure function, no
process spawn needed).

Run from the repo root or backend/ with any Python that can import respx-free
stdlib-only code, e.g.:
    cd backend && uv run python -m pytest ../scripts/tests/test_latency_report.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from latency_report import _render_report, _Turn  # noqa: E402


def _turn(turn_id: int, *, model: str, ttfa_ms: float, tool_ms: float) -> _Turn:
    return _Turn(turn_id=turn_id, model=model, ttfa_ms=ttfa_ms, tool_ms=tool_ms)


def _mixed_turns() -> list[_Turn]:
    """A mix including a filler-raced-looking record: tool_ms==0.0 but a
    fast ttfa (528ms) that, if trusted blindly, would land in the no-lookup
    bucket and pull its p95 down -- exactly the shape the live verifier
    reproduction (test_tool_wiring.py) would have produced pre-fix."""
    turns = [_turn(i, model="claude-convo-test", ttfa_ms=600.0 + i, tool_ms=0.0) for i in range(30)]
    turns += [
        _turn(100 + i, model="claude-tool-test", ttfa_ms=2500.0 + i, tool_ms=1200.0 + i)
        for i in range(20)
    ]
    # the ambiguous one: looks fast and no-lookup by tool_ms alone.
    turns.append(_turn(999, model="claude-convo-test", ttfa_ms=528.0, tool_ms=0.0))
    return turns


def test_no_driver_json_never_emits_an_r1_verdict_from_tool_ms_inference_alone():
    """DIAGNOSTIC-ONLY: the tool_ms-inferred split must be shown, but no
    PASS/FAIL verdict computed from it -- an unverified 0 must not
    masquerade as a confirmed fast sample."""
    report = _render_report(
        _mixed_turns(), min_turns=50, source=Path("fake.jsonl"), categories=None
    )

    assert "By lookup status" in report
    assert "DIAGNOSTIC ONLY" in report
    assert "CANNOT BE CERTIFIED without" in report
    # explicitly must NOT contain a PASS or FAIL verdict for the inferred split
    assert "R1 status (no-lookup turns only): PASS" not in report
    assert "R1 status (no-lookup turns only): FAIL" not in report
    assert "R1 status (no-lookup turns only, from tool_ms)" not in report  # old (pre-fix) wording


def test_the_filler_raced_looking_record_is_visible_in_the_diagnostic_breakdown_but_uncertified():
    """The ambiguous record (tool_ms=0.0, fast ttfa) is still counted in the
    diagnostic breakdown -- this table isn't hidden, just never trusted for
    a verdict on its own."""
    turns = _mixed_turns()
    report = _render_report(turns, min_turns=50, source=Path("fake.jsonl"), categories=None)

    no_lookup_count = sum(1 for t in turns if t.tool_ms <= 0)
    assert f"| no-lookup (tool_ms<=0) | {no_lookup_count} |" in report


def test_driver_json_ground_truth_still_certifies_a_real_r1_verdict():
    """When ground truth (driver categories) IS available, an authoritative
    R1 PASS/FAIL verdict must still be computed -- the fix only removes the
    UNVERIFIED verdict, not the legitimate one."""
    turns = _mixed_turns()
    categories = ["chat"] * 30 + ["github"] * 20 + ["chat"]  # matches _mixed_turns()'s shape

    report = _render_report(
        turns, min_turns=50, source=Path("fake.jsonl"), categories=categories
    )

    assert "By question type" in report
    assert (
        "R1 status (no-lookup turns only): PASS" in report
        or "R1 status (no-lookup turns only): FAIL" in report
    )
    # the diagnostic-only, uncertified table must not appear when ground
    # truth is available.
    assert "DIAGNOSTIC ONLY" not in report
