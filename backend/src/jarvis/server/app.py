"""FastAPI token endpoint (docs/DESIGN.md §Interfaces, pinned exactly):

    POST /api/token  {"identity": str, "room": str|null} ->
                      {"token": str, "url": str}

T-10 hardens this with required Clerk JWT auth and derives identity
server-side from the verified token, ignoring the client-supplied identity.
That is a pure addition at the request-handling boundary (a dependency that
overrides `req.identity` before the grants are built) -- nothing here needs
to change shape for it, so no auth is built now.
"""

from __future__ import annotations

import os
import uuid

from fastapi import FastAPI
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel

app = FastAPI(title="jarvis")


@app.get("/healthz")
def healthz() -> dict:
    """Trivial liveness probe for scripts/smoke_deploy.sh (T-9).

    Deliberately dumb: no dependency checks, no DB ping -- just proof the
    process is up and serving.
    """
    return {"status": "ok"}


class TokenRequest(BaseModel):
    identity: str
    room: str | None = None


class TokenResponse(BaseModel):
    token: str
    url: str


@app.post("/api/token", response_model=TokenResponse)
def create_token(req: TokenRequest) -> TokenResponse:
    # T-10 seam: an auth dependency would resolve `identity` from a verified
    # Clerk JWT here and ignore req.identity; unauthenticated for now (T-1).
    room = req.room or f"jarvis-{uuid.uuid4().hex[:8]}"

    grants = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    token = (
        AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(req.identity)
        .with_grants(grants)
        .to_jwt()
    )
    return TokenResponse(token=token, url=os.environ["LIVEKIT_URL"])
