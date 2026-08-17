"""Deployment entrypoint: FastAPI API + built SPA, in one uvicorn process.

Run as:
    uvicorn scripts.serve_web:app --host 0.0.0.0 --port 8080

T-9 pins this composition here (not in backend/src/jarvis/server/app.py,
which is out of that ticket's scope and does not serve static files at
all). This module imports the real `jarvis.server.app:app` unmodified,
mounts the Vite-built SPA from $JARVIS_WEB_DIST, and adds SPA
history-mode fallback routing for client-side routing to work.

$JARVIS_WEB_DIST defaults to /app/web (where backend/Dockerfile copies the
built SPA in the runtime image) and can be pointed at frontend/dist for a
source-tree run. A missing or incomplete dist directory must not crash the
API: this module logs a warning and serves /healthz + /api/* alone.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from jarvis.server.app import app

logger = logging.getLogger("jarvis.serve_web")

# Compress static responses. This server sent everything uncompressed --
# every byte of JS and wasm went over the wire raw. Measured on the current
# build: the ONNX Runtime wasm binary is 13,479,978 bytes raw and 3,435,831
# gzipped, so this is roughly a 10 MB saving on the single largest asset,
# and it helps the pre-existing bundles too (the livekit chunk is 490 KB
# raw). Nothing about the app changes; the browser negotiates it via
# Accept-Encoding and falls back to identity if it cannot.
#
# minimum_size skips tiny payloads, where the compression overhead exceeds
# the saving. JSON API responses are small and mostly below it, so this is
# effectively a static-asset optimisation -- which is the intent.
app.add_middleware(GZipMiddleware, minimum_size=1024)

_DEFAULT_DIST = "/app/web"

# Path prefixes owned by the real API app. The SPA fallback route below
# must never intercept these, even though FastAPI/Starlette already
# resolves routes in registration order (the routes on the imported `app`
# were registered before this module adds the catch-all, so they win any
# match) -- this is a second, explicit guard so a future reordering fails
# loudly instead of silently serving HTML for an API path.
_RESERVED_PREFIXES = ("api/",)
_RESERVED_EXACT = ("api", "healthz")


def _resolve_dist_dir() -> Path | None:
    """Validate $JARVIS_WEB_DIST (or the default). None means "no SPA"."""
    raw = os.environ.get("JARVIS_WEB_DIST", _DEFAULT_DIST)
    path = Path(raw)

    if not path.is_dir():
        logger.warning(
            "JARVIS_WEB_DIST=%s does not exist or is not a directory; "
            "serving the API only, no SPA will be served.",
            path,
        )
        return None

    index = path / "index.html"
    if not index.is_file():
        logger.warning(
            "%s has no index.html; serving the API only, no SPA will be served.",
            path,
        )
        return None

    return path


_dist_dir = _resolve_dist_dir()

if _dist_dir is not None:
    _index_html = _dist_dir / "index.html"

    @app.get("/{full_path:path}", response_model=None)
    async def spa_fallback(full_path: str, request: Request) -> FileResponse | JSONResponse:
        """Serve a built SPA asset if it exists, else fall back to index.html.

        Registered after /healthz and /api/token, so those routes always
        match first; the guard below is defense-in-depth in case this
        module is ever reordered.
        """
        if full_path in _RESERVED_EXACT or full_path.startswith(_RESERVED_PREFIXES):
            return JSONResponse({"detail": "not found"}, status_code=404)

        if full_path:
            candidate = (_dist_dir / full_path).resolve()
            try:
                candidate.relative_to(_dist_dir.resolve())
            except ValueError:
                # Path traversal attempt (e.g. "../../etc/passwd") -- never
                # escape the dist directory. Fall through to index.html.
                candidate = None
            if candidate is not None and candidate.is_file():
                return FileResponse(candidate)

        return FileResponse(_index_html)
else:
    logger.warning("Starting with no SPA mounted; JARVIS_WEB_DIST was unusable.")
