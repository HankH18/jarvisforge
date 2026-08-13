"""get_weather tool (SPEC R8; docs/DESIGN.md §Interfaces).

Two keyless Open-Meteo calls: geocoding (location name -> lat/lon) then
forecast. ``when`` selects both what is requested and what is returned --
"now" -> current conditions, "today" -> today's daily summary, "week" -> the
multi-day daily forecast. No API key (Open-Meteo is keyless) and no caching
layer (T-3 non-goal; every call hits the network fresh).

``data`` is the ONLY material ``synthesize_grounded`` may draw on (see
jarvis.llm.types.ToolResult) -- its prompt builder forwards each result's
``source``/``data`` but NOT the top-level ``fetched_at`` metadata field,
so this module also copies ``fetched_at`` into ``data`` itself. That is what
makes a recency question ("how current is this?") answerable from the model
prompt, per T-3 acceptance #3, without touching llm/synthesis.py (T-2,
out of this ticket's scope).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from jarvis.llm.types import ToolResult

from ..registry import ToolSpec

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_TIMEOUT_SECONDS = 10.0

_CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation,weather_code,"
    "wind_speed_10m,relative_humidity_2m"
)
_DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,weather_code"
)

When = Literal["now", "today", "week"]

_SOURCE = "weather:open-meteo"


def get_weather(location: str, when: When) -> ToolResult:
    """Resolve ``location`` to coordinates via Open-Meteo geocoding, then
    fetch a forecast shaped by ``when``. Any failure (unknown location, HTTP
    error status, network error) returns a ``ToolResult`` with ``error`` set
    and empty ``data`` -- never a guess (SPEC R6/R8); that error shape feeds
    ``synthesize_grounded``'s structural no-evidence gate, which refuses
    before any model call.
    """
    fetched_at = datetime.now(UTC).isoformat()
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            geocoded = _geocode(client, location)
            if geocoded is None:
                return ToolResult(
                    source=_SOURCE,
                    fetched_at=fetched_at,
                    data={},
                    error=f"no location found for {location!r}",
                )
            latitude, longitude, resolved_name = geocoded
            forecast = _fetch_forecast(client, latitude, longitude, when)
    except httpx.HTTPStatusError as exc:
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"open-meteo returned HTTP {exc.response.status_code}",
        )
    except httpx.HTTPError as exc:
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"open-meteo request failed: {exc!r}",
        )

    data: dict[str, Any] = {
        "location": resolved_name,
        "latitude": latitude,
        "longitude": longitude,
        "when": when,
        "fetched_at": fetched_at,
        **forecast,
    }
    return ToolResult(source=_SOURCE, fetched_at=fetched_at, data=data, error=None)


def _geocode(client: httpx.Client, location: str) -> tuple[float, float, str] | None:
    """Look up ``location`` -> (latitude, longitude, display name), or None
    when Open-Meteo has no match (its API omits "results" entirely for a
    zero-match search -- it does not return an empty list).
    """
    response = client.get(
        GEOCODING_URL,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return None
    top = results[0]
    name = top["name"]
    country = top.get("country")
    display_name = f"{name}, {country}" if country else name
    return top["latitude"], top["longitude"], display_name


def _fetch_forecast(
    client: httpx.Client, latitude: float, longitude: float, when: When
) -> dict[str, Any]:
    params: dict[str, Any] = {"latitude": latitude, "longitude": longitude, "timezone": "auto"}
    if when == "now":
        params["current"] = _CURRENT_FIELDS
    else:
        params["daily"] = _DAILY_FIELDS
        params["forecast_days"] = 1 if when == "today" else 7

    response = client.get(FORECAST_URL, params=params)
    response.raise_for_status()
    payload = response.json()

    forecast: dict[str, Any] = {"timezone": payload.get("timezone")}
    if when == "now":
        forecast["current"] = payload.get("current", {})
        forecast["current_units"] = payload.get("current_units", {})
    else:
        forecast["daily"] = payload.get("daily", {})
        forecast["daily_units"] = payload.get("daily_units", {})
    return forecast


# Non-heavy per DESIGN.md §Interfaces (stays on $CONVO_MODEL).
WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description=(
        "Get live weather for a location from Open-Meteo. `when` selects "
        "current conditions ('now'), today's summary ('today'), or a "
        "multi-day forecast ('week')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Place name, e.g. 'London' or 'Austin, TX'.",
            },
            "when": {"type": "string", "enum": ["now", "today", "week"]},
        },
        "required": ["location", "when"],
    },
    handler=lambda **kwargs: get_weather(**kwargs),
    heavy=False,
)
