"""Read the labels out of a Strava bulk export.

Strava's own API is closed to this use - a subscription is required since June 2026, and
the terms forbid analysing API-obtained data - so the bulk export is the only lawful route.
The export's ride files add nothing over a Garmin download, which is the same FIT by a
longer path. What it does carry, and what nothing else does, is the rider's own labels:
which bike each ride was on, and whether it was a commute.

That matters because an equipment comparison is meaningless if half the rides were on a
different bike. A label is worth more here than another year of unlabelled history.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from .. import store
from ..config import Paths

ACTIVITIES_CSV = "activities.csv"
BIKES_CSV = "bikes.csv"
MATCH_TOLERANCE_S = 180

# Strava writes the athlete's locale into the export, so more than one shape turns up.
_DATE_FORMATS = (
    "%b %d, %Y, %I:%M:%S %p",
    "%d %b %Y, %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y, %H:%M:%S",
)


def _parse_date(value: str) -> datetime | None:
    text = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "1.0", "true", "yes"}


def _read_member(source: Path, name: str) -> str | None:
    """Pull one CSV out of an export, whether it is a zip or an unpacked directory."""
    if source.is_dir():
        path = source / name
        return path.read_text(encoding="utf-8-sig") if path.is_file() else None

    with zipfile.ZipFile(source) as archive:
        for member in archive.namelist():
            # Only ever match on the basename: a crafted zip can contain path traversal.
            if Path(member).name == name and not Path(member).name.startswith("."):
                return archive.read(member).decode("utf-8-sig")
    return None


def read_bikes(source: Path) -> dict[str, dict]:
    """The rider's bike inventory, keyed by the gear name used on activities."""
    text = _read_member(source, BIKES_CSV)
    if not text:
        return {}
    return {
        row["Bike Name"]: {
            "brand": (row.get("Bike Brand") or "").strip(),
            "model": (row.get("Bike Model") or "").strip(),
        }
        for row in csv.DictReader(io.StringIO(text))
        if (row.get("Bike Name") or "").strip()
    }


def read_labels(source: Path) -> pl.DataFrame:
    """One row per ride: when it started, which bike, and whether it was a commute."""
    text = _read_member(source, ACTIVITIES_CSV)
    if text is None:
        raise ValueError(f"{source.name} contains no {ACTIVITIES_CSV}")

    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if "Ride" not in (row.get("Activity Type") or ""):
            continue
        started = _parse_date(row.get("Activity Date", ""))
        if started is None:
            continue
        rows.append(
            {
                "strava_start": started,
                "strava_id": (row.get("Activity ID") or "").strip() or None,
                "gear": (row.get("Activity Gear") or "").strip() or None,
                "commute": _truthy(row.get("Commute", "")),
                "activity_name": (row.get("Activity Name") or "").strip() or None,
            }
        )

    if not rows:
        return pl.DataFrame(schema=_LABEL_SCHEMA)
    return pl.DataFrame(rows, schema_overrides=_LABEL_SCHEMA).sort("strava_start")


_LABEL_SCHEMA = {
    "strava_start": pl.Datetime(time_unit="us", time_zone="UTC"),
    "strava_id": pl.Utf8,
    "gear": pl.Utf8,
    "commute": pl.Boolean,
    "activity_name": pl.Utf8,
}


def attach_labels(
    paths: Paths, labels: pl.DataFrame, tolerance_s: int = MATCH_TOLERANCE_S
) -> tuple[pl.DataFrame, int]:
    """Match labels onto catalog rows by start time and write the catalog back.

    Matching is by timestamp rather than by id because the two services have no shared
    identifier. Strava records the same start instant the device did, so a few minutes of
    tolerance is generous; anything further apart is a different ride.
    """
    catalog = store.load_catalog(paths)
    if not catalog.height or not labels.height:
        return catalog, 0

    added = ("gear", "commute", "strava_id", "activity_name")
    existing = [c for c in added if c in catalog.columns]
    base = catalog.drop(existing) if existing else catalog

    matched = (
        base.sort("start_time")
        .join_asof(
            labels.sort("strava_start"),
            left_on="start_time",
            right_on="strava_start",
            strategy="nearest",
            tolerance=f"{tolerance_s}s",
        )
        .drop("strava_start")
    )
    count = int(matched["gear"].is_not_null().sum()) if "gear" in matched.columns else 0
    matched = matched.sort("start_time", descending=True, nulls_last=True)
    matched.write_parquet(paths.catalog)
    return matched, count
