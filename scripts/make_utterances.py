"""CLI: pre-synthesize a bank of spoken test utterances (T-9).

Writes 16-bit mono PCM WAV clips plus a manifest.json into
``scripts/utterances/``. These are real, clearly-articulated speech clips
(synthesized with the project's own Cartesia TTS) meant to be played at the
live agent to generate real turns for latency measurement -- they exercise
weather (T-3), GitHub (T-4), the self-capability manifest (T-2), memory
(T-5), and a couple of plain conversational turns that need no tool at all.

Mirrors ``scripts/build_fillers.py``'s Cartesia synthesis path exactly,
including wrapping synthesis in ``livekit.agents.utils.http_context.open()``
-- without that, ``synthesize()`` raises "Attempted to use an http session
outside of a job context" because the LiveKit TTS plugin borrows its aiohttp
session from the agent job context, and this script is not a job.

Runnable from any cwd: ``uv run python scripts/make_utterances.py`` from
``backend/``, or ``python3 scripts/make_utterances.py`` from the repo root.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# repo_root/scripts/make_utterances.py -> parents[1] == repo root. Resolved
# from __file__ so behavior does not depend on the process's cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

_DEFAULT_OUT_DIR = _REPO_ROOT / "scripts" / "utterances"
_SCHEMA_VERSION = 1

# (slug, text, category) -- 12 utterances spread across the product's real
# capabilities. Kept short and clearly articulated: long utterances make
# turns slower and add STT error, which is exactly what this bank exists to
# avoid introducing as noise into the latency measurement.
UTTERANCES: tuple[tuple[str, str, str], ...] = (
    # Weather (T-3 tool)
    ("weather-london", "What's the weather in London right now?", "weather"),
    ("weather-tokyo", "Is it raining in Tokyo today?", "weather"),
    # GitHub (T-4 tool)
    (
        "github-octocat-issues",
        "How many open issues does octocat slash Hello World have?",
        "github",
    ),
    ("github-repo-stars", "How many stars does torvalds slash linux have?", "github"),
    # Self-capability (T-2 manifest)
    ("capability-what-can-you-do", "What can you actually do?", "capability"),
    ("capability-list-tools", "What tools do you have access to?", "capability"),
    # Memory (T-5)
    (
        "memory-remember-celsius",
        "Remember that I prefer temperatures in Celsius.",
        "memory",
    ),
    ("memory-recall-preference", "What temperature unit do I prefer?", "memory"),
    # Plain conversation, no tool needed -- these are the ones that must hit
    # R1's 800ms time-to-first-audio bar, since no lookup is involved.
    ("chat-how-are-you", "How are you doing today?", "chat"),
    ("chat-tell-joke", "Tell me a short joke.", "chat"),
    ("chat-favorite-color", "What's your favorite color?", "chat"),
    ("chat-good-morning", "Good morning, how's it going?", "chat"),
)


class UtteranceBuildError(Exception):
    """An actionable, user-facing build failure."""


@dataclass(frozen=True)
class _ClipResult:
    slug: str
    text: str
    category: str
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


def _clip_result(slug: str, text: str, category: str, path: Path) -> _ClipResult:
    channels, sample_rate, duration_ms = _wav_stats(path)
    return _ClipResult(
        slug=slug,
        text=text,
        category=category,
        file=path.name,
        duration_ms=duration_ms,
        sha256=_sha256(path),
        channels=channels,
        sample_rate=sample_rate,
    )


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
        raise UtteranceBuildError(f"cartesia TTS returned no audio for utterance: {text!r}")
    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


async def _run_cartesia(
    utterances: tuple[tuple[str, str, str], ...], out_dir: Path, voice: str | None, force: bool
) -> tuple[str, list[_ClipResult]]:
    if not os.environ.get("CARTESIA_API_KEY"):
        raise UtteranceBuildError(
            "CARTESIA_API_KEY is not set. Export it (see .env.example) to synthesize "
            "utterances -- this script has no offline stand-in, since these clips must "
            "be real speech the deployed agent will actually hear and transcribe."
        )

    from livekit.agents.utils import http_context

    # LiveKit TTS plugins borrow an aiohttp session from the agent job context.
    # This script is not a job, so without opening one explicitly every
    # synthesize() dies with "Attempted to use an http session outside of a job
    # context". Opening it here is what makes the real Cartesia path runnable
    # from a plain CLI (mirrors scripts/build_fillers.py).
    async with http_context.open():
        return await _run_cartesia_synthesis(utterances, out_dir, voice, force)


async def _run_cartesia_synthesis(
    utterances: tuple[tuple[str, str, str], ...], out_dir: Path, voice: str | None, force: bool
) -> tuple[str, list[_ClipResult]]:
    from jarvis.pipeline.providers import build_tts

    tts = build_tts()
    voice_label = voice or "default"
    if voice:
        update_options = getattr(tts, "update_options", None)
        if update_options is None:
            raise UtteranceBuildError(f"{type(tts).__name__} does not support --voice overrides")
        update_options(voice=voice)

    results = []
    try:
        for slug, text, category in utterances:
            out_path = out_dir / f"{slug}.wav"
            if out_path.exists() and not force:
                print(f"skip  {out_path} (exists; use --force to regenerate)")
            else:
                await _synthesize_cartesia(text, out_path, tts)
                print(f"wrote {out_path}")
            results.append(_clip_result(slug, text, category, out_path))
    finally:
        aclose = getattr(tts, "aclose", None)
        if aclose is not None:
            await aclose()
    return voice_label, results


def _write_manifest(out_dir: Path, engine: str, voice: str, clips: list[_ClipResult]) -> Path:
    sample_rates = {c.sample_rate for c in clips}
    channel_counts = {c.channels for c in clips}
    if len(sample_rates) != 1 or len(channel_counts) != 1:
        raise UtteranceBuildError(
            f"inconsistent audio format across clips: sample_rates={sample_rates}, "
            f"channels={channel_counts}"
        )
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "engine": engine,
        "voice": voice,
        "sample_rate": sample_rates.pop(),
        "channels": channel_counts.pop(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # noqa: UP017
        "clips": [
            {
                "slug": c.slug,
                "text": c.text,
                "category": c.category,
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
    parser = argparse.ArgumentParser(
        description="Pre-synthesize spoken test utterances (weather/GitHub/capability/memory/chat)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help="output directory (default: scripts/utterances)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Cartesia voice id override",
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
        voice, clips = asyncio.run(_run_cartesia(UTTERANCES, out_dir, args.voice, args.force))
        manifest_path = _write_manifest(out_dir, "cartesia", voice, clips)
    except UtteranceBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"manifest written to {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
