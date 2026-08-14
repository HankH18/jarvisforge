"""Clerk session-token verification for /api/token (T-10).

The app.py seam (see its module docstring) is a dependency that resolves
identity from a VERIFIED Clerk JWT and overrides ``req.identity`` before
the LiveKit grants are built -- this module is that dependency.

Verification itself is not reimplemented here: ``clerk_backend_api``
(already a declared dependency, backend/pyproject.toml, added at T-0 under
an explicit scope exception for this ticket) ships an official
``verify_token`` that performs real RS256 signature verification --
``jwt.decode(token, jwt_key, algorithms=["RS256"], ...)`` -- against a key
resolved from Clerk's JWKS, NOT a bare unverified decode. Two ways to
resolve that key, both going through the exact same signature-verification
code path in ``clerk_backend_api.security.verifytoken._decode_token``:

* Networked (production default): ``CLERK_SECRET_KEY`` set, ``CLERK_JWT_KEY``
  unset. The SDK fetches Clerk's JWKS from ``{api_url}/v1/jwks`` (Bearer
  ``CLERK_SECRET_KEY``) and caches the resolved PEM key in its own
  in-process, 5-minute-TTL cache (``clerk_backend_api.security.cache.Cache``)
  keyed by ``kid`` -- so this module does not need to reimplement JWKS
  fetch/cache logic, "cache the keys sensibly" comes from the SDK.
* Networkless (``CLERK_JWT_KEY`` set to Clerk's PEM public key, from the
  Clerk dashboard's "Advanced" JWT verification settings): no network call
  at all, same signature check against that fixed key. This is what
  backend/tests/multiuser uses -- unit tests must not depend on live
  network access to Clerk's API to stay deterministic -- and is itself a
  real, Clerk-documented production mode, not a test-only shortcut.

Either way, an attacker who signs their own token with their own key (no
matter how well-formed the claims) fails ``_decode_token``'s
``jwt.decode(...)`` with ``InvalidSignatureError`` and never reaches this
module's caller with an identity.

CONDITIONAL AUTH (human-authorized addition after initial T-10 landing):
T-10 is a bonus ticket; the deployed Fly app has no Clerk credentials
configured at all (T-9's base MVP demo predates Clerk and never set them),
so an unconditional auth gate would 401 every caller of the live
deployment -- taking out the base product to satisfy a bonus.
``clerk_auth_enabled()`` below is the single, server-side-only mode switch:
/api/token requires a verified Clerk token (this ticket's acceptance #1,
full force) iff ``CLERK_SECRET_KEY`` or ``CLERK_JWT_KEY`` is set in the
process environment; otherwise ``resolve_identity`` falls back to T-1's
original contract (trust ``TokenRequest.identity`` as given, see app.py).
The switch reads ONLY those two env vars -- never a request header, body
field, or query parameter -- so no client input can ever move a deployed
process between the two modes; see
backend/tests/multiuser/test_auth_jwt.py::
test_client_cannot_force_anonymous_mode_via_request.

Multi-user auth (Clerk) -- operator reference
==============================================

Two modes, chosen by which of these are set in the process environment
(check with ``fly secrets list -a <app>``; neither is set on the current
jarvisforge deployment today):

* ANONYMOUS (default, no Clerk secrets set): ``/api/token`` trusts
  ``TokenRequest.identity`` as given, no sign-in required -- this is the
  single-user MVP demo's contract (T-1/T-9), unchanged by T-10.
* AUTHENTICATED (either var set): ``/api/token`` requires a valid Clerk
  session token and 401s everyone else; the LiveKit identity always comes
  from the verified token's ``sub``, never the request body.

To turn AUTHENTICATED mode on for a Fly deployment, set ONE of:

    fly secrets set CLERK_SECRET_KEY=sk_live_...   # networked JWKS fetch (recommended)
    fly secrets set CLERK_JWT_KEY="$(cat key.pem)" # networkless, Clerk dashboard > Advanced

then redeploy (a Fly secret set triggers a release). The frontend also
needs its own key wired in so the SPA can actually present Clerk's
sign-in UI and mint tokens for it to send: set ``VITE_CLERK_PUBLISHABLE_KEY``
in ``frontend/.env`` (not committed -- gitignored, same as backend's
``.env``) before running ``npm run build``; see
``frontend/src/auth/clerk.ts``'s module docstring for exactly how that
var is consumed and how it degrades (visibly, not silently) when unset.

Every process logs which mode it started in at import time (see
``jarvis.server.app``'s startup log, ``logging.warning`` so it is visible
with zero logging configuration) -- an operator checking Fly logs after a
deploy can always see which mode is live without guessing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from clerk_backend_api.security import (
    TokenVerificationError,
    TokenVerificationErrorReason,
    VerifyTokenOptions,
    verify_token,
)
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

__all__ = [
    "ClerkIdentity",
    "TokenVerificationError",
    "clerk_auth_enabled",
    "require_clerk_identity",
    "resolve_identity",
    "verify_clerk_session_token",
]


@dataclass(frozen=True)
class ClerkIdentity:
    """The only identity ``/api/token`` trusts in authenticated mode:
    Clerk's own ``sub`` claim from a signature-verified token. Never
    constructed from anything client-supplied (request body, query string,
    headers other than the bearer token itself).
    """

    user_id: str


def clerk_auth_enabled() -> bool:
    """The single, server-side-only mode switch (see module docstring).

    True iff ``CLERK_SECRET_KEY`` or ``CLERK_JWT_KEY`` is set (non-blank)
    in the process environment right now. Deliberately re-read on every
    call, not cached at import time, for the same reason ``_default_options``
    is: env vars loaded after this module is imported (``set -a && . .env``
    right before ``uv run``, or a test's ``monkeypatch``) must still be
    picked up.
    """
    return bool(os.environ.get("CLERK_SECRET_KEY", "").strip()) or bool(
        os.environ.get("CLERK_JWT_KEY", "").strip()
    )


def _default_options() -> VerifyTokenOptions:
    """Built fresh on every call (not cached at import time) so a
    ``CLERK_SECRET_KEY``/``CLERK_JWT_KEY`` loaded into the environment after
    this module is imported -- e.g. ``set -a && . ../.env`` run right before
    ``uv run`` -- is still picked up, and so tests can monkeypatch
    ``os.environ`` per-test without import-order games.
    """
    return VerifyTokenOptions(
        secret_key=os.environ.get("CLERK_SECRET_KEY"),
        jwt_key=os.environ.get("CLERK_JWT_KEY"),
    )


def verify_clerk_session_token(
    token: str, *, options: VerifyTokenOptions | None = None
) -> ClerkIdentity:
    """Verify ``token``'s signature and return the identity it carries.

    Raises ``clerk_backend_api.security.TokenVerificationError`` -- never
    returns a ``ClerkIdentity`` for a token whose signature does not verify,
    that has expired, or that carries no ``sub`` claim.
    """
    payload = verify_token(token, options or _default_options())
    sub = payload.get("sub")
    if not sub:
        # A token that verifies but carries no subject is not a usable
        # identity -- reuse TOKEN_INVALID rather than inventing a new
        # failure mode the caller has to special-case.
        raise TokenVerificationError(TokenVerificationErrorReason.TOKEN_INVALID)
    return ClerkIdentity(user_id=sub)


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authorization header must be 'Bearer <token>'")
    return token


def require_clerk_identity(request: Request) -> ClerkIdentity:
    """FastAPI dependency: UNCONDITIONALLY 401s on a missing, malformed,
    forged, or expired token. On success, returns the identity derived
    ONLY from the verified token -- never from anything else on the
    request. Used directly by ``resolve_identity`` below when Clerk is
    configured; also usable standalone (e.g. for a future endpoint that
    should always require auth regardless of deployment mode).
    """
    token = _bearer_token(request.headers.get("authorization"))
    try:
        return verify_clerk_session_token(token)
    except TokenVerificationError as exc:
        raise HTTPException(status_code=401, detail=exc.reason.value[0]) from exc


def resolve_identity(request: Request) -> ClerkIdentity | None:
    """The dependency /api/token actually uses.

    Mode is chosen ONLY by ``clerk_auth_enabled()`` (server config) --
    nothing about ``request`` is consulted to pick the mode, only to
    extract the bearer token once already in authenticated mode:

    * Clerk configured (``clerk_auth_enabled()`` True): behaves exactly
      like ``require_clerk_identity`` -- 401s on anything but a verified
      token, returns the verified ``ClerkIdentity`` on success. Full
      T-10 acceptance #1 force.
    * Clerk NOT configured: returns ``None``, unconditionally, without
      even looking at the Authorization header. The caller (app.py) then
      falls back to ``TokenRequest.identity`` -- T-1's original,
      pre-T-10 contract -- so the base MVP demo (T-9, deployed with no
      Clerk credentials set) keeps working unauthenticated.
    """
    if not clerk_auth_enabled():
        return None
    return require_clerk_identity(request)
