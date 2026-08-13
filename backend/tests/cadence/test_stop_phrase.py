"""jarvis.cadence.policy: stop-phrase normalization/detection. Pure-unit
style, matching tests/grounding/test_policy.py.
"""

from __future__ import annotations

import pytest

from jarvis.cadence.policy import STOP_PHRASES, is_stop_phrase, normalize_utterance


def test_stop_phrases_are_pinned_exactly():
    assert STOP_PHRASES == ("quiet jarvis", "jarvis quiet", "stop")


@pytest.mark.parametrize(
    "text",
    [
        "Stop!",
        "stop",
        " STOP ",
        "Okay, stop.",
        "Jarvis, quiet.",
        "quiet Jarvis",
    ],
)
def test_is_stop_phrase_true_cases(text):
    assert is_stop_phrase(text)


@pytest.mark.parametrize(
    "text",
    [
        "stop the timer",
        "don't stop",
        "stopping",
        "",
        "quiet",
    ],
)
def test_is_stop_phrase_false_cases(text):
    assert not is_stop_phrase(text)


def test_normalize_utterance_lowercases_and_strips_punctuation():
    assert normalize_utterance("Stop!") == "stop"
    assert normalize_utterance(" STOP ") == "stop"


def test_normalize_utterance_collapses_internal_whitespace():
    assert normalize_utterance("stop   the    timer") == "stop the timer"


def test_normalize_utterance_drops_a_single_leading_discourse_marker():
    assert normalize_utterance("Hey, jarvis quiet") == "jarvis quiet"
    assert normalize_utterance("Okay, stop.") == "stop"


def test_normalize_utterance_is_what_is_stop_phrase_checks_against():
    # is_stop_phrase is defined purely in terms of normalize_utterance -- the
    # normalized form of every true-case above must land exactly on a member
    # of STOP_PHRASES.
    for text in ("Stop!", "Jarvis, quiet.", "quiet Jarvis"):
        assert normalize_utterance(text) in STOP_PHRASES
