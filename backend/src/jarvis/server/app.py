"""FastAPI token endpoint (docs/DESIGN.md §Interfaces, pinned exactly):

    POST /api/token  {"identity": str, "room": str|null} ->
                      {"token": str, "url": str}

T-10 hardens this with Clerk JWT auth and server-side identity derivation,
but CONDITIONALLY on server configuration (human-authorized decision, see
jarvis.auth.clerk's module docstring for the full reasoning): T-10 is a
bonus ticket, and the deployed Fly app (T-9's base MVP demo) has no Clerk
credentials set at all, so making the gate unconditional would 401 every
caller of the live deployment -- taking out the base product to satisfy a
bonus.

``jarvis.auth.clerk.resolve_identity`` is the seam:

* Clerk configured (``CLERK_SECRET_KEY`` or ``CLERK_JWT_KEY`` set) --
  "authenticated mode": resolves and verifies the caller's Clerk session
  token and hands back a ``ClerkIdentity``; the handler below uses ITS
  ``user_id``, never ``req.identity``, to build the LiveKit grant. A
  missing/forged/tampered/expired token 401s before any grant is built --
  T-10 acceptance #1, full force.
* Clerk NOT configured -- "anonymous mode" (the current Fly deployment):
  resolves to ``None`` and the handler falls back to ``req.identity`` --
  T-1's original, pre-T-10 contract, unchanged.

Which mode a process is in is decided ONLY by those two env vars, checked
once per request server-side -- never by anything in the request itself
(see resolve_identity's docstring and
backend/tests/multiuser/test_auth_jwt.py::
test_client_cannot_force_anonymous_mode_via_request). The wire shape
(request/response JSON) is unchanged in both modes -- pinned -- only the
Authorization header is new, and it only matters in authenticated mode.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import Depends, FastAPI
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel

from jarvis.auth.clerk import ClerkIdentity, clerk_auth_enabled, resolve_identity

logger = logging.getLogger(__name__)

app = FastAPI(title="jarvis")

# Loud, unmissable startup log of which mode this process is in -- an
# operator must never be left guessing whether a deployment is enforcing
# auth. Uses the plain `logging.warning` module function (not a handler
# this module configures) so it prints regardless of whatever logging
# config uvicorn/the deployment applies -- WARNING is Python logging's
# default-visible threshold with zero configuration.
if clerk_auth_enabled():
    logging.warning(
        "jarvis.server.app: Clerk credentials ARE configured -- /api/token is in "
        "AUTHENTICATED mode: every request requires a verified Clerk session token "
        "(client-supplied identity is ignored)."
    )
else:
    logging.warning(
        "jarvis.server.app: no Clerk credentials configured (CLERK_SECRET_KEY / "
        "CLERK_JWT_KEY both unset) -- /api/token is in ANONYMOUS mode: it trusts "
        "the client-supplied identity, exactly as before T-10. To require sign-in "
        "(multi-user), set one of those two env vars -- e.g. on Fly: "
        "`fly secrets set CLERK_SECRET_KEY=sk_live_...` -- and redeploy; see the "
        "'Multi-user auth (Clerk)' section in jarvis.auth.clerk's module docstring "
        "for both modes and exactly which var(s) to set."
    )


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
def create_token(
    req: TokenRequest,
    identity: ClerkIdentity | None = Depends(resolve_identity),  # noqa: B008 - FastAPI's own idiom
) -> TokenResponse:
    # Authenticated mode (Clerk configured): `identity` is the verified
    # Clerk `sub` -- req.identity is deliberately never read, so a client
    # cannot impersonate another user's mem0 namespace via the request
    # body no matter what it puts in "identity". resolve_identity already
    # 401'd before this line for anything but a verified token.
    #
    # Anonymous mode (Clerk not configured, e.g. the current Fly
    # deployment): `identity` is None and this falls back to req.identity
    # -- T-1's original, pre-T-10 contract, unchanged.
    user_identity = identity.user_id if identity is not None else req.identity
    room = req.room or f"jarvis-{uuid.uuid4().hex[:8]}"

    grants = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    token = (
        AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(user_identity)
        .with_grants(grants)
        .to_jwt()
    )
    return TokenResponse(token=token, url=os.environ["LIVEKIT_URL"])
