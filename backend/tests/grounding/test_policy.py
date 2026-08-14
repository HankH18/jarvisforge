"""ANSWER_POLICY (docs/DESIGN.md §Interfaces) states the grounding rules the
model is instructed with. The refusal *decision* is made in our own code
(see test_synthesize_refusal.py); this only checks the pinned rules are
actually present in the system prompt that reaches the model.
"""

from __future__ import annotations

from jarvis.llm.policy import ANSWER_POLICY
from jarvis.llm.types import REFUSAL


def test_answer_policy_requires_strict_grounding_in_results_data():
    assert "data" in ANSWER_POLICY.lower()
    assert "only" in ANSWER_POLICY.lower()


def test_answer_policy_forbids_training_knowledge_for_github_or_weather():
    lowered = ANSWER_POLICY.lower()
    assert "training knowledge" in lowered
    assert "github" in lowered
    assert "weather" in lowered


def test_answer_policy_pins_the_exact_refusal_prefix():
    assert REFUSAL in ANSWER_POLICY


def test_answer_policy_requires_honoring_prefs_verbatim():
    lowered = ANSWER_POLICY.lower()
    assert "preference" in lowered
    assert "verbatim" in lowered


def test_answer_policy_treats_a_negative_fact_as_a_supported_answer_not_a_refusal():
    """Binds the fd5b88b rule (T-19): a fact the data establishes NEGATIVELY
    -- absent, unsupported, zero, or a listed limitation/non-goal -- is a
    supported ANSWER, not a refusal, and the REFUSAL prefix is reserved for
    when the data doesn't address the question at all. Unlike the bare
    substring checks above (any reasonable prose satisfies "data"/"only"/
    "preference"/"verbatim"), every assertion here names a term or exact
    phrase that is specific to this rule and absent from the policy text
    that predates fd5b88b -- deleting the added paragraph makes every one
    of them fail, not just weaken.
    """
    lowered = ANSWER_POLICY.lower()

    # The rule names what a negative fact even IS.
    assert "negatively" in lowered
    for term in ("absent", "zero", "limitation", "non-goal"):
        assert term in lowered, f"negative-fact rule must name {term!r} as negative evidence"

    # The rule's conclusion: a negative fact is answered, not refused. Pinned
    # as one CONTIGUOUS phrase spanning the colon into the instruction that
    # follows it ("...not a refusal: state it plainly") rather than two
    # separate substrings, so a rewrite can't preserve "not a refusal" in
    # isolation while wrapping it in a negation (e.g. "it is false that X is
    # a supported answer, not a refusal -- always refuse instead") without
    # also breaking this exact span.
    assert "is a supported answer, not a refusal: state it plainly" in lowered

    # The rule scopes REFUSAL down to true non-support -- not merely
    # negative content -- with an exact, load-bearing phrase.
    assert (
        f'use the "{REFUSAL.lower()}" prefix only when the supplied data does '
        "not address the question at all" in lowered
    )
