"""get_weather tool package (T-3; docs/DESIGN.md §Interfaces)."""

from __future__ import annotations

from .service import WEATHER_TOOL, get_weather

__all__ = ["WEATHER_TOOL", "get_weather"]
