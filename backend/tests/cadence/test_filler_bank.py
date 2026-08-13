"""FillerBank: random selection with no immediate repeat, and manifest
loading (jarvis.cadence.fillers). Uses cadence_fakes.make_clips throughout so these
tests never depend on the real committed assets under
backend/assets/fillers/ -- that is test_assets.py's job.
"""

from __future__ import annotations

import json
import random

import pytest
from cadence_fakes import make_clips

from jarvis.cadence.fillers import FILLER_PHRASES, FillerBank, FillerClip, load_manifest

# ---- FILLER_PHRASES --------------------------------------------------------


def test_filler_phrases_are_the_six_pinned_phrases():
    assert len(FILLER_PHRASES) == 6
    assert set(FILLER_PHRASES) == {
        "One sec.",
        "Let me check.",
        "Give me a moment.",
        "Looking that up.",
        "On it.",
        "Checking on that now.",
    }


# ---- FillerBank construction ------------------------------------------------


def test_empty_clip_list_raises_value_error():
    with pytest.raises(ValueError):
        FillerBank([])


def test_len_reports_the_clip_count(tmp_path):
    bank = FillerBank(make_clips(4, tmp_path))
    assert len(bank) == 4


def test_last_is_none_before_any_selection(tmp_path):
    bank = FillerBank(make_clips(3, tmp_path))
    assert bank.last is None


# ---- no-immediate-repeat selection ------------------------------------------


def test_next_never_immediately_repeats_the_previous_clip(tmp_path):
    clips = make_clips(4, tmp_path)
    # Seeded rng: the invariant under test is "no immediate repeat", not any
    # particular sequence -- the seed only turns a flaky-by-nature random
    # process into a deterministic, reproducible test run.
    bank = FillerBank(clips, rng=random.Random(20260813))

    previous = None
    for _ in range(200):
        clip = bank.next()
        if previous is not None:
            assert clip.slug != previous.slug
        assert bank.last is clip
        previous = clip


def test_next_repeats_when_only_one_clip_exists(tmp_path):
    clips = make_clips(1, tmp_path)
    bank = FillerBank(clips, rng=random.Random(1))

    first = bank.next()
    second = bank.next()
    third = bank.next()

    assert first.slug == clips[0].slug
    assert second.slug == clips[0].slug
    assert third.slug == clips[0].slug


def test_next_only_ever_returns_clips_from_the_bank(tmp_path):
    clips = make_clips(5, tmp_path)
    bank = FillerBank(clips, rng=random.Random(7))
    pool = {c.slug for c in clips}

    for _ in range(50):
        assert bank.next().slug in pool


# ---- load_manifest -----------------------------------------------------------


def _write_manifest(tmp_path, *, schema_version=1, clips=None):
    if clips is None:
        clips = [
            {
                "slug": "one-sec",
                "text": "One sec.",
                "file": "one-sec.wav",
                "duration_ms": 812,
                "sha256": "deadbeef",
            }
        ]
    manifest = {
        "schema_version": schema_version,
        "engine": "macos-say",
        "voice": "default",
        "sample_rate": 24000,
        "channels": 1,
        "generated_at": "2026-08-13T00:00:00Z",
        "clips": clips,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_load_manifest_raises_file_not_found_when_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.json")


def test_load_manifest_raises_value_error_on_unknown_schema_version(tmp_path):
    (tmp_path / "one-sec.wav").write_bytes(b"\x00" * 16)
    path = _write_manifest(tmp_path, schema_version=2)

    with pytest.raises(ValueError):
        load_manifest(path)


def test_load_manifest_raises_value_error_when_an_audio_file_is_missing(tmp_path):
    # deliberately do NOT create one-sec.wav
    path = _write_manifest(tmp_path)

    with pytest.raises(ValueError):
        load_manifest(path)


def test_load_manifest_resolves_file_relative_to_the_manifest_directory(tmp_path):
    subdir = tmp_path / "nested"
    subdir.mkdir()
    (subdir / "one-sec.wav").write_bytes(b"\x00" * 16)
    path = _write_manifest(subdir)

    clips = load_manifest(path)

    assert len(clips) == 1
    clip = clips[0]
    assert isinstance(clip, FillerClip)
    assert clip.slug == "one-sec"
    assert clip.text == "One sec."
    assert clip.duration_ms == 812
    assert clip.path.resolve() == (subdir / "one-sec.wav").resolve()


def test_load_manifest_result_feeds_a_working_filler_bank(tmp_path):
    (tmp_path / "one-sec.wav").write_bytes(b"\x00" * 16)
    path = _write_manifest(tmp_path)

    bank = FillerBank.from_manifest(path)

    assert len(bank) == 1
    assert bank.next().slug == "one-sec"
