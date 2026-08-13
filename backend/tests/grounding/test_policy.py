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
