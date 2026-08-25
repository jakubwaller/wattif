"""Body mass over time, from Garmin's weigh-in log.

Mass is an input to the power model, and using today's figure for a ride two years ago
quietly biases the past. The effect is small on flat ground, where mass only enters through
rolling resistance, but it points the same way for every old ride, and a consistent bias is
exactly what corrupts a trend.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ..config import Paths


def fetch_weigh_ins(client, start: date, end: date) -> pl.DataFrame:
    """Pull the weigh-in log. Returns an empty frame if nothing is recorded."""
    rows = []
    payload = client.get_weigh_ins(start.isoformat(), end.isoformat()) or {}
    for entry in payload.get("dailyWeightSummaries") or []:
        metrics = entry.get("allWeightMetrics") or [{}]
        grams = metrics[0].get("weight")
        day = entry.get("summaryDate")
        if grams and day:
            rows.append({"date": date.fromisoformat(day), "kg": grams / 1000.0})
    return pl.DataFrame(rows).sort("date") if rows else pl.DataFrame()


def save(paths: Paths, frame: pl.DataFrame) -> None:
    if frame.height:
        frame.write_parquet(paths.root / "weight.parquet")


def load(paths: Paths) -> pl.DataFrame:
    path = paths.root / "weight.parquet"
    return pl.read_parquet(path) if path.is_file() else pl.DataFrame()


class WeightSeries:
    """Body mass on any date, interpolated between weigh-ins."""

    def __init__(self, frame: pl.DataFrame):
        self.points: list[tuple[date, float]] = (
            list(zip(frame["date"].to_list(), frame["kg"].to_list(), strict=True))
            if frame.height
            else []
        )

    def __bool__(self) -> bool:
        return bool(self.points)

    def at(self, when: date, default: float) -> float:
        """Mass on `when`, linearly interpolated. Clamps outside the logged range."""
        if not self.points:
            return default
        if when <= self.points[0][0]:
            return self.points[0][1]
        if when >= self.points[-1][0]:
            return self.points[-1][1]

        for (d0, w0), (d1, w1) in zip(self.points, self.points[1:], strict=False):
            if d0 <= when <= d1:
                span = (d1 - d0) or timedelta(days=1)
                fraction = (when - d0) / span
                return w0 + (w1 - w0) * fraction
        return default
