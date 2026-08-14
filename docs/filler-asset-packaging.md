# Filler assets: packaging fix (T-26)

## What was actually broken

`jarvis.cadence.fillers` resolved its assets directory as
`Path(__file__).resolve().parents[3] / "assets" / "fillers"` -- correct only
for the exact source-tree depth `backend/src/jarvis/cadence/fillers.py`.
Two separate problems, initially conflated as one:

1. **Resolution logic**: hardcoding a specific `parents[N]` index breaks the
   moment the module lives at a different depth (e.g. inside
   `site-packages/`). Fixed in `fillers.py` with `_resolve_assets_dir()`:
   an `importlib.resources`-anchored primary strategy plus a bounded
   upward-search fallback, neither of which assumes a fixed depth.
2. **Packaging**: `backend/assets/fillers/` (the original, human-curated
   source of truth, built by `scripts/build_fillers.py`) is a *sibling* of
   `backend/src`, not inside the `jarvis` package tree that
   `backend/pyproject.toml`'s `packages = ["src/jarvis"]` ships. No file at
   that path was ever going to appear in a wheel or in the Docker image's
   `COPY backend/src ./src` step, no matter how resolution logic in
   `fillers.py` was written.

An initial pass at this ticket treated (2) as unfixable within scope
(`backend/pyproject.toml` and `backend/Dockerfile` are out of scope) and
reported it as a plan defect. **That was wrong.** The fix for (2) never
required editing either of those files -- it only required putting a copy of
the assets somewhere *already* inside the scope that *is* packaged:
`backend/src/jarvis/cadence/**`.

## The actual fix

Copied the six `.wav` clips and `manifest.json` byte-for-byte from
`backend/assets/fillers/` into
`backend/src/jarvis/cadence/assets/fillers/` (294,389 bytes total -- see
below). `backend/assets/` was not modified, moved, or deleted; the original
files there are untouched and remain the source of truth
`scripts/build_fillers.py` regenerates.

This works with **zero changes** to `backend/pyproject.toml` or
`backend/Dockerfile`:

- Hatchling's default wheel build includes every file under a declared
  `packages` directory, not just `.py` files -- confirmed independently
  before this fix by `jarvis/capabilities.yaml` (a non-`.py` file directly
  under `src/jarvis/`) already shipping in the built wheel with no explicit
  package-data declaration. The relocated `assets/fillers/` directory rides
  along the same way.
- `backend/Dockerfile`'s `backend-build` stage already does
  `COPY backend/src ./src` followed by `uv pip install --no-cache .` with no
  filtering, so the packaged assets carry straight into `/opt/venv` in the
  final image with no separate `COPY` line needed.
- `_resolve_assets_dir()`'s primary strategy,
  `importlib.resources.files("jarvis.cadence")`, was already implemented by
  this ticket and finds the relocated assets immediately -- confirmed both
  in a normal source checkout (see the `test_production_default_...` test)
  and inside a real built image (see "Container evidence" below).

### Size

```
$ du -c backend/src/jarvis/cadence/assets/fillers/*
 56  checking-now.wav
 60  give-me-a-mo.wav
 52  let-me-check.wav
 52  looking-it-up.wav
  4  manifest.json
 32  on-it.wav
 48  one-sec.wav
```

Total: **294,389 bytes (~287.5 KiB / ~0.29 MB)** added to the repo -- a
second, byte-identical copy of assets that already existed under
`backend/assets/fillers/`. This is small enough to be a non-issue for a git
repo or a submission zip (well under a megabyte); it is not flagged as a
concern.

### Guarding against the two copies drifting apart

Because the fix is a *duplicate* copy rather than a single canonical
location, `backend/tests/cadence/test_asset_resolution.py` adds
`test_packaged_assets_match_the_committed_source_of_truth`, which compares
every file under the packaged copy against `backend/assets/fillers/`
byte-for-byte and fails loudly if either location is edited without the
other. `test_production_default_resolves_to_the_packaged_copy_and_loads`
separately proves, with no mocking, that a real source checkout's
`ASSETS_DIR`/`MANIFEST_PATH` already resolve to the packaged copy (i.e. the
`importlib.resources` strategy is genuinely primary, not merely
implemented) and that `FillerBank.from_manifest()` loads all 6 clips.

## Container evidence (built from the current tree)

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
prescribes, from the current tree, with `backend/pyproject.toml` and
`backend/Dockerfile` untouched) loads all 6 filler clips.

## What remains genuinely out of scope

Nothing blocks acceptance 2 anymore. One follow-up is worth flagging for a
future ticket, not this one: `backend/assets/fillers/` and
`backend/src/jarvis/cadence/assets/fillers/` are now two committed copies of
the same six files. Collapsing that duplication (e.g. making
`scripts/build_fillers.py` write directly into the packaged location and
retiring `backend/assets/` as a separate directory, or the reverse) touches
`scripts/build_fillers.py`'s output path and how other tooling/docs
reference `backend/assets/fillers/` -- a design decision, not a T-26 bug fix,
and left to a future ticket. The divergence-guard test above ensures the
duplication stays safe (loudly caught, not silently stale) in the meantime.
