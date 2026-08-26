"""Positions to headings, and headings against the wind."""

from __future__ import annotations

import math

EARTH_R_M = 6_371_000.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


def headwind_ms(wind_speed_ms: float, wind_from_deg: float, heading_deg: float) -> float:
    """Component of wind opposing travel. Positive is headwind, negative is tailwind.

    Meteorological convention: `wind_from_deg` is the direction the wind blows *from*. A
    wind from the north while heading north is a pure headwind, so the projection is
    cos(from - heading), which is 1 in that case.
    """
    return wind_speed_ms * math.cos(math.radians(wind_from_deg - heading_deg))


def scale_wind_to_rider(
    wind_10m_ms: float, roughness_m: float = 0.1, rider_h_m: float = 1.6
) -> float:
    """Bring a 10 m forecast wind down to the height a cyclist actually occupies.

    Weather models report wind at 10 m, where it is meaningfully faster than at chest
    height. The logarithmic wind profile gives roughly 65% of the 10 m value over mixed
    suburban terrain, which is the single largest correction in this module and also the
    least certain.
    """
    if wind_10m_ms <= 0:
        return 0.0
    return wind_10m_ms * math.log(rider_h_m / roughness_m) / math.log(10.0 / roughness_m)
