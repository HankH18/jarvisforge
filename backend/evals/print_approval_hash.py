"""T-29: print the canonical content hash a human should paste into
golden.jsonl's `_meta.approved_sha256` after reviewing the golden set.

This tool ONLY PRINTS. It deliberately never writes `golden.jsonl` itself --
mirroring T-25's non-goal that no code path may fill in `approved_by`/
`approved_at` either. An approval is a human act: this script exists so a
human doesn't have to hand-compute a sha256, not so an agent can mint one.
See `backend/evals/README.md`, "Approval binds to content".

The hash it prints is produced by `evals.scoring.golden_content_sha256`,
the EXACT SAME function `evals/run.py`'s approval gate calls to verify
`_meta.approved_sha256` -- see that function's docstring for the precise
canonicalization (which line is excluded, blank-line handling, line
ordering, trailing newline, encoding). Sharing one function is what makes
the helper and the gate incapable of drifting apart.

Usage (from `backend/`):
    uv run python -m evals.print_approval_hash --golden evals/golden.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.scoring import golden_content_sha256


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print the T-29 canonical content hash of a golden set (every line "
            "except _meta) for a human to paste into _meta.approved_sha256. "
            "Prints ONLY -- never writes golden.jsonl itself."
        )
    )
    parser.add_argument("--golden", required=True, type=Path, help="path to golden.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    try:
        digest = golden_content_sha256(args.golden)
    except (ValueError, OSError) as exc:
        print(f"error: failed to hash {args.golden}: {exc}", file=sys.stderr)
        return 2
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
