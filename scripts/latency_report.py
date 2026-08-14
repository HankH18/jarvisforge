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


def _load_categories(driver_json: Path, n_turns: int) -> list[str] | None:
    """Per-turn utterance categories from a scripts/drive_turns.py --json run.

    R1's 800ms bar is conditioned on replies "that need no external lookup",
    but the worker's JSONL cannot express that: ``tool_ms`` is recorded as 0.0
    even on turns where a tool fired, so lookup and no-lookup turns are
    indistinguishable in the log alone. The driver knows which utterance drove
    each turn, and it drives them strictly sequentially into one room, so index
    i of its ``turns`` array is index i of the worker log. Returns None if the
    two do not line up, rather than silently mis-labelling turns.
    """
    data = json.loads(driver_json.read_text())
    driver_turns = data.get("turns", []) if isinstance(data, dict) else data
    if len(driver_turns) != n_turns:
        return None
    return [str(t.get("utterance_file", "unknown")).split("-")[0] for t in driver_turns]


def _render_report(
    turns: list[_Turn], *, min_turns: int, source: Path, categories: list[str] | None = None
) -> str:
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
        f"R1 status (all turns): {'PASS' if overall['p95'] <= 800 and len(turns) >= 50 else 'FAIL'}"
        f" (p95={overall['p95']:.1f}ms, n={len(turns)})",
        "",]

    if categories:
        by_cat: dict[str, list[float]] = defaultdict(list)
        for idx, turn in enumerate(turns):
            by_cat[categories[idx]].append(turn.ttfa_ms)
        # "chat" utterances are the ones answerable with no tool call, which is
        # the population R1's 800ms bar is actually defined over.
        lines += [
            "## By question type",
            "",
            "R1 pins 800ms for replies that need **no external lookup**. This split comes"
            " from ground truth -- the driver's own utterance categories (`--driver-json`)"
            " -- so, unlike the `tool_ms`-inferred fallback used when `--driver-json` isn't"
            " given, it can certify an R1 PASS/FAIL verdict below.",
            "",
            "| category | count | p50 (ms) | p95 (ms) | max (ms) | external lookup |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for cat in sorted(by_cat):
            cs = _stats(by_cat[cat])
            lookup = "no" if cat == "chat" else "yes"
            lines.append(
                f"| {cat} | {int(cs['count'])} | {cs['p50']:.1f} | {cs['p95']:.1f} | "
                f"{cs['max']:.1f} | {lookup} |"
            )
        lines.append("")
        if "chat" in by_cat:
            cs = _stats(by_cat["chat"])
            verdict = "PASS" if cs["p95"] <= 800 else "FAIL"
            lines += [
                f"**R1 status (no-lookup turns only): {verdict}** "
                f"(p95={cs['p95']:.1f}ms, p50={cs['p50']:.1f}ms, n={int(cs['count'])}).",
                "",
            ]
    else:
        # T-16, revised after an independent verifier reproduced a real
        # defect: tool_ms IS now populated with the real measured tool span
        # in the realistic case (jarvis.pipeline.latency.LatencyLogger's
        # note_tool_dispatch_started()/note_tool_span() fix -- see that
        # module's docstring for the full story, including a narrower
        # documented residual risk that is NOT fully closed). But this
        # script has no independent ground truth to confirm a `tool_ms`-only
        # inference against -- the JSONL schema is pinned to exactly
        # {turn_id, turn_end_ts, first_audio_ts, tool_ms, model}
        # (docs/DESIGN.md §Data models), with no explicit "a tool fired"
        # field, precisely so a schema change wasn't smuggled in as a side
        # effect of this fix. A WRONG inference here would silently flatter
        # the no-lookup population's p95 -- exactly the failure mode this
        # table exists to route around -- so this section is DIAGNOSTIC
        # ONLY: it reports the split but NEVER emits an R1 PASS/FAIL
        # verdict. Re-run with --driver-json (the "By question type" table
        # above, which comes from ground truth -- which utterance was
        # actually asked -- not inference) for an authoritative R1 verdict.
        no_lookup = [t.ttfa_ms for t in turns if t.tool_ms <= 0]
        lookup = [t.ttfa_ms for t in turns if t.tool_ms > 0]
        lines += [
            "## By lookup status (from tool_ms) -- DIAGNOSTIC ONLY, not an R1 verdict",
            "",
            "No `--driver-json` was given. This split is INFERRED from each record's"
            " `tool_ms` (`> 0` means a tool fired this turn -- a lookup; `== 0` means"
            " it didn't), not read from an explicit ground-truth field -- an inference,"
            " not a verified fact, so **no R1 PASS/FAIL verdict is computed from it**."
            " An unverified 0 must not masquerade as a confirmed fast sample. Re-run"
            " with `--driver-json` for an authoritative no-lookup-only R1 verdict.",
            "",
            "| status (inferred) | count | p50 (ms) | p95 (ms) | max (ms) |",
            "| --- | --- | --- | --- | --- |",
        ]
        buckets = (("no-lookup (tool_ms<=0)", no_lookup), ("lookup (tool_ms>0)", lookup))
        for label, values in buckets:
            if not values:
                continue
            vs = _stats(values)
            lines.append(
                f"| {label} | {int(vs['count'])} | {vs['p50']:.1f} | {vs['p95']:.1f} | "
                f"{vs['max']:.1f} |"
            )
        lines.append("")
        lines += [
            f"**R1 status (no-lookup turns only): CANNOT BE CERTIFIED without"
            f" `--driver-json`** -- {len(no_lookup)} of {len(turns)} turn(s) look"
            " no-lookup by tool_ms inference alone; that is not accepted as R1"
            " evidence on its own.",
            "",
        ]
        print(
            f"NOTE: {len(no_lookup)}/{len(turns)} turn(s) classified no-lookup by "
            "tool_ms inference only (no --driver-json) -- no R1 verdict computed "
            "for them; re-run with --driver-json for a certified verdict.",
            file=sys.stderr,
        )

    lines += [
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
        "--driver-json",
        type=Path,
        default=None,
        help=(
            "Optional scripts/drive_turns.py --json output. Used to split turns into "
            "lookup vs no-lookup, which is the population R1's 800ms bar is defined over."
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

    categories = None
    if args.driver_json is not None:
        categories = _load_categories(args.driver_json, len(turns))
        if categories is None:
            print(
                f"warning: {args.driver_json} does not have one entry per worker turn; "
                "skipping the by-question-type split rather than mislabelling turns.",
                file=sys.stderr,
            )
    report = _render_report(
        turns, min_turns=args.min_turns, source=args.input, categories=categories
    )
    print(report)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"\nWrote report to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
