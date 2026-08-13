"""CLI: compute time-to-first-audio latency stats from the recorded JSONL (T-9).

Reads the per-turn latency log that ``jarvis.pipeline.latency.LatencyLogger``
writes (one JSON object per line: ``{turn_id, turn_end_ts, first_audio_ts,
tool_ms, model}`` -- see ``backend/src/jarvis/pipeline/latency.py``) and
reports time-to-first-audio (``first_audio_ts - turn_end_ts``, both Unix
epoch seconds from ``time.time()``) in milliseconds: p50, p95, max, turn
count, and a per-model breakdown.

R1 (docs/SPEC.md) requires p95 <= 800ms time-to-first-audio over >= 50
deployed turns. This tool refuses to produce a report from fewer than
``--min-turns`` (default 50) turns -- exiting non-zero and saying exactly how
many valid turns it found -- so a handful of local samples can never be
mistaken for R1 evidence. Malformed lines are skipped with a warning rather
than crashing the run.

Runnable from any cwd: ``uv run python scripts/latency_report.py`` from
``backend/``, or ``python3 scripts/latency_report.py`` from the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# repo_root/scripts/latency_report.py -> parents[1] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_INPUT = _REPO_ROOT / "backend" / "var" / "latency.jsonl"
_DEFAULT_MIN_TURNS = 50

# Required fields per the pinned JSONL schema (jarvis.pipeline.latency).
_REQUIRED_FIELDS = ("turn_id", "turn_end_ts", "first_audio_ts", "tool_ms", "model")


@dataclass(frozen=True)
class _Turn:
    turn_id: int
    model: str
    ttfa_ms: float
    tool_ms: float


def _parse_line(line: str, *, line_no: int, source: str) -> _Turn | None:
    """Parse one JSONL line into a `_Turn`, or None (with a stderr warning)
    if the line is blank, malformed JSON, missing a required field, has the
    wrong type, or has a non-positive/negative time span."""
    stripped = line.strip()
    if not stripped:
        return None

    try:
        record = json.loads(stripped)
    except json.JSONDecodeError as exc:
        print(f"WARN {source}:{line_no}: skipping malformed JSON ({exc})", file=sys.stderr)
        return None

    if not isinstance(record, dict):
        print(f"WARN {source}:{line_no}: skipping non-object line", file=sys.stderr)
        return None

    missing = [field for field in _REQUIRED_FIELDS if field not in record]
    if missing:
        print(
            f"WARN {source}:{line_no}: skipping line missing field(s) {missing}",
            file=sys.stderr,
        )
        return None

    try:
        turn_id = int(record["turn_id"])
        turn_end_ts = float(record["turn_end_ts"])
        first_audio_ts = float(record["first_audio_ts"])
        tool_ms = float(record["tool_ms"])
        model = str(record["model"])
    except (TypeError, ValueError) as exc:
        print(
            f"WARN {source}:{line_no}: skipping line with bad field type ({exc})",
            file=sys.stderr,
        )
        return None

    ttfa_ms = (first_audio_ts - turn_end_ts) * 1000.0
    if ttfa_ms < 0:
        print(
            f"WARN {source}:{line_no}: skipping turn {turn_id} with negative "
            f"time-to-first-audio ({ttfa_ms:.1f}ms)",
            file=sys.stderr,
        )
        return None

    return _Turn(turn_id=turn_id, model=model, ttfa_ms=ttfa_ms, tool_ms=tool_ms)


def _load_turns(path: Path) -> list[_Turn]:
    turns: list[_Turn] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            turn = _parse_line(line, line_no=line_no, source=str(path))
            if turn is not None:
                turns.append(turn)
    return turns


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list (pct in [0, 100])."""
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty list")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
        "count": float(len(ordered)),
    }


def _render_report(turns: list[_Turn], *, min_turns: int, source: Path) -> str:
    overall = _stats([t.ttfa_ms for t in turns])

    by_model: dict[str, list[_Turn]] = defaultdict(list)
    for t in turns:
        by_model[t.model].append(t)

    lines = [
        "# Latency report: time-to-first-audio",
        "",
        f"Source: `{source}`",
        f"Turns analyzed: {len(turns)} (minimum required: {min_turns})",
        "",
        "## Overall",
        "",
        "| metric | ms |",
        "| --- | --- |",
        f"| p50 | {overall['p50']:.1f} |",
        f"| p95 | {overall['p95']:.1f} |",
        f"| max | {overall['max']:.1f} |",
        f"| count | {int(overall['count'])} |",
        "",
        "R1 requires p95 <= 800ms over >= 50 turns.",
        f"R1 status: {'PASS' if overall['p95'] <= 800 and len(turns) >= 50 else 'FAIL'}"
        f" (p95={overall['p95']:.1f}ms, n={len(turns)})",
        "",
        "## By model",
        "",
        "| model | count | p50 (ms) | p95 (ms) | max (ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for model in sorted(by_model):
        model_stats = _stats([t.ttfa_ms for t in by_model[model]])
        lines.append(
            f"| {model} | {int(model_stats['count'])} | {model_stats['p50']:.1f} | "
            f"{model_stats['p95']:.1f} | {model_stats['max']:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute time-to-first-audio latency stats from the latency JSONL log."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help=f"Path to the latency JSONL log (default: {_DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--min-turns",
        type=int,
        default=_DEFAULT_MIN_TURNS,
        help=(
            "Minimum number of valid turns required to produce a report "
            f"(default: {_DEFAULT_MIN_TURNS})."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="If given, also write the Markdown report to this path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 1

    turns = _load_turns(args.input)

    if len(turns) < args.min_turns:
        print(
            f"ERROR: found only {len(turns)} valid turn(s) in {args.input}, "
            f"but --min-turns requires {args.min_turns}. Refusing to produce a "
            "report -- R1 compliance cannot be claimed from too few samples.",
            file=sys.stderr,
        )
        return 1

    report = _render_report(turns, min_turns=args.min_turns, source=args.input)
    print(report)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"\nWrote report to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
