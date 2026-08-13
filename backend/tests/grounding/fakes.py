"""In-process ModelClient test doubles (no network), matching the style of
tests/pipeline/mocks.py.
"""

from __future__ import annotations


class RecordingModelClient:
    """Returns a canned reply and records exactly what it was asked."""

    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_model: str | None = None
        self.last_system: str | None = None
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_model = model
        self.last_system = system
        self.last_user = user
        return self.reply


class ExplodingModelClient:
    """Raises if ever called -- proves a code path never reaches the model."""

    def complete(self, *, model: str, system: str, user: str) -> str:
        raise AssertionError("model client should not be called on this path")
