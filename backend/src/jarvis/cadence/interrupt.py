"""Barge-in pause + stop-phrase cancellation state machine (T-6)."""

from __future__ import annotations

from typing import Literal

from .policy import is_stop_phrase

PlaybackState = Literal["idle", "speaking", "paused", "cancelled"]
CadenceAction = Literal["none", "pause", "cancel"]


class TurnController:
    """State machine for barge-in (pause) and stop phrases (cancel)."""

    def __init__(self) -> None:
        self._state: PlaybackState = "idle"

    @property
    def state(self) -> PlaybackState:
        return self._state

    def playback_started(self) -> None:
        """-> "speaking" (also legal from "paused"/"idle"); no-op once cancelled."""
        if self._state == "cancelled":
            return
        self._state = "speaking"

    def playback_finished(self) -> None:
        """-> "idle" unless already "cancelled"."""
        if self._state == "cancelled":
            return
        self._state = "idle"

    def user_speech_started(self) -> CadenceAction:
        """Barge-in. While speaking -> pause playback, returns "pause".
        Anywhere else -> "none" (state unchanged)."""
        if self._state == "speaking":
            self._state = "paused"
            return "pause"
        return "none"

    def user_transcript(self, text: str) -> CadenceAction:
        """Stop phrase -> "cancel" and state "cancelled", from ANY non-idle
        state (including "paused" — barge-in fires first, the transcript
        arrives after). Non-stop speech while "speaking" -> "pause".
        Otherwise -> "none"."""
        if self._state != "idle" and is_stop_phrase(text):
            self._state = "cancelled"
            return "cancel"
        if self._state == "speaking":
            self._state = "paused"
            return "pause"
        return "none"

    def reset(self) -> None:
        """Back to "idle" for the next turn."""
        self._state = "idle"
