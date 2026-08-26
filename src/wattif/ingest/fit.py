"""Decode FIT files into a normalised per-record table.

FIT is the common denominator: it is what Garmin records natively and what Strava hands
back in a bulk export. Everything downstream reads the table this module produces, so a
new source only has to deliver FIT bytes.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl
from garmin_fit_sdk import Decoder, Stream

SEMICIRCLE_TO_DEG = 180.0 / 2**31

LAP_SCHEMA = {
    "lap": pl.Int32,
    "start_time": pl.Datetime(time_unit="us", time_zone="UTC"),
    "elapsed_s": pl.Float64,
    "distance_m": pl.Float64,
    "trigger": pl.Utf8,
}

# Normalised name -> FIT field names in order of preference. The "enhanced" variants carry
# more range and precision; older devices only write the plain ones.
_RECORD_FIELDS: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp",),
    "distance_m": ("distance",),
    "speed_ms": ("enhanced_speed", "speed"),
    "altitude_m": ("enhanced_altitude", "altitude"),
    "heart_rate": ("heart_rate",),
    "cadence": ("cadence",),
    "power_w": ("power",),
    "temperature_c": ("temperature",),
}


@dataclass(frozen=True)
class Activity:
    """One ride: a summary row, its per-record streams, and its lap boundaries."""

    activity_id: str
    summary: dict
    records: pl.DataFrame
    laps: pl.DataFrame

    def _has_any(self, column: str) -> bool:
        if column not in self.records.columns:
            return False
        return self.records[column].null_count() < self.records.height

    @property
    def has_position(self) -> bool:
        return self._has_any("lat")

    @property
    def has_power(self) -> bool:
        return self._has_any("power_w")


def _read_bytes(path: Path) -> io.BytesIO:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return io.BytesIO(raw)


def _first(mesgs: list[dict] | None) -> dict:
    return mesgs[0] if mesgs else {}


def _pick(record: dict, names: tuple[str, ...]):
    for name in names:
        value = record.get(name)
        if value is not None:
            return value
    return None


def _lap_table(messages: dict) -> pl.DataFrame:
    """Lap boundaries, with the trigger that produced each one.

    The trigger matters: `manual` means the rider pressed the button, which is how an A/B
    block gets marked. `distance` is the watch's own auto-lap and is useful as a ready-made
    block grid, but the rider did not choose where it fell.
    """
    laps = messages.get("lap_mesgs") or []
    rows = [
        {
            "lap": index,
            "start_time": lap.get("start_time"),
            "elapsed_s": lap.get("total_elapsed_time"),
            "distance_m": lap.get("total_distance"),
            "trigger": str(lap.get("lap_trigger")) if lap.get("lap_trigger") is not None else None,
        }
        for index, lap in enumerate(laps)
    ]
    if not rows:
        return pl.DataFrame(schema=LAP_SCHEMA)
    return pl.DataFrame(rows, schema_overrides=LAP_SCHEMA)


def _align_time_zone(starts: pl.DataFrame, frame: pl.DataFrame) -> pl.DataFrame:
    """Put lap start times in the same time zone as the record stream.

    Most files carry UTC on both, but a device that writes naive timestamps would otherwise
    fail the join outright. Normalising through naive UTC keeps the instants unchanged.
    """
    want = getattr(frame.schema["timestamp"], "time_zone", None)
    have = getattr(starts.schema["start_time"], "time_zone", None)
    if want == have:
        return starts

    column = pl.col("start_time")
    if have is not None:
        column = column.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
    if want is not None:
        column = column.dt.replace_time_zone(want)
    return starts.with_columns(column)


def _assign_laps(frame: pl.DataFrame, laps: pl.DataFrame) -> pl.DataFrame:
    """Tag every record with the lap it falls in, by timestamp."""
    if not laps.height or "timestamp" not in frame.columns:
        return frame.with_columns(lap=pl.lit(None, dtype=pl.Int32))

    starts = laps.drop_nulls("start_time").sort("start_time")
    if not starts.height:
        return frame.with_columns(lap=pl.lit(None, dtype=pl.Int32))
    starts = _align_time_zone(starts, frame)

    # A record belongs to the last lap that started at or before it.
    return (
        frame.sort("timestamp")
        .join_asof(
            starts.select("start_time", "lap"),
            left_on="timestamp",
            right_on="start_time",
            strategy="backward",
        )
        .drop("start_time")
        .with_columns(pl.col("lap").cast(pl.Int32))
    )


def _activity_id(messages: dict, path: Path) -> str:
    """A stable id for the same ride regardless of which source delivered it.

    `time_created` comes off the device that recorded the file, so a Garmin download and a
    Strava export of the same ride agree on it. Falls back to the filename stem.
    """
    created = _first(messages.get("file_id_mesgs")).get("time_created")
    if isinstance(created, datetime):
        return created.strftime("%Y%m%dT%H%M%SZ")
    return path.name.split(".")[0]


def read_fit(path: Path) -> Activity:
    """Decode one FIT file. Raises ValueError if it is not readable as FIT."""
    stream = Stream.from_bytes_io(_read_bytes(path))
    decoder = Decoder(stream)
    if not decoder.is_fit():
        raise ValueError(f"{path.name} is not a FIT file")

    # Leave date_time conversion on: `_activity_id` depends on `time_created` being a
    # datetime, and the catalog stores real timestamps rather than FIT epoch offsets.
    messages, errors = decoder.read()
    records = messages.get("record_mesgs") or []
    if not records:
        raise ValueError(f"{path.name} contains no record messages")

    rows = []
    for record in records:
        row = {name: _pick(record, names) for name, names in _RECORD_FIELDS.items()}
        lat, lon = record.get("position_lat"), record.get("position_long")
        row["lat"] = lat * SEMICIRCLE_TO_DEG if lat is not None else None
        row["lon"] = lon * SEMICIRCLE_TO_DEG if lon is not None else None
        rows.append(row)

    frame = pl.DataFrame(rows, infer_schema_length=None)
    laps = _lap_table(messages)
    frame = _assign_laps(frame, laps)
    session = _first(messages.get("session_mesgs"))
    activity_id = _activity_id(messages, path)

    summary = {
        "activity_id": activity_id,
        "source_file": path.name,
        "sport": session.get("sport"),
        "sub_sport": session.get("sub_sport"),
        "start_time": session.get("start_time"),
        "elapsed_s": session.get("total_elapsed_time"),
        "moving_s": session.get("total_timer_time"),
        "distance_m": session.get("total_distance"),
        "ascent_m": session.get("total_ascent"),
        "avg_speed_ms": session.get("enhanced_avg_speed") or session.get("avg_speed"),
        "avg_hr": session.get("avg_heart_rate"),
        "max_hr": session.get("max_heart_rate"),
        "avg_power_w": session.get("avg_power"),
        "n_records": frame.height,
        "n_laps": laps.height,
        "manual_laps": int((laps["trigger"] == "manual").sum()) if laps.height else 0,
        "decode_errors": len(errors or []),
    }
    return Activity(activity_id=activity_id, summary=summary, records=frame, laps=laps)


def find_fit_files(root: Path) -> list[Path]:
    """Every FIT file under a directory, compressed or not."""
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.name.lower().endswith((".fit", ".fit.gz"))
    )
