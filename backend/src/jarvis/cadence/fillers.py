"""Filler clip bank: manifest loading + no-immediate-repeat selection (T-6).

``load_manifest`` reads a ``manifest.json`` (built by
``scripts/build_fillers.py``, a sibling ticket's scope -- this module only
consumes the manifest, never generates audio). Absence of the manifest or
its audio files is an expected, cleanly-degrading condition: callers get a
``FileNotFoundError``/``ValueError`` rather than a partially-built bank.

Asset resolution (T-26): ``ASSETS_DIR`` used to be computed as
``Path(__file__).resolve().parents[3] / "assets" / "fillers"`` -- correct
only for the exact source-tree depth
``backend/src/jarvis/cadence/fillers.py``. Installed as a wheel, ``jarvis``
lands under ``site-packages/`` at a different depth and that arithmetic
points at the wrong directory. ``_resolve_assets_dir`` below replaces the
fixed parent index with two depth-independent strategies -- see its
docstring.

The assets live in exactly one committed place:
``backend/src/jarvis/cadence/assets/fillers/`` -- which, being inside the
``jarvis`` package tree, rides into the wheel and the Docker image with no
``backend/pyproject.toml``/``backend/Dockerfile`` changes. ``scripts/build_fillers.py``
writes directly into this same directory (T-30 collapsed what used to be a
second, separately-committed copy at ``backend/assets/fillers/`` into this
one location, so a rebuild can never again strand a packaged copy stale).
The ``importlib.resources`` strategy below is tried first and finds this
directory in every arrangement (source checkout, wheel, or container); the
upward-search fallback exists for completeness but the packaged copy is
what actually resolves in this repo. See docs/filler-asset-packaging.md for
the full writeup and container evidence.
"""

from __future__ import annotations

import importlib.resources
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_ASSETS_SUBPATH: Final[tuple[str, str]] = ("assets", "fillers")
_MANIFEST_NAME: Final[str] = "manifest.json"
_MAX_UPWARD_SEARCH: Final[int] = 8


def _resolve_assets_dir(
    start_file: Path | None = None,
    *,
    resource_anchor: str = "jarvis.cadence",
) -> Path:
    """Locate the fillers assets directory without depending on *how many*
    directories separate this module from it.

    Two strategies, tried in order -- neither hardcodes a specific
    ``parents[N]`` index:

    1. **importlib.resources**, anchored on the installed
       ``resource_anchor`` package (``jarvis.cadence`` in production).  This
       resolves to wherever Python actually put the package -- editable
       install, wheel unpacked into ``site-packages``, zipapp, ... -- rather
       than assuming a fixed number of parent hops from this file. It finds
       the assets iff they were shipped as package data under
       ``<anchor>/assets/fillers``; whether that is true today is a
       packaging-config question outside this function's control (see the
       module docstring).
    2. A **bounded upward search** from ``start_file`` (default
       ``__file__``) for an ``assets/fillers/manifest.json`` sibling,
       capped at ``_MAX_UPWARD_SEARCH`` levels. This covers a source
       checkout (or any layout where the assets ship as loose files near
       the package rather than packaged inside it) without hardcoding
       *which* parent index is the right one -- it keeps walking up until
       it finds the marker file or gives up, so it is correct regardless of
       how many directories sit between this file and the repo layout.

    Neither strategy is guaranteed to find anything (e.g. a wheel installed
    without the assets declared as package data, or a source tree with the
    assets deleted). In that case this falls back to the historical
    source-tree-shaped guess purely so ``MANIFEST_PATH`` is a stable path
    for the existing ``FileNotFoundError`` (from ``load_manifest``) to
    mention -- it does not touch the filesystem beyond the searches above.
    """
    try:
        anchor = importlib.resources.files(resource_anchor)
        candidate = anchor.joinpath(*_ASSETS_SUBPATH)
        if candidate.joinpath(_MANIFEST_NAME).is_file():
            with importlib.resources.as_file(candidate) as resolved:
                return Path(resolved)
    except (ModuleNotFoundError, FileNotFoundError, NotADirectoryError):
        pass

    here = (start_file if start_file is not None else Path(__file__)).resolve()
    search_root = here.parent
    candidates = [search_root, *list(search_root.parents)[: _MAX_UPWARD_SEARCH - 1]]
    for parent in candidates:
        candidate = parent.joinpath(*_ASSETS_SUBPATH)
        if candidate.joinpath(_MANIFEST_NAME).is_file():
            return candidate

    # Nothing found by either strategy -- keep the historical source-tree
    # shape as a last-resort guess so downstream FileNotFoundErrors point
    # somewhere legible instead of an arbitrary empty path.
    fallback_root = search_root.parents[2] if len(search_root.parents) > 2 else search_root
    return fallback_root.joinpath(*_ASSETS_SUBPATH)


ASSETS_DIR: Final[Path] = _resolve_assets_dir()
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
