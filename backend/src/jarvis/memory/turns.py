"""``Turn`` -- one conversational turn persisted via ``MemoryStore.add_turns``.

Not pinned in docs/DESIGN.md (it only says the method takes ``list[Turn]``);
this ticket owns the type, kept intentionally minimal: a role + text pair,
mirroring the ``{"role": ..., "content": ...}`` message shape mem0's own
``Memory.add()`` expects, which is all mem0 (or any future backend) actually
needs. ``role`` is a plain ``str`` rather than a ``Literal`` so callers can
pass "system" (mem0 silently skips system messages -- see
mem0/memory/main.py ``_add_to_vector_store``) without the type getting in
the way.
"""

from __future__ import annotations

from typing import TypedDict


class Turn(TypedDict):
    role: str  # "user" | "assistant" | "system"
    text: str
