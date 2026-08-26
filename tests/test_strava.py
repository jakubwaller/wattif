"""Strava export tests.

Fixtures here are synthetic by policy: no real ride, name or gear ever goes into this
repository.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from wattif.config import Paths
from wattif.ingest.strava import _parse_date, attach_labels, read_bikes, read_labels
from wattif.store import upsert_catalog

ACTIVITIES = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Activity Gear,Commute\n"
    "1001,\"Mar 03, 2026, 9:15:00 AM\",Morning,Ride,Test Gravel,1.0\n"
    "1002,\"Mar 04, 2026, 6:00:00 PM\",Evening,Ride,Test Rental,0.0\n"
    "1003,\"Mar 05, 2026, 7:00:00 AM\",Walk,Walk,,0.0\n"
)
BIKES = (
    "Bike Name,Bike Brand,Bike Model,Bike Default Sport Types\n"
    "Test Gravel,Acme,Model One,Gravel Ride\n"
)


@pytest.fixture
def export(tmp_path: Path) -> Path:
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("activities.csv", ACTIVITIES)
        archive.writestr("bikes.csv", BIKES)
        # A traversal attempt and a macOS resource fork, both of which must be ignored.
        archive.writestr("__MACOSX/._activities.csv", "junk")
    return path


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path / "data").ensure()


def test_dates_parse_in_more_than_one_locale():
    assert _parse_date("Mar 03, 2026, 9:15:00 AM") == datetime(
        2026, 3, 3, 9, 15, tzinfo=UTC
    )
    assert _parse_date("2026-03-03 09:15:00") == datetime(2026, 3, 3, 9, 15, tzinfo=UTC)
    assert _parse_date("nonsense") is None


def test_only_rides_are_read(export: Path):
    labels = read_labels(export)
    assert labels.height == 2
    assert labels["gear"].to_list() == ["Test Gravel", "Test Rental"]
    assert labels["commute"].to_list() == [True, False]


def test_bikes_carry_their_brand(export: Path):
    bikes = read_bikes(export)
    assert bikes["Test Gravel"]["brand"] == "Acme"


def test_an_export_without_activities_is_an_error(tmp_path: Path):
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("bikes.csv", BIKES)
    with pytest.raises(ValueError):
        read_labels(empty)


def test_labels_match_rides_by_start_time(export: Path, paths: Paths):
    base = datetime(2026, 3, 3, 9, 15, tzinfo=UTC)
    upsert_catalog(
        paths,
        [
            # Two seconds of clock skew still counts as the same ride.
            {"activity_id": "a", "start_time": base + timedelta(seconds=2), "distance_m": 30_000.0},
            {"activity_id": "b", "start_time": datetime(2026, 3, 4, 18, 0, tzinfo=UTC),
             "distance_m": 8_000.0},
        ],
    )
    catalog, matched = attach_labels(paths, read_labels(export))
    assert matched == 2
    by_id = dict(zip(catalog["activity_id"], catalog["gear"], strict=True))
    assert by_id["a"] == "Test Gravel"
    assert by_id["b"] == "Test Rental"


def test_a_ride_far_from_any_label_stays_unlabelled(export: Path, paths: Paths):
    upsert_catalog(
        paths,
        [{"activity_id": "c", "start_time": datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
          "distance_m": 5_000.0}],
    )
    catalog, matched = attach_labels(paths, read_labels(export))
    assert matched == 0
    assert catalog["gear"].to_list() == [None]


def test_relabelling_replaces_rather_than_duplicates(export: Path, paths: Paths):
    upsert_catalog(
        paths,
        [{"activity_id": "a", "start_time": datetime(2026, 3, 3, 9, 15, tzinfo=UTC),
          "distance_m": 30_000.0}],
    )
    labels = read_labels(export)
    attach_labels(paths, labels)
    catalog, _ = attach_labels(paths, labels)
    assert [c for c in catalog.columns if c.startswith("gear")] == ["gear"]


def test_an_empty_catalog_is_left_alone(export: Path, paths: Paths):
    catalog, matched = attach_labels(paths, read_labels(export))
    assert matched == 0
    assert isinstance(catalog, pl.DataFrame)
