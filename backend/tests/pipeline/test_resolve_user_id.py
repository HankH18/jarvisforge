"""Coverage for jarvis.pipeline.worker._resolve_user_id: the mem0 memory
namespace key must stay STABLE across sessions for the same signed-in
user, even though jarvis.server.app now mints a UNIQUE LiveKit participant
identity per session/tab/device (P1 fix -- LiveKit's DUPLICATE_IDENTITY
eviction, see jarvis.server.app's module docstring).

Before the corresponding fix, _resolve_user_id returned
RemoteParticipant.identity RAW, with no parsing. That value feeds mem0's
namespace key directly (see entrypoint()'s `user_id_getter`/`prefs_getter`
closures), so a session-scoped identity like "user_abc::<hex>" produced
the mem0 namespace "user_abc::<hex>" -- a DIFFERENT namespace every
session. Cross-session memory silently broke for every authenticated
user, which is T-5's entire premise. The fix: _resolve_user_id recovers
the stable key via jarvis.auth.identity.stable_identity_key.

Deliberately uses jarvis.auth.identity.session_scoped_identity -- the REAL
minting function -- to produce the participant identity in every test
below, rather than a hand-written "user::hex" string: a hand-written
fixture is exactly how a format mismatch between the minting side
(jarvis.server.app) and the recovery side (this module) would hide.
"""

from __future__ import annotations

from jarvis.auth.identity import session_scoped_identity
from jarvis.pipeline.worker import _resolve_user_id


class _FakeParticipant:
    def __init__(self, identity: str) -> None:
        self.identity = identity


class _FakeRoom:
    """Stands in for livekit.rtc.Room: _resolve_user_id only ever reads
    ``remote_participants.values()``."""

    def __init__(self, *identities: str) -> None:
        self.remote_participants = {
            f"sid-{i}": _FakeParticipant(identity) for i, identity in enumerate(identities)
        }


def test_resolve_user_id_recovers_the_stable_key_from_a_real_minted_identity():
    """Drives _resolve_user_id with a room stub whose participant identity
    is a real session-scoped identity produced by the actual minting
    function (not hand-written), and asserts the result equals the bare
    user_id -- the mem0 namespace key.
    """
    user_id = "user_2abcRealClerkId"
    livekit_identity = session_scoped_identity(user_id)

    assert _resolve_user_id(_FakeRoom(livekit_identity)) == user_id


def test_two_sessions_for_the_same_user_get_different_identities_but_the_same_resolved_user_id():
    """The exact property that was broken: two DIFFERENT LiveKit participant
    identities (one per session/tab/device, from the real minting function)
    for the SAME user must resolve to the SAME _resolve_user_id result --
    otherwise cross-session memory recall breaks.
    """
    user_id = "user_same_person_two_tabs"
    identity_tab_1 = session_scoped_identity(user_id)
    identity_tab_2 = session_scoped_identity(user_id)
    assert identity_tab_1 != identity_tab_2, (
        "sanity check: session_scoped_identity must actually differ per call"
    )

    assert _resolve_user_id(_FakeRoom(identity_tab_1)) == user_id
    assert _resolve_user_id(_FakeRoom(identity_tab_2)) == user_id


def test_resolve_user_id_anonymous_mode_identity_is_unaffected():
    """Anonymous mode (Clerk not configured): the LiveKit identity IS the
    bare, client-supplied identity handed to /api/token -- it has no
    session-scoping separator at all. Must resolve unchanged, exactly as
    before the P1 fix and its follow-up.
    """
    room = _FakeRoom("plain-anonymous-client-identity")
    assert _resolve_user_id(room) == "plain-anonymous-client-identity"


def test_resolve_user_id_falls_back_to_unknown_with_no_participants():
    assert _resolve_user_id(_FakeRoom()) == "unknown"


def test_resolve_user_id_falls_back_to_unknown_for_a_bare_stub_room():
    """entrypoint() tests (test_worker_entrypoint.py) pass a bare
    ``object()`` as ctx.room for cases that never drive a turn --
    _resolve_user_id must not blow up on that, it must degrade to
    "unknown" (AttributeError swallowed)."""
    assert _resolve_user_id(object()) == "unknown"
