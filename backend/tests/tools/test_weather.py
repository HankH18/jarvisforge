"""get_weather() (SPEC R8; docs/DESIGN.md §Interfaces): two keyless
Open-Meteo calls (geocoding, then forecast) returning a ``ToolResult``.

All HTTP is intercepted by respx and replayed from recorded fixtures under
tests/tools/fixtures/weather/ (see that directory's payloads) -- no test in
this file makes a live call.

Three acceptance items, each proven by a dedicated test (see docstrings
below for which test covers which):
  1. pinned ToolResult shape for now/today/week, from recorded fixtures.
  2. unknown location / HTTP error / network timeout -> ToolResult.error set,
     data empty, and that error feeds the REAL jarvis.llm.synthesize_grounded
     to a REFUSAL answer (with a model client that raises if called).
  3. fetched_at is real ISO-8601 UTC, and reaches the synthesize_grounded
     prompt (a recency question is answerable from it).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from jarvis.llm.synthesis import synthesize_grounded
from jarvis.llm.types import REFUSAL
from jarvis.tools.registry import ToolSpec
from jarvis.tools.weather import WEATHER_TOOL, get_weather
from jarvis.tools.weather.service import FORECAST_URL, GEOCODING_URL

FIXTURES = Path(__file__).parent / "fixtures" / "weather"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class ExplodingModelClient:
    """Raises if ever called -- proves a code path never reaches the model.

    Self-contained here (not imported from tests/grounding/fakes.py) since
    this ticket's file scope is backend/tests/tools/** only.
    """

    def complete(self, *, model: str, system: str, user: str) -> str:
        raise AssertionError("model client should not be called on this path")


class RecordingModelClient:
    """Returns a canned reply and records exactly what it was asked."""

    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


def _mock_geocoding(fixture_name: str) -> None:
    respx.get(GEOCODING_URL).mock(return_value=httpx.Response(200, json=_fixture(fixture_name)))


def _mock_forecast(fixture_name: str) -> None:
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=_fixture(fixture_name)))


# ---------------------------------------------------------------------------
# 1. Pinned ToolResult shape for now/today/week, from recorded fixtures.
# ---------------------------------------------------------------------------


@respx.mock
def test_get_weather_now_returns_pinned_shape_from_recorded_fixtures():
    _mock_geocoding("geocode_london.json")
    _mock_forecast("forecast_now.json")

    result = get_weather("London", "now")

    assert result["source"] == "weather:open-meteo"
    assert result["error"] is None
    assert result["data"]["location"] == "London, United Kingdom"
    assert result["data"]["when"] == "now"
    assert result["data"]["latitude"] == _fixture("geocode_london.json")["results"][0]["latitude"]
    recorded_current = _fixture("forecast_now.json")["current"]
    assert result["data"]["current"] == recorded_current
    assert "daily" not in result["data"]


@respx.mock
def test_get_weather_today_returns_pinned_shape_from_recorded_fixtures():
    _mock_geocoding("geocode_london.json")
    _mock_forecast("forecast_today.json")

    result = get_weather("London", "today")

    assert result["source"] == "weather:open-meteo"
    assert result["error"] is None
    assert result["data"]["when"] == "today"
    recorded_daily = _fixture("forecast_today.json")["daily"]
    assert result["data"]["daily"] == recorded_daily
    assert len(result["data"]["daily"]["time"]) == 1
    assert "current" not in result["data"]


@respx.mock
def test_get_weather_week_returns_pinned_shape_from_recorded_fixtures():
    _mock_geocoding("geocode_london.json")
    _mock_forecast("forecast_week.json")

    result = get_weather("London", "week")

    assert result["source"] == "weather:open-meteo"
    assert result["error"] is None
    assert result["data"]["when"] == "week"
    recorded_daily = _fixture("forecast_week.json")["daily"]
    assert result["data"]["daily"] == recorded_daily
    assert len(result["data"]["daily"]["time"]) == 7
    assert "current" not in result["data"]


def test_get_weather_matches_the_pinned_signature():
    """docs/DESIGN.md §Interfaces: get_weather(location: str, when: Literal)
    -> ToolResult, and is registered non-heavy (stays on $CONVO_MODEL).
    """
    assert isinstance(WEATHER_TOOL, ToolSpec)
    assert WEATHER_TOOL.name == "get_weather"
    assert WEATHER_TOOL.heavy is False


# ---------------------------------------------------------------------------
# 2. Unknown location / API error / timeout -> error set, REFUSAL downstream.
# ---------------------------------------------------------------------------


@respx.mock
def test_unknown_location_yields_error_and_empty_data():
    """Zero-match geocoding search (Open-Meteo omits "results" entirely)."""
    _mock_geocoding("geocode_not_found.json")

    result = get_weather("zzzznotarealplacexyz123", "now")

    assert result["source"] == "weather:open-meteo"
    assert result["data"] == {}
    assert result["error"] is not None


@respx.mock
def test_unknown_location_feeds_the_refusal_path_not_a_guess():
    """Acceptance #2, end-to-end: an errored ToolResult, passed into the
    REAL synthesize_grounded, refuses via the structural no-evidence gate
    before ever touching a model client (ExplodingModelClient raises if
    called -- that is what proves this is a refusal, not a guess).
    """
    _mock_geocoding("geocode_not_found.json")

    result = get_weather("zzzznotarealplacexyz123", "now")
    answer = synthesize_grounded(
        "What's the weather in zzzznotarealplacexyz123?",
        [result],
        [],
        client=ExplodingModelClient(),
    )

    assert answer == REFUSAL


@respx.mock
def test_api_5xx_yields_error_and_empty_data():
    _mock_geocoding("geocode_london.json")
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(500, text="internal error"))

    result = get_weather("London", "now")

    assert result["source"] == "weather:open-meteo"
    assert result["data"] == {}
    assert result["error"] is not None
    assert "500" in result["error"]


@respx.mock
def test_api_5xx_feeds_the_refusal_path_not_a_guess():
    _mock_geocoding("geocode_london.json")
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(503, text="unavailable"))

    result = get_weather("London", "now")
    answer = synthesize_grounded(
        "What's the weather in London?", [result], [], client=ExplodingModelClient()
    )

    assert answer == REFUSAL


@respx.mock
def test_network_timeout_yields_error_and_empty_data():
    respx.get(GEOCODING_URL).mock(side_effect=httpx.TimeoutException("connect timed out"))

    result = get_weather("London", "now")

    assert result["source"] == "weather:open-meteo"
    assert result["data"] == {}
    assert result["error"] is not None


@respx.mock
def test_network_timeout_feeds_the_refusal_path_not_a_guess():
    respx.get(GEOCODING_URL).mock(side_effect=httpx.TimeoutException("connect timed out"))

    result = get_weather("London", "now")
    answer = synthesize_grounded(
        "What's the weather in London?", [result], [], client=ExplodingModelClient()
    )

    assert answer == REFUSAL


# ---------------------------------------------------------------------------
# 3. fetched_at is real ISO-8601 UTC, and reaches the synthesize_grounded
#    prompt -- a recency question is answerable from it.
# ---------------------------------------------------------------------------


@respx.mock
def test_fetched_at_is_timezone_aware_iso8601_utc():
    _mock_geocoding("geocode_london.json")
    _mock_forecast("forecast_now.json")

    before = datetime.now(UTC)
    result = get_weather("London", "now")
    after = datetime.now(UTC)

    parsed = datetime.fromisoformat(result["fetched_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert before <= parsed <= after


@respx.mock
def test_fetched_at_is_populated_even_on_the_error_path():
    _mock_geocoding("geocode_not_found.json")

    result = get_weather("zzzznotarealplacexyz123", "now")

    parsed = datetime.fromisoformat(result["fetched_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


@respx.mock
def test_fetched_at_reaches_the_model_prompt_so_recency_is_answerable():
    """``synthesize_grounded``'s prompt builder forwards each result's
    ``source``/``data`` only, not the top-level ``fetched_at`` field (see
    llm/synthesis.py: ``json.dumps({"source": ..., "data": ...})``). This
    module therefore copies ``fetched_at`` into ``data`` too (see
    tools/weather/service.py), so a recency question ("how current is
    this?") is answerable from the real production prompt.
    """
    _mock_geocoding("geocode_london.json")
    _mock_forecast("forecast_now.json")
    result = get_weather("London", "now")
    client = RecordingModelClient(reply=f"As of {result['fetched_at']}, it's clear in London.")

    answer = synthesize_grounded(
        "How current is that weather reading?",
        [result],
        [],
        model="stub-model",
        client=client,
    )

    assert client.calls == 1
    assert result["fetched_at"] in client.last_user
    assert result["fetched_at"] in answer
