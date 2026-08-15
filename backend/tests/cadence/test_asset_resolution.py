"""T-26: prove filler-asset resolution does not depend on the depth of
``__file__`` -- neither the historical bug (a hardcoded
``parents[3]``) nor a legitimate fix should care how many directories
separate ``jarvis/cadence/fillers.py`` from its assets.

Both tests below are deliberately built so the OLD implementation
(``Path(__file__).resolve().parents[3] / "assets" / "fillers"``) fails in the
exact arrangement where the NEW implementation
(``jarvis.cadence.fillers._resolve_assets_dir``) succeeds -- proving the new
test is not vacuously passing by accidentally finding the real, committed
packaged copy via the source tree. Neither test touches the real packaged
assets directory or the real committed manifest; both build throwaway
fixtures under ``tmp_path``.
"""

from __future__ import annotations

import importlib
import json
import sys
import uuid
from pathlib import Path

from jarvis.cadence import fillers
from jarvis.cadence.fillers import FillerBank, _resolve_assets_dir, load_manifest

_OLD_PARENTS_DEPTH = 3  # the historical, buggy hardcoded index


def _old_implementation(fake_file: Path) -> Path:
    """Reproduces the exact expression fillers.py used before T-26, so tests
    can show it breaks in arrangements the new implementation handles."""
    return fake_file.resolve().parents[_OLD_PARENTS_DEPTH] / "assets" / "fillers"


def _write_manifest(fillers_dir: Path, *, slug: str = "one-sec") -> None:
    fillers_dir.mkdir(parents=True, exist_ok=True)
    wav_name = f"{slug}.wav"
    (fillers_dir / wav_name).write_bytes(b"\x00" * 16)
    manifest = {
        "schema_version": 1,
        "engine": "macos-say",
        "voice": "default",
        "sample_rate": 24000,
        "channels": 1,
        "generated_at": "2026-08-13T00:00:00Z",
        "clips": [
            {
                "slug": slug,
                "text": "One sec.",
                "file": wav_name,
                "duration_ms": 900,
                "sha256": "deadbeef",
            }
        ],
    }
    (fillers_dir / "manifest.json").write_text(json.dumps(manifest))


# ---- strategy 2: bounded upward search, at a depth the old code guesses wrong ----


def test_upward_search_finds_assets_at_a_depth_the_old_parents3_guess_misses(tmp_path):
    """Simulates an "installed" layout deeper than the source tree, where the
    assets sit as loose files one level above ``cadence/`` -- NOT three
    parents up from the module file, as the old hardcoded index assumed.

    Uses a ``resource_anchor`` that cannot resolve (a package name that does
    not exist), so strategy 1 (importlib.resources) is forced to fail and
    fall through to strategy 2 (upward search) -- this test is specifically
    about strategy 2, and must not accidentally pass because the *real*
    ``jarvis.cadence`` package now ships its own packaged copy (T-26 asset
    relocation) and strategy 1 would find that regardless of ``start_file``.
    """
    fake_file = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "pkg"
        / "jarvis_like"
        / "cadence"
        / "fillers.py"
    )
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("# stand-in module file, never imported\n")

    # Assets live next to "cadence/", i.e. one parent up from the module --
    # not three, where the old implementation would look.
    real_assets_dir = fake_file.parent.parent / "assets" / "fillers"
    _write_manifest(real_assets_dir)

    # Old implementation: looks 3 parents up (site-packages/) -- wrong.
    old_guess = _old_implementation(fake_file)
    assert not (old_guess / "manifest.json").is_file(), (
        "old parents[3] implementation should NOT find the manifest at this "
        "depth -- if it does, this test setup no longer proves anything"
    )

    # New implementation: bounded upward search finds it regardless of the
    # specific depth (resource_anchor deliberately unresolvable -- see
    # docstring -- so this exercises strategy 2, not strategy 1).
    resolved = _resolve_assets_dir(
        start_file=fake_file, resource_anchor="__t26_no_such_package__"
    )
    assert resolved == real_assets_dir
    assert (resolved / "manifest.json").is_file()

    # And the resolved directory actually feeds a working bank.
    bank = FillerBank.from_manifest(resolved / "manifest.json")
    assert len(bank) == 1
    assert bank.next().slug == "one-sec"


# ---- strategy 1: importlib.resources, decoupled from __file__ depth entirely ----


def test_importlib_resources_finds_assets_installed_distribution_style(tmp_path, monkeypatch):
    """Simulates a genuinely packaged/installed distribution: the assets are
    NOT reachable from the module file by walking up any number of parent
    directories (they live under a completely separate ``sys.path`` entry,
    the way a wheel's package data would after ``pip install``) -- they are
    only reachable via ``importlib.resources`` anchored on the installed
    package. This is the arrangement a real ``site-packages`` install
    produces and the old ``parents[N]`` arithmetic can never handle, no
    matter what N is chosen.
    """
    pkg_name = f"jarvis_t26_sim_{uuid.uuid4().hex}"

    site_dir = tmp_path / "site"
    pkg_dir = site_dir / pkg_name
    cadence_dir = pkg_dir / "cadence"
    (pkg_dir).mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")
    cadence_dir.mkdir()
    (cadence_dir / "__init__.py").write_text("")

    assets_dir = cadence_dir / "assets" / "fillers"
    _write_manifest(assets_dir, slug="installed-clip")

    monkeypatch.syspath_prepend(str(site_dir))

    # The module file lives in a totally unrelated, empty subtree: no
    # assets/fillers directory exists anywhere in its ancestry, so the
    # upward-search fallback (and any choice of old parents[N]) is
    # guaranteed to fail here -- only the importlib.resources anchor can
    # succeed, proving that strategy is what's doing the work.
    unrelated_file = tmp_path.joinpath(
        "unrelated", *[f"level{i}" for i in range(9)], "fillers.py"
    )
    unrelated_file.parent.mkdir(parents=True)
    unrelated_file.write_text("# stand-in, never imported\n")

    old_guess = _old_implementation(unrelated_file)
    assert not (old_guess / "manifest.json").is_file(), (
        "old parents[3] implementation should NOT find the manifest in an "
        "unrelated subtree -- if it does, this test setup is broken"
    )

    resolved = _resolve_assets_dir(
        start_file=unrelated_file, resource_anchor=f"{pkg_name}.cadence"
    )
    assert resolved.resolve() == assets_dir.resolve()
    assert (resolved / "manifest.json").is_file()

    clips = load_manifest(resolved / "manifest.json")
    assert len(clips) == 1
    assert clips[0].slug == "installed-clip"

    bank = FillerBank.from_manifest(resolved / "manifest.json")
    assert bank.next().slug == "installed-clip"


# ---- packaged copy wins in THIS real checkout, and stays honest ------------


def test_production_default_resolves_to_the_packaged_copy_and_loads():
    """No mocking, no simulated arrangement: the module-level ``ASSETS_DIR``
    computed by the *real* import of ``jarvis.cadence.fillers``, in this
    exact checkout, must already resolve -- via the importlib.resources
    strategy -- to the packaged copy under
    ``src/jarvis/cadence/assets/fillers/`` (T-26 asset relocation, and since
    T-30 the *only* committed copy). This proves strategy 1 is genuinely
    primary (it wins even though strategy 2 would also succeed here), and
    that a real source checkout loads the bank end-to-end.
    """
    packaged_dir = Path(fillers.__file__).resolve().parent / "assets" / "fillers"
    assert packaged_dir.is_dir(), f"expected packaged assets at {packaged_dir}"

    assert packaged_dir == fillers.ASSETS_DIR
    assert packaged_dir / "manifest.json" == fillers.MANIFEST_PATH
    assert fillers.MANIFEST_PATH.is_file()

    bank = FillerBank.from_manifest()
    assert len(bank) == 6
    clip = bank.next()
    assert clip.path.is_file()


def test_no_second_copy_of_filler_assets_exists():
    """T-30: collapses the T-26 divergence-guard test's "two copies must
    agree" invariant into the stronger "there is only one copy" invariant --
    ``backend/assets/fillers/`` (the pre-T-30 original location
    ``scripts/build_fillers.py`` used to write, and the packaged copy under
    ``backend/src/jarvis/cadence/assets/fillers/`` had to be kept in sync
    with byte-for-byte) must not exist. A second copy reappearing is exactly
    the drift the retired test was built to catch; encoding "there is
    nothing to drift from" is a stronger guarantee than "the two things that
    can drift currently agree".
    """
    # backend/tests/cadence/test_asset_resolution.py -> parents[2] == backend
    # (a fixed reference to this repo's known layout for the test's own
    # sake -- NOT the production resolution logic under test above, which
    # must not hardcode this kind of depth).
    backend_dir = Path(__file__).resolve().parents[2]
    stale_location = backend_dir / "assets" / "fillers"

    assert not stale_location.exists(), (
        f"a second copy of the filler assets reappeared at {stale_location} -- "
        "T-30 collapsed the two committed copies into the single packaged "
        "location jarvis.cadence.fillers.ASSETS_DIR resolves to; do not "
        "resurrect the old backend/assets/fillers/ directory (see "
        "docs/filler-asset-packaging.md)"
    )


def test_build_fillers_default_output_is_the_packaged_location():
    """Binds ``scripts/build_fillers.py`` itself, not just the assets on
    disk: even before anyone runs a rebuild, the script's default ``--out``
    must already point at the same packaged directory production loads
    (``jarvis.cadence.fillers.ASSETS_DIR``). If someone points the default
    back at the old ``backend/assets/fillers/`` location, this fails loudly
    here instead of silently stranding the packaged copy stale on the next
    voice rebuild.
    """
    repo_root = Path(__file__).resolve().parents[3]
    scripts_dir = repo_root / "scripts"
    assert scripts_dir.is_dir(), f"expected {scripts_dir} to exist"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    build_fillers = importlib.import_module("build_fillers")

    assert build_fillers._DEFAULT_OUT_DIR == fillers.ASSETS_DIR, (
        "scripts/build_fillers.py's default output directory "
        f"({build_fillers._DEFAULT_OUT_DIR}) must equal the packaged location "
        f"production actually loads (jarvis.cadence.fillers.ASSETS_DIR == "
        f"{fillers.ASSETS_DIR})"
    )
