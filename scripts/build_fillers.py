"""CLI: pre-synthesize the six cadence filler phrases (T-6).

Writes 16-bit mono PCM WAV clips plus a manifest.json into
``backend/assets/fillers/`` (schema pinned in ``.claude/scratch/t6-contract.md``,
consumed by ``jarvis.cadence.fillers.load_manifest``).

Two engines:
    cartesia    Real pipeline TTS via ``jarvis.pipeline.providers.build_tts()``,
                so filler clips match the live pipeline's voice exactly.
    macos-say   Offline stand-in via ``/usr/bin/say`` + ``/usr/bin/afconvert``,
                for environments with no Cartesia API key.

Runnable from any cwd: ``uv run python scripts/build_fillers.py`` from
``backend/``, or ``python3 scripts/build_fillers.py`` from the repo root.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# repo_root/scripts/build_fillers.py -> parents[1] == repo root. Resolved from
# __file__ so behavior does not depend on the process's cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

_DEFAULT_OUT_DIR = _REPO_ROOT / "backend" / "assets" / "fillers"
_DEFAULT_SAY_VOICE = "Samantha"
_SCHEMA_VERSION = 1

# Exact slugs/phrases/order pinned in the T-6 contract's "Filler bank" table;
# must match jarvis.cadence.fillers.FILLER_PHRASES.
FILLER_PHRASES: tuple[tuple[str, str], ...] = (
    ("one-sec", "One sec."),
    ("let-me-check", "Let me check."),
    ("give-me-a-mo", "Give me a moment."),
    ("looking-it-up", "Looking that up."),
    ("on-it", "On it."),
    ("checking-now", "Checking on that now."),
)


class FillerBuildError(Exception):
    """An actionable, user-facing build failure."""


@dataclass(frozen=True)
class _ClipResult:
    slug: str
    text: str
    file: str
    duration_ms: int
    sha256: str
    channels: int
    sample_rate: int


def _wav_stats(path: Path) -> tuple[int, int, int]:
    """Read back (channels, sample_rate, duration_ms) from a written wav file."""
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
    duration_ms = round(frames / sample_rate * 1000) if sample_rate else 0
    return channels, sample_rate, duration_ms


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clip_result(slug: str, text: str, path: Path) -> _ClipResult:
    channels, sample_rate, duration_ms = _wav_stats(path)
    return _ClipResult(
        slug=slug,
        text=text,
        file=path.name,
        duration_ms=duration_ms,
        sha256=_sha256(path),
        channels=channels,
        sample_rate=sample_rate,
    )


def _synthesize_macos_say(text: str, out_path: Path, voice: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        aiff_path = Path(tmp) / "clip.aiff"
        subprocess.run(
            ["/usr/bin/say", "-v", voice, "-o", str(aiff_path), text],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "/usr/bin/afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@24000",
                "-c",
                "1",
                str(aiff_path),
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )


def _run_macos_say(
    phrases: tuple[tuple[str, str], ...], out_dir: Path, voice: str | None, force: bool
) -> tuple[str, list[_ClipResult]]:
    voice_label = voice or _DEFAULT_SAY_VOICE
    results = []
    for slug, text in phrases:
        out_path = out_dir / f"{slug}.wav"
        if out_path.exists() and not force:
            print(f"skip  {out_path} (exists; use --force to regenerate)")
        else:
            _synthesize_macos_say(text, out_path, voice_label)
            print(f"wrote {out_path}")
        results.append(_clip_result(slug, text, out_path))
    return voice_label, results


async def _synthesize_cartesia(text: str, out_path: Path, tts: object) -> None:
    frames = bytearray()
    sample_rate: int | None = None
    num_channels: int | None = None
    stream = tts.synthesize(text)  # type: ignore[attr-defined]
    async with stream:
        async for audio in stream:
            frame = audio.frame
            if sample_rate is None:
                sample_rate, num_channels = frame.sample_rate, frame.num_channels
            frames += frame.data.tobytes()
    if sample_rate is None or num_channels is None or not frames:
        raise FillerBuildError(f"cartesia TTS returned no audio for phrase: {text!r}")
    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


async def _run_cartesia(
    phrases: tuple[tuple[str, str], ...], out_dir: Path, voice: str | None, force: bool
) -> tuple[str, list[_ClipResult]]:
    if not os.environ.get("CARTESIA_API_KEY"):
        raise FillerBuildError(
            "CARTESIA_API_KEY is not set. Export it (see backend/.env.example) to "
            "synthesize fillers with --engine cartesia, or pass --engine macos-say "
            "for an offline stand-in build."
        )

    from jarvis.pipeline.providers import build_tts  # lazy: keeps macos-say LiveKit-free

    tts = build_tts()
    voice_label = voice or "default"
    if voice:
        update_options = getattr(tts, "update_options", None)
        if update_options is None:
            raise FillerBuildError(f"{type(tts).__name__} does not support --voice overrides")
        update_options(voice=voice)

    results = []
    try:
        for slug, text in phrases:
            out_path = out_dir / f"{slug}.wav"
            if out_path.exists() and not force:
                print(f"skip  {out_path} (exists; use --force to regenerate)")
            else:
                await _synthesize_cartesia(text, out_path, tts)
                print(f"wrote {out_path}")
            results.append(_clip_result(slug, text, out_path))
    finally:
        aclose = getattr(tts, "aclose", None)
        if aclose is not None:
            await aclose()
    return voice_label, results


def _write_manifest(out_dir: Path, engine: str, voice: str, clips: list[_ClipResult]) -> Path:
    sample_rates = {c.sample_rate for c in clips}
    channel_counts = {c.channels for c in clips}
    if len(sample_rates) != 1 or len(channel_counts) != 1:
        raise FillerBuildError(
            f"inconsistent audio format across clips: sample_rates={sample_rates}, "
            f"channels={channel_counts}"
        )
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "engine": engine,
        "voice": voice,
        "sample_rate": sample_rates.pop(),
        "channels": channel_counts.pop(),
        # datetime.UTC (ruff UP017) needs Python 3.11+; this script must also run
        # under whichever bare "python3" is on PATH per the repo-root invocation
        # contract, which is not guaranteed to be 3.11+.
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # noqa: UP017
        "clips": [
            {
                "slug": c.slug,
                "text": c.text,
                "file": c.file,
                "duration_ms": c.duration_ms,
                "sha256": c.sha256,
            }
            for c in clips
        ],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pre-synthesize cadence filler clips.")
    parser.add_argument(
        "--engine",
        choices=("cartesia", "macos-say"),
        default="cartesia",
        help="synthesis engine (default: cartesia, the real pipeline TTS)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help="output directory (default: backend/assets/fillers)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="say-voice name (macos-say) or Cartesia voice id (cartesia)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-synthesize clips that already exist",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    out_dir = args.out if args.out.is_absolute() else _REPO_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.engine == "macos-say":
            voice, clips = _run_macos_say(FILLER_PHRASES, out_dir, args.voice, args.force)
        else:
            voice, clips = asyncio.run(
                _run_cartesia(FILLER_PHRASES, out_dir, args.voice, args.force)
            )
        manifest_path = _write_manifest(out_dir, args.engine, voice, clips)
    except FillerBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
        print(f"error: {exc.cmd} failed (exit {exc.returncode}): {stderr}", file=sys.stderr)
        return 1

    print(f"manifest written to {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
