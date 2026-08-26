"""Turn one ride's streams into comparable numbers.

The output that matters is `equivalent_kmh`: the speed this effort would have produced on
flat ground in still air. Raw average speed says as much about the wind and the route as
about the rider, which is why a fitness trend built on it is mostly a weather trend.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ..config import Profile
from ..model.geo import bearing_deg, headwind_ms, scale_wind_to_rider
from ..model.physics import air_density, equivalent_flat_still_air_speed, power_at_wheel
from ..weather.open_meteo import WeatherArchive, hour_index

MOVING_MS = 0.5
ALTITUDE_WINDOW = 5  # records; barometric altitude is noisy at 1-3 s resolution
MAX_GRADIENT = 0.20  # anything steeper is a sensor artefact on Hamburg roads


@dataclass(frozen=True)
class RideMetrics:
    activity_id: str
    moving_s: float
    distance_m: float
    raw_kmh: float
    equivalent_kmh: float
    mean_power_w: float
    mean_hr: float
    mean_headwind_ms: float
    ascent_m: float
    weather: bool

    @property
    def efficiency(self) -> float:
        """Corrected speed per heartbeat. The fitness signal, once wind is removed."""
        return self.equivalent_kmh / self.mean_hr if self.mean_hr else 0.0


def _prepare(records: pl.DataFrame) -> pl.DataFrame:
    """Smooth the noisy channels and derive gradient, heading and acceleration."""
    df = records.drop_nulls(["speed_ms", "distance_m", "timestamp"]).sort("timestamp")
    if df.height < 10:
        return df

    df = df.with_columns(
        alt_smooth=pl.col("altitude_m").rolling_mean(ALTITUDE_WINDOW, min_samples=1),
        dt=pl.col("timestamp").diff().dt.total_seconds(),
        d_dist=pl.col("distance_m").diff(),
    )
    df = df.with_columns(
        gradient=(pl.col("alt_smooth").diff() / pl.col("d_dist"))
        .fill_nan(0.0)
        .fill_null(0.0)
        .clip(-MAX_GRADIENT, MAX_GRADIENT),
        accel=(pl.col("speed_ms").diff() / pl.col("dt")).fill_nan(0.0).fill_null(0.0),
    )
    return df.filter(pl.col("speed_ms") > MOVING_MS, pl.col("dt") > 0, pl.col("dt") < 60)


def _headings(df: pl.DataFrame) -> list[float | None]:
    """Compass bearing per record, from each point to the next."""
    if "lat" not in df.columns:
        return [None] * df.height
    lats, lons = df["lat"].to_list(), df["lon"].to_list()
    out: list[float | None] = []
    for i in range(len(lats) - 1):
        a, b, c, d = lats[i], lons[i], lats[i + 1], lons[i + 1]
        out.append(None if None in (a, b, c, d) else bearing_deg(a, b, c, d))
    out.append(out[-1] if out else None)
    return out


def analyse(
    activity_id: str,
    records: pl.DataFrame,
    profile: Profile,
    archive: WeatherArchive | None = None,
    mass_kg: float | None = None,
) -> RideMetrics | None:
    """Compute corrected metrics for one ride. Returns None if the streams are too thin."""
    df = _prepare(records)
    if df.height < 10:
        return None

    total_kg = mass_kg if mass_kg is not None else profile.total_kg

    headings = _headings(df)
    timestamps = df["timestamp"].to_list()

    # One weather lookup per (day, hour) rather than per record.
    conditions: dict[int, tuple[float, float, float]] = {}
    have_weather = False
    if archive is not None and "lat" in df.columns and df["lat"].null_count() < df.height:
        lat = df["lat"].drop_nulls().median()
        lon = df["lon"].drop_nulls().median()
        hourly = archive.hourly(lat, lon, timestamps[0].date())
        for hour in sorted({t.hour for t in timestamps}) if hourly else []:
            index = hour_index(hourly, hour)
            if index is None:
                continue
            wind = scale_wind_to_rider(hourly["wind_speed_10m"][index] or 0.0)
            conditions[hour] = (
                wind,
                hourly["wind_direction_10m"][index] or 0.0,
                air_density(
                    hourly["temperature_2m"][index] or 15.0,
                    hourly["surface_pressure"][index] or 1013.0,
                ),
            )
            have_weather = True

    speeds = df["speed_ms"].to_list()
    gradients = df["gradient"].to_list()
    accels = df["accel"].to_list()
    dts = df["dt"].to_list()

    total_power = 0.0
    total_time = 0.0
    total_headwind = 0.0

    for i, speed in enumerate(speeds):
        wind_ms, wind_dir, rho = conditions.get(timestamps[i].hour, (0.0, 0.0, 1.225))
        head = 0.0
        if wind_ms and headings[i] is not None:
            head = headwind_ms(wind_ms, wind_dir, headings[i])

        watts = power_at_wheel(
            speed,
            gradient=gradients[i],
            headwind_ms=head,
            accel_ms2=accels[i],
            mass_kg=total_kg,
            cda=profile.cda,
            crr=profile.crr,
            rho=rho,
        )
        total_power += max(watts, 0.0) * dts[i]
        total_headwind += head * dts[i]
        total_time += dts[i]

    mean_power = total_power / total_time if total_time else 0.0
    distance = float(df["distance_m"].max() - df["distance_m"].min())
    ascent = float(
        df["alt_smooth"].diff().clip(0, None).sum() if "alt_smooth" in df.columns else 0.0
    )

    return RideMetrics(
        activity_id=activity_id,
        moving_s=total_time,
        distance_m=distance,
        raw_kmh=distance / total_time * 3.6 if total_time else 0.0,
        equivalent_kmh=equivalent_flat_still_air_speed(
            mean_power, mass_kg=total_kg, cda=profile.cda, crr=profile.crr
        )
        * 3.6,
        mean_power_w=mean_power / (1 - profile.drivetrain_loss),
        mean_hr=float(df["heart_rate"].mean() or 0.0),
        mean_headwind_ms=total_headwind / total_time if total_time else 0.0,
        ascent_m=ascent,
        weather=have_weather,
    )
