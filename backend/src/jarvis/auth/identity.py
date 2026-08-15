"""Session-scoped LiveKit participant identity format (T-10 P1 fix).

Two DIFFERENT processes need this format:

* ``jarvis.server.app`` (the FastAPI token endpoint) MINTS it --
  ``session_scoped_identity`` appends a unique per-session suffix to the
  stable Clerk ``user_id`` so a second tab/device gets its own LiveKit
  participant identity instead of reusing the first one and triggering
  LiveKit's ``DUPLICATE_IDENTITY`` eviction (confirmed in the installed
  SDK source -- see ``app.py``'s module docstring for the full citation).
* ``jarvis.pipeline.worker`` (the LiveKit Agents worker process) RECOVERS
  it -- ``stable_identity_key`` strips the per-session suffix back off so
  the mem0 memory namespace stays keyed on the stable ``user_id``, not a
  different string every session.

This module is deliberately **stdlib-only** (no FastAPI, no pydantic, no
livekit imports) so it can be imported from the worker process without
dragging the web framework into it. ``app.py`` imports FastAPI and
pydantic at module level, so importing the identity format FROM ``app.py``
would pull the whole web framework into the worker's process and its
deployment image -- a real coupling defect. Both processes instead import
this module, which owns the format (the separator, mint, and recover) in
exactly one place, so the two sides cannot drift out of sync on how they
encode/decode it.
"""

from __future__ import annotations

import uuid

IDENTITY_SEPARATOR = "::"
"""Separator between the stable Clerk ``user_id`` and the per-session
suffix in a LiveKit participant identity minted by
``session_scoped_identity``. Clerk user ids (``user_<random>``, see
``jarvis.auth.clerk.ClerkIdentity``) never contain ``:``, so splitting on
the FIRST occurrence unambiguously recovers the stable id -- see
``stable_identity_key`` below.
"""


def session_scoped_identity(user_id: str) -> str:
    """Mint a LiveKit participant identity that is UNIQUE PER TOKEN (i.e.
    per session/tab/device), not the bare stable ``user_id`` alone.

    See this module's docstring and ``jarvis.server.app``'s
    ``_room_for_authenticated_identity`` docstring for why: reusing the
    same identity for every session of the same user means a second
    tab/device silently evicts the first (LiveKit's ``DUPLICATE_IDENTITY``
    behaviour). The stable id remains recoverable from the result via
    ``stable_identity_key``.
    """
    return f"{user_id}{IDENTITY_SEPARATOR}{uuid.uuid4().hex}"


def stable_identity_key(livekit_identity: str) -> str:
    """Recover the stable ``user_id`` from a LiveKit participant identity.

    Total and defensive by construction -- every input, however malformed,
    produces a value with no exception:

    * **Well-formed** (``"<user_id>::<suffix>"``, non-empty ``user_id``):
      returns ``user_id``, the part before the FIRST separator. A suffix
      that itself contains ``"::"`` (multiple separators) does not change
      this -- only the first occurrence is significant, so
      ``"user::a::b"`` still recovers ``"user"``.
    * **No separator at all** (anonymous mode: the client-supplied LiveKit
      identity, which has no session-scoping applied to it): returned
      UNCHANGED. This is the anonymous-mode contract -- it must keep
      working exactly as before this function existed, not crash and not
      collapse to ``""``.
    * **Leading separator** (``"::suffix"``, i.e. an EMPTY component
      before the first ``"::"``) or an **empty string**: also returned
      UNCHANGED rather than truncated to ``""``. A real
      ``session_scoped_identity`` output never has an empty ``user_id``
      component (Clerk ids are always non-empty), so this shape is never
      produced by this module's own minting side -- it can only arise
      from a malformed or adversarial identity. Returning a fabricated
      ``""`` key here would be actively dangerous: mem0 would namespace
      EVERY such caller under the same empty-string key, pooling
      otherwise-unrelated callers into one shared memory namespace. Since
      there is no trustworthy stable id to recover, the safe choice is to
      leave the identity exactly as given (still usable as an -- odd but
      unique-per-caller -- opaque key) rather than manufacture a shared one.
    * **Trailing separator** (``"user_id::"``, empty suffix): the
      ``user_id`` component is still non-empty, so this is treated as
      well-formed and returns ``user_id`` -- the (empty) suffix is never
      inspected.

    In short: the first ``"::"``-delimited component is returned only when
    it is non-empty; otherwise the input passes through unchanged.
    """
    key, separator, _suffix = livekit_identity.partition(IDENTITY_SEPARATOR)
    return key if separator and key else livekit_identity
