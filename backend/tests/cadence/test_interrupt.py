"""TurnController: state machine for barge-in (pause) and stop-phrase
cancellation (jarvis.cadence.interrupt), per every rule in the T-6 contract's
"Rules that MUST hold" section.
"""

from __future__ import annotations

from jarvis.cadence.interrupt import TurnController


def test_starts_idle():
    tc = TurnController()
    assert tc.state == "idle"


def test_playback_started_from_idle_goes_to_speaking():
    tc = TurnController()
    tc.playback_started()
    assert tc.state == "speaking"


def test_playback_started_from_paused_resumes_to_speaking():
    tc = TurnController()
    tc.playback_started()
    tc.user_speech_started()
    assert tc.state == "paused"

    tc.playback_started()
    assert tc.state == "speaking"


def test_playback_finished_from_speaking_returns_to_idle():
    tc = TurnController()
    tc.playback_started()
    tc.playback_finished()
    assert tc.state == "idle"


def test_playback_finished_while_cancelled_stays_cancelled():
    tc = TurnController()
    tc.playback_started()
    tc.user_transcript("stop")
    assert tc.state == "cancelled"

    tc.playback_finished()
    assert tc.state == "cancelled"


def test_user_speech_started_while_speaking_pauses():
    tc = TurnController()
    tc.playback_started()

    action = tc.user_speech_started()

    assert action == "pause"
    assert tc.state == "paused"


def test_user_speech_started_while_idle_is_a_no_op():
    tc = TurnController()

    action = tc.user_speech_started()

    assert action == "none"
    assert tc.state == "idle"


def test_user_speech_started_while_already_paused_is_a_no_op():
    tc = TurnController()
    tc.playback_started()
    tc.user_speech_started()
    assert tc.state == "paused"

    action = tc.user_speech_started()

    assert action == "none"
    assert tc.state == "paused"


def test_stop_phrase_while_idle_is_a_no_op():
    tc = TurnController()

    action = tc.user_transcript("stop")

    assert action == "none"
    assert tc.state == "idle"


def test_stop_phrase_while_speaking_cancels():
    tc = TurnController()
    tc.playback_started()

    action = tc.user_transcript("stop")

    assert action == "cancel"
    assert tc.state == "cancelled"


def test_stop_phrase_while_paused_cancels():
    tc = TurnController()
    tc.playback_started()
    tc.user_speech_started()
    assert tc.state == "paused"

    action = tc.user_transcript("quiet jarvis")

    assert action == "cancel"
    assert tc.state == "cancelled"


def test_non_stop_speech_while_speaking_pauses():
    tc = TurnController()
    tc.playback_started()

    action = tc.user_transcript("what's the weather today")

    assert action == "pause"
    assert tc.state == "paused"


def test_non_stop_speech_while_idle_is_a_no_op():
    tc = TurnController()

    action = tc.user_transcript("what's the weather today")

    assert action == "none"
    assert tc.state == "idle"


def test_non_stop_speech_while_paused_is_a_no_op():
    tc = TurnController()
    tc.playback_started()
    tc.user_speech_started()
    assert tc.state == "paused"

    action = tc.user_transcript("what's the weather today")

    assert action == "none"
    assert tc.state == "paused"


def test_barge_in_then_stop_phrase_sequence_gives_pause_then_cancel():
    tc = TurnController()
    tc.playback_started()

    assert tc.user_speech_started() == "pause"
    assert tc.state == "paused"

    assert tc.user_transcript("stop") == "cancel"
    assert tc.state == "cancelled"


def test_cancel_is_terminal_until_reset():
    tc = TurnController()
    tc.playback_started()
    tc.user_transcript("stop")
    assert tc.state == "cancelled"

    tc.playback_started()
    assert tc.state == "cancelled"  # no-op while cancelled

    assert tc.user_speech_started() == "none"
    assert tc.state == "cancelled"  # no-op while cancelled

    tc.reset()
    assert tc.state == "idle"

    # the lock is lifted -- normal transitions work again
    tc.playback_started()
    assert tc.state == "speaking"


def test_stop_phrase_while_already_cancelled_is_still_a_cancel_non_idle_state():
    tc = TurnController()
    tc.playback_started()
    tc.user_transcript("stop")
    assert tc.state == "cancelled"

    # "cancelled" is a non-idle state, and the rule for user_transcript is
    # "stop phrase -> cancel, from ANY non-idle state" -- cancelled included.
    action = tc.user_transcript("stop")

    assert action == "cancel"
    assert tc.state == "cancelled"


def test_reset_returns_to_idle_from_any_state():
    tc = TurnController()
    tc.playback_started()
    tc.reset()
    assert tc.state == "idle"
