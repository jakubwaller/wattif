"""Historical hourly weather from Open-Meteo's archive.

Wind is the largest uncontrolled variable in a ride's average speed, so without it a
fitness trend is mostly a wind trend. This is the one place wattif talks to the internet.

Two deliberate constraints. Coordinates are rounded to 0.1 degrees, roughly 11 km, before
they leave the machine: that is finer than the weather model's own grid, so it costs
nothing in accuracy while making the request useless for locating your home. And every
response is cached on disk, so a re-analysis of your history is offline and instant.

Data by Open-Meteo.com, CC BY 4.0.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_FIELDS = "wind_speed_10m,wind_direction_10m,temperature_2m,surface_pressure"
COORD_PRECISION = 1  # decimal places: ~11 km
TIMEOUT_S = 30


def coarsen(lat: float, lon: float) -> tuple[float, float]:
    """Blur a position to the weather grid before it leaves the machine."""
    return round(lat, COORD_PRECISION), round(lon, COORD_PRECISION)


class WeatherArchive:
    """Cached access to hourly historical weather."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, lat: float, lon: float, day: date) -> Path:
        return self.cache_dir / f"{lat:+.1f}_{lon:+.1f}_{day.isoformat()}.json"

    def hourly(self, lat: float, lon: float, day: date) -> dict | None:
        """Hourly weather for one coarsened location and day, or None if unavailable.

        Returns the raw Open-Meteo `hourly` block: parallel lists keyed by `time`.
        """
        lat, lon = coarsen(lat, lon)
        cached = self._cache_file(lat, lon, day)
        if cached.is_file():
            return json.loads(cached.read_text()) or None

        try:
            response = requests.get(
                ARCHIVE_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                    "hourly": HOURLY_FIELDS,
                    "timezone": "UTC",
                    "wind_speed_unit": "ms",
                },
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            hourly = response.json().get("hourly")
        except Exception as exc:
            print(f"  weather lookup failed for {day}: {type(exc).__name__}")
            return None

        if hourly:
            cached.write_text(json.dumps(hourly))
        return hourly


def hour_index(hourly: dict, hour_utc: int) -> int | None:
    """Position of a given UTC hour within the archive's parallel lists."""
    times = hourly.get("time") or []
    for index, stamp in enumerate(times):
        if stamp.endswith(f"T{hour_utc:02d}:00"):
            return index
    return None
