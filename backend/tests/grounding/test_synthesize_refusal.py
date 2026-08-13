"""Acceptance #3: given empty/irrelevant tool results, synthesize_grounded's
answer starts with REFUSAL -- exercised on fixture transcripts under
tests/grounding/fixtures/.

Two different things are proven here, deliberately kept apart:

1. STRUCTURAL no-evidence refusal (no results / all-errored / all-empty-data)
   is decided entirely in OUR code (llm/synthesis.has_supporting_evidence),
   before any model client is touched. These tests inject
   ``ExplodingModelClient``, which raises if ever called -- that is what
   proves the refusal is OUR decision, not something a mocked model merely
   "said".

2. Whether data that IS present actually answers a given question is a
   SEMANTIC judgment this module deliberately does NOT make (see the module
   docstring in llm/synthesis.py for why an earlier keyword-overlap version
   of this gate was wrong: it refused plainly answerable questions like
   "Will I need an umbrella?" against ``{"precipitation_mm": 5}``). For that
   case, these tests only prove the real production prompt reaching the
   model carries ANSWER_POLICY, results[].data as the only evidence, and
   prefs verbatim, and that a REFUSAL-prefixed model reply passes through
   unmodified. They do NOT prove the model refuses (or answers) correctly --
   that correctness is measured by the T-7 golden eval set (SPEC R6a), not
   at this layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import ExplodingModelClient, RecordingModelClient

from jarvis.llm.policy import ANSWER_POLICY
from jarvis.llm.synthesis import has_supporting_evidence, synthesize_grounded, usable_results
from jarvis.llm.types import REFUSAL

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# 1. Structural no-evidence refusal -- decided in OUR code.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    ["empty_results.json", "errored_result.json", "empty_data_result.json"],
)
def test_refuses_on_no_evidence_without_ever_calling_the_model(fixture_name):
    case = _load(fixture_name)

    answer = synthesize_grounded(
        case["question"], case["results"], case["prefs"], client=ExplodingModelClient()
    )

    assert answer.startswith(REFUSAL)


@pytest.mark.parametrize(
    "fixture_name",
    ["empty_results.json", "errored_result.json", "empty_data_result.json"],
)
def test_has_supporting_evidence_is_false_for_the_same_fixtures(fixture_name):
    """The deterministic gate itself, unit-tested directly (not just via
    synthesize_grounded), since that gate -- not the model -- is where the
    no-evidence refusal decision lives.
    """
    case = _load(fixture_name)

    assert has_supporting_evidence(case["results"]) is False
    assert usable_results(case["results"]) == []


def test_errored_result_never_counts_as_usable_even_with_real_data():
    """An `error` set always disqualifies a result, regardless of what its
    `data` contains -- the gate does not try to salvage partial data from a
    failed fetch.
    """
    result = {
        "source": "weather:open-meteo",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {"location": "Denver", "temperature_c": 10},
        "error": "upstream timeout",
    }
    assert has_supporting_evidence([result]) is False
    assert usable_results([result]) == []


def test_one_usable_result_among_bad_ones_is_enough_to_clear_the_gate():
    errored = {
        "source": "weather:open-meteo",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {},
        "error": "upstream timeout",
    }
    empty = {
        "source": "github:graphql",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {},
        "error": None,
    }
    usable = {
        "source": "weather:open-meteo",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {"temperature_c": 21},
        "error": None,
    }
    assert has_supporting_evidence([errored, empty, usable]) is True
    assert usable_results([errored, empty, usable]) == [usable]


# ---------------------------------------------------------------------------
# 2. Data present but of unproven relevance -- the gate must NOT refuse this
#    structurally; the real prompt must reach the model, and a model refusal
#    (when it happens) must pass through unmodified. Regression coverage for
#    the wrong-refusal bug found by adversarial review: a keyword-overlap
#    "relevance" heuristic refused all five cases below even though their
#    data plainly answers the question.
# ---------------------------------------------------------------------------


def test_irrelevant_but_present_data_reaches_the_model_unrefused_by_our_gate():
    """This is NOT a claim that the model should refuse here -- it asserts
    the real production prompt (ANSWER_POLICY, results[].data, prefs
    verbatim) reaches the model, and that when the model DOES come back with
    a REFUSAL-prefixed reply, synthesize_grounded passes it through
    unmodified rather than rewriting or re-deciding it.
    """
    case = _load("irrelevant_result.json")
    client = RecordingModelClient(reply=f"{REFUSAL}, the fetched data doesn't cover that.")

    answer = synthesize_grounded(
        case["question"], case["results"], case["prefs"], model="stub-model", client=client
    )

    assert client.calls == 1  # our gate did not refuse structurally
    assert client.last_system == ANSWER_POLICY
    result = case["results"][0]
    assert json.dumps({"source": result["source"], "data": result["data"]}) in client.last_user
    for pref in case["prefs"]:
        assert pref in client.last_user
    assert answer == f"{REFUSAL}, the fetched data doesn't cover that."


@pytest.mark.parametrize(
    "fixture_name",
    [
        "regression_umbrella.json",
        "regression_windy.json",
        "regression_outside.json",
        "regression_hot.json",
        "regression_nyc.json",
    ],
)
def test_answerable_questions_are_not_wrongly_refused_by_keyword_mismatch(fixture_name):
    """Regression for the adversarial-review finding: each of these has data
    that plainly answers the question but shares no literal keyword with it
    (e.g. "umbrella" vs. "precipitation_mm", "NYC" vs. "New York City"). A
    prior keyword-overlap gate wrongly refused all five. The gate must never
    do that again: any structurally usable data reaches the model, full stop.
    """
    case = _load(fixture_name)
    client = RecordingModelClient(reply="a real, non-refusal answer")

    answer = synthesize_grounded(
        case["question"], case["results"], case["prefs"], model="stub-model", client=client
    )

    assert client.calls == 1, "our gate refused structurally instead of asking the model"
    assert not answer.startswith(REFUSAL)
    assert answer == "a real, non-refusal answer"


def test_answers_when_a_result_actually_supports_the_question():
    case = _load("supported_result.json")
    client = RecordingModelClient(reply="It's 21C and clear in Paris right now.")

    answer = synthesize_grounded(
        case["question"], case["results"], case["prefs"], model="stub-model", client=client
    )

    assert answer == "It's 21C and clear in Paris right now."
    assert client.calls == 1
    assert client.last_model == "stub-model"


def test_the_real_prompt_carries_answer_policy_results_data_and_prefs_verbatim():
    """Guards against the injection seam turning into theater: assert on the
    actual request synthesize_grounded builds, not merely that the client
    was called.
    """
    case = _load("supported_result.json")
    client = RecordingModelClient(reply="ok")

    synthesize_grounded(
        case["question"], case["results"], case["prefs"], model="stub-model", client=client
    )

    # ANSWER_POLICY reaches the request unmodified, as the system prompt.
    assert client.last_system == ANSWER_POLICY

    # results[].data reaches the request (as JSON), not paraphrased.
    result_data = case["results"][0]["data"]
    assert json.dumps({"source": case["results"][0]["source"], "data": result_data}) in (
        client.last_user
    )

    # every pref reaches the request verbatim (exact substring, unaltered).
    for pref in case["prefs"]:
        assert pref in client.last_user


def test_prefs_are_honored_verbatim_even_when_empty():
    client = RecordingModelClient(reply="ok")
    result = {
        "source": "weather:open-meteo",
        "fetched_at": "2026-08-13T00:00:00+00:00",
        "data": {"location": "Paris", "temperature_c": 21},
        "error": None,
    }

    synthesize_grounded(
        "What's the weather in Paris?", [result], [], model="stub-model", client=client
    )

    assert "(none)" in client.last_user


def test_blank_model_reply_is_treated_as_a_refusal():
    """Even on the "has supporting evidence" path, a model that comes back
    with nothing usable must not be reported as a real answer.
    """
    case = _load("supported_result.json")
    client = RecordingModelClient(reply="   ")

    answer = synthesize_grounded(
        case["question"], case["results"], case["prefs"], model="stub-model", client=client
    )

    assert answer == REFUSAL
