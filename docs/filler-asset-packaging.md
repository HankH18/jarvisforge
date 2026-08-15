# Filler assets: packaging (T-26) and one source of truth (T-30)

## What was actually broken (T-26)

`jarvis.cadence.fillers` resolved its assets directory as
`Path(__file__).resolve().parents[3] / "assets" / "fillers"` -- correct only
for the exact source-tree depth `backend/src/jarvis/cadence/fillers.py`.
Two separate problems, initially conflated as one:

1. **Resolution logic**: hardcoding a specific `parents[N]` index breaks the
   moment the module lives at a different depth (e.g. inside
   `site-packages/`). Fixed in `fillers.py` with `_resolve_assets_dir()`:
   an `importlib.resources`-anchored primary strategy plus a bounded
   upward-search fallback, neither of which assumes a fixed depth.
2. **Packaging**: the original `backend/assets/fillers/` was a *sibling* of
   `backend/src`, not inside the `jarvis` package tree that
   `backend/pyproject.toml`'s `packages = ["src/jarvis"]` ships. No file at
   that path was ever going to appear in a wheel or in the Docker image's
   `COPY backend/src ./src` step, no matter how resolution logic in
   `fillers.py` was written.

The fix for (2) was to copy the assets into a location already inside the
scope that *is* packaged: `backend/src/jarvis/cadence/**`. This works with
**zero changes** to `backend/pyproject.toml` or `backend/Dockerfile`:

- Hatchling's default wheel build includes every file under a declared
  `packages` directory, not just `.py` files -- confirmed independently by
  `jarvis/capabilities.yaml` (a non-`.py` file directly under `src/jarvis/`)
  already shipping in the built wheel with no explicit package-data
  declaration. The relocated `assets/fillers/` directory rides along the
  same way.
- `backend/Dockerfile`'s `backend-build` stage already does
  `COPY backend/src ./src` followed by `uv pip install --no-cache .` with no
  filtering, so the packaged assets carry straight into `/opt/venv` in the
  final image with no separate `COPY` line needed.
- `_resolve_assets_dir()`'s primary strategy,
  `importlib.resources.files("jarvis.cadence")`, finds the packaged assets
  immediately -- confirmed both in a normal source checkout (see
  `test_production_default_resolves_to_the_packaged_copy_and_loads` in
  `backend/tests/cadence/test_asset_resolution.py`) and inside a real built
  image (see "Container evidence" below).

### Container evidence (built from the T-26 tree)

```
$ docker build -f backend/Dockerfile -t jarvisforge:t26probe2 .
... (builds cleanly, no Dockerfile/pyproject.toml changes)

$ docker run --rm --entrypoint sh jarvisforge:t26probe2 -c \
    "find / -path '*jarvis/cadence/assets*' 2>/dev/null"
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/give-me-a-mo.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/one-sec.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/let-me-check.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/on-it.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/checking-now.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/looking-it-up.wav
/opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers/manifest.json

$ docker run --rm --entrypoint python jarvisforge:t26probe2 -c "
from jarvis.cadence.fillers import ASSETS_DIR, MANIFEST_PATH, FillerBank
print('ASSETS_DIR =', ASSETS_DIR)
print('ASSETS_DIR.exists() =', ASSETS_DIR.exists())
bank = FillerBank.from_manifest()
print('LOADED OK: FillerBank has', len(bank), 'clips')
clip = bank.next()
print('sample clip slug =', clip.slug)
print('sample clip bytes read =', len(clip.path.read_bytes()))
"
ASSETS_DIR = /opt/venv/lib/python3.12/site-packages/jarvis/cadence/assets/fillers
ASSETS_DIR.exists() = True
LOADED OK: FillerBank has 6 clips
sample clip slug = give-me-a-mo
sample clip bytes read = 58482
```

Acceptance 2 is satisfied: the deployed image (built exactly as `fly.toml`
prescribes, with `backend/pyproject.toml` and `backend/Dockerfile`
untouched) loads all 6 filler clips.

## The duplication T-26 left behind, and why (historical)

T-26 fixed packaging by *copying* the six clips and `manifest.json`
byte-for-byte into the packaged location, deliberately leaving the original
`backend/assets/fillers/` untouched as the thing `scripts/build_fillers.py`
still regenerated. That was flagged at the time as a real, if contained,
problem: two committed copies of the same files, kept in sync only by a
divergence-guard test (`test_packaged_assets_match_the_committed_source_of_truth`)
that compared them byte-for-byte and failed loudly on drift. It could not
*repair* drift -- only catch it after a rebuild had already stranded the
packaged copy stale. Collapsing the duplication was explicitly deferred to
a future ticket.

## One source of truth (T-30)

This ticket is that future ticket. The fix:

- `scripts/build_fillers.py`'s default output directory (`--out`, absent an
  override) is now `jarvis.cadence.fillers._resolve_assets_dir()` itself --
  the same resolver production imports at module load to compute
  `ASSETS_DIR` -- rather than an independently-hardcoded guess at where that
  is. In this checkout that resolves to
  `backend/src/jarvis/cadence/assets/fillers/`. A rebuild now writes
  straight into the location production loads; there is no second copy left
  to go stale.
- `backend/assets/fillers/` (the original, pre-packaging location) is
  **deleted** from the repo (`git rm`), not merely gitignored. Nothing in
  `src/` or `tests/` needs it as a scratch target: the only writer was
  `scripts/build_fillers.py`, and its default output now points at the
  packaged location directly. A `--out` override still lets you write
  anywhere (e.g. a scratch directory of your own choosing) for a one-off
  experiment, but nothing in the tree depends on `backend/assets/` existing.
- The T-26 divergence-guard test
  (`test_packaged_assets_match_the_committed_source_of_truth`) is retired --
  comparing two copies for byte-equality is meaningless once there is only
  one copy. It is replaced in
  `backend/tests/cadence/test_asset_resolution.py` by two tests that encode
  "one canonical copy" directly:
  - `test_no_second_copy_of_filler_assets_exists` -- fails if
    `backend/assets/fillers/` ever reappears on disk.
  - `test_build_fillers_default_output_is_the_packaged_location` -- imports
    `scripts/build_fillers.py` directly and fails if its `_DEFAULT_OUT_DIR`
    ever stops equaling `jarvis.cadence.fillers.ASSETS_DIR`, so a regression
    in the *code* (not just a stray file on disk) is also caught.
- The packaged copy's own integrity checks are unchanged and unweakened:
  `backend/tests/cadence/test_assets.py` still verifies, against the real
  committed manifest and wav files, that every listed clip exists, is valid
  PCM16 at the manifest's sample rate/channels, and hashes to the sha256 the
  manifest records; `test_asset_resolution.py`'s
  `test_production_default_resolves_to_the_packaged_copy_and_loads` still
  proves a real source checkout's `ASSETS_DIR` resolves to the packaged
  copy and that `FillerBank.from_manifest()` loads all 6 clips end-to-end.

### No audio bytes changed

T-30 is a packaging/plumbing change only -- no clip regeneration (Cartesia
is returning HTTP 402 billing errors; that is out of scope here regardless).
The six clips and `manifest.json` under
`backend/src/jarvis/cadence/assets/fillers/` are byte-identical before and
after this ticket; see the T-30 ticket close-out for the recorded sha256
list proving it.

## Manifest schema

`manifest.json` (written by `scripts/build_fillers.py`'s `_write_manifest`,
read by `jarvis.cadence.fillers.load_manifest`) is a single JSON object:

```json
{
  "schema_version": 1,
  "engine": "cartesia",
  "voice": "<engine-specific voice id/name>",
  "sample_rate": 24000,
  "channels": 1,
  "generated_at": "2026-08-13T00:00:00Z",
  "clips": [
    {
      "slug": "one-sec",
      "text": "One sec.",
      "file": "one-sec.wav",
      "duration_ms": 812,
      "sha256": "<hex digest of the wav file's bytes>"
    }
  ]
}
```

- `schema_version` -- must equal `1`; `load_manifest` raises `ValueError` on
  any other value.
- `engine` -- `"cartesia"` or `"macos-say"`, whichever engine produced the
  clips.
- `sample_rate` / `channels` -- shared across every clip (16-bit PCM WAV;
  `_write_manifest` raises if clips disagree).
- `clips[].file` -- resolved relative to the directory `manifest.json`
  itself lives in, never as an absolute or repo-relative path -- this is
  what makes the manifest+clips portable to wherever `_resolve_assets_dir()`
  finds them (source checkout, wheel, container).
- `clips[].slug` -- the six pinned values also enumerated in
  `jarvis.cadence.fillers.FILLER_PHRASES` (by text) and pinned by slug in
  `backend/tests/cadence/test_assets.py`'s `_PINNED_SLUGS`: `one-sec`,
  `let-me-check`, `give-me-a-mo`, `looking-it-up`, `on-it`, `checking-now`.
- `clips[].sha256` -- verified against the actual wav bytes on load by
  `test_wav_sha256_matches_the_manifest`; a mismatch is a corrupt/edited
  clip, not a schema question, and is never silently tolerated.
