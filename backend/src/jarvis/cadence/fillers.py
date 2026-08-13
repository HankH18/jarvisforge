"""Filler clip bank: manifest loading + no-immediate-repeat selection (T-6).

``load_manifest`` reads ``backend/assets/fillers/manifest.json`` (built by
``scripts/build_fillers.py``, a sibling ticket's scope -- this module only
consumes the manifest, never generates audio). Absence of the manifest or
its audio files is an expected, cleanly-degrading condition: callers get a
``FileNotFoundError``/``ValueError`` rather than a partially-built bank.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# backend/src/jarvis/cadence/fillers.py -> parents[3] == backend
ASSETS_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "assets" / "fillers"
MANIFEST_PATH: Final[Path] = ASSETS_DIR / "manifest.json"

FILLER_PHRASES: Final[tuple[str, ...]] = (
    "One sec.",
    "Let me check.",
    "Give me a moment.",
    "Looking that up.",
    "On it.",
    "Checking on that now.",
)

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FillerClip:
    slug: str
    text: str
    path: Path
    duration_ms: int


def load_manifest(path: Path | None = None) -> list[FillerClip]:
    """Parse manifest.json into clips.

    Raises ``FileNotFoundError`` if ``path`` is absent, ``ValueError`` if a
    listed audio file is missing or the schema version is unknown.
    """
    manifest_path = path if path is not None else MANIFEST_PATH
    with manifest_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    schema_version = raw.get("schema_version")
    if schema_version != _SCHEMA_VERSION:
        raise ValueError(f"unknown manifest schema_version: {schema_version!r}")

    clips_dir = manifest_path.resolve().parent
    clips: list[FillerClip] = []
    for entry in raw.get("clips", []):
        audio_path = clips_dir / entry["file"]
        if not audio_path.is_file():
            raise ValueError(f"manifest entry {entry['slug']!r} missing audio file: {audio_path}")
        clips.append(
            FillerClip(
                slug=entry["slug"],
                text=entry["text"],
                path=audio_path,
                duration_ms=entry["duration_ms"],
            )
        )
    return clips


class FillerBank:
    def __init__(self, clips: Sequence[FillerClip], *, rng: random.Random | None = None) -> None:
        """Raises ValueError on an empty clip list."""
        if not clips:
            raise ValueError("FillerBank requires at least one clip")
        self._clips: tuple[FillerClip, ...] = tuple(clips)
        self._rng = rng if rng is not None else random.Random()
        self._last: FillerClip | None = None

    @classmethod
    def from_manifest(
        cls, path: Path | None = None, *, rng: random.Random | None = None
    ) -> FillerBank:
        return cls(load_manifest(path), rng=rng)

    @property
    def last(self) -> FillerClip | None:
        return self._last

    def __len__(self) -> int:
        return len(self._clips)

    def next(self) -> FillerClip:
        """Random pick that is never the immediately-previous clip (unless
        only one clip exists, in which case it repeats)."""
        if len(self._clips) == 1:
            choice = self._clips[0]
        else:
            candidates = [c for c in self._clips if c is not self._last]
            choice = self._rng.choice(candidates)
        self._last = choice
        return choice
