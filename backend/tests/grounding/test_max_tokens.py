"""T-18: TOOL_MODEL is claude-sonnet-5, not claude-sonnet-4-6. Unlike
claude-sonnet-4-6, omitting the `thinking` request parameter on Sonnet 5
does NOT turn thinking off -- adaptive thinking runs by default -- and
max_tokens caps thinking tokens and answer tokens TOGETHER. Every heavy-tool
turn escalates to TOOL_MODEL via jarvis.llm.routing.route_model(), so
_MAX_TOKENS must leave room for both, or a real grounded answer can be cut
off mid-sentence.

NOTE on why these tests don't use ``fakes.RecordingModelClient``: the
``ModelClient`` Protocol's ``complete()`` never carried ``max_tokens`` --
it is fixed inside ``AnthropicModelClient.complete()`` and never crosses the
injection seam. Threading ``max_tokens`` through that seam would mean
adding it to every call to ``client.complete(...)`` in synthesis.py, which
would break the several *other* ``RecordingModelClient``/
``ExplodingModelClient`` doubles across tests/pipeline, tests/tools, and
tests/memory (all outside this ticket's file scope) the moment
``synthesize_grounded`` called their ``complete()`` with an unexpected
keyword argument. So instead these tests exercise the actual production
boundary -- ``AnthropicModelClient``, the default ``ModelClient`` -- by
replacing the underlying ``anthropic.Anthropic`` SDK client with an
in-memory double. No network call is made either way.
"""

from __future__ import annotations

import anthropic

import jarvis.llm.synthesis as synthesis
from jarvis.llm.synthesis import synthesize_grounded


def _approx_tokens(text: str) -> int:
    """Conservative ~4-chars-per-token estimate for English prose. Not a
    real tokenizer (see shared/token-counting.md: never approximate Claude
    tokens with a non-Anthropic tokenizer) -- good enough only to bound
    "does this obviously not fit," which is all this test needs.
    """
    return len(text) // 4 + 1


# A realistic grounded answer for a heavy-tool turn (github_qa/agent_pr) --
# several sentences, not a one-line weather reply, since T-18's failure mode
# is specifically about longer answers on escalated turns.
_REPRESENTATIVE_ANSWER = (
    "The open pull requests on octocat/Hello-World are #42, adding retry "
    "logic to the sync worker, and #47, fixing a race condition in the "
    "cache invalidation path. Both were opened this week by the same "
    "author and neither has been reviewed yet. PR #42 touches three files "
    "in the worker package and includes a new unit test for the backoff "
    "schedule. PR #47 is smaller -- a single-file change with an inline "
    "comment explaining the lock ordering fix."
)

# Adaptive thinking on Sonnet 5 has no configurable fixed budget -- it is
# the model's own call, not something this module sets. This reserve is a
# conservative floor for "a real adaptive-thinking pass on a non-trivial
# synthesis question," used only to prove the chosen _MAX_TOKENS leaves
# genuine headroom -- not a prediction of actual thinking spend.
_MIN_THINKING_RESERVE_TOKENS = 2000


def test_max_tokens_covers_a_representative_answer_plus_a_thinking_reserve():
    """Binds _MAX_TOKENS to the real reason it was raised: the reserve alone
    already exceeds the old 1024 value, so this fails if _MAX_TOKENS is put
    back to 1024 -- independent of any request-plumbing details.
    """
    required = _approx_tokens(_REPRESENTATIVE_ANSWER) + _MIN_THINKING_RESERVE_TOKENS

    assert required > 1024, "test setup: the reserve should already exceed the old budget"
    assert required <= synthesis._MAX_TOKENS


def test_the_real_anthropic_request_carries_max_tokens_equal_to_the_module_constant(
    monkeypatch,
):
    """Proves _MAX_TOKENS is not just a number sitting unused -- it is
    exactly the max_tokens synthesize_grounded's default (production)
    ModelClient sends in the request. No network: the Anthropic SDK client
    class itself is replaced with an in-memory double before
    AnthropicModelClient() is constructed.
    """
    captured: dict = {}

    class _FakeTextBlock:
        type = "text"

        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.content = [_FakeTextBlock(text)]

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(_REPRESENTATIVE_ANSWER)

    class _FakeAnthropicSDKClient:
        def __init__(self, *args, **kwargs) -> None:
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropicSDKClient)

    result = {
        "source": "github:graphql",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {"open_prs": 2},
        "error": None,
    }

    answer = synthesize_grounded("What PRs are open?", [result], [], model="claude-sonnet-5")

    assert answer == _REPRESENTATIVE_ANSWER
    assert captured["max_tokens"] == synthesis._MAX_TOKENS
    assert captured["max_tokens"] > 1024, "would fail if _MAX_TOKENS is reverted to 1024"
