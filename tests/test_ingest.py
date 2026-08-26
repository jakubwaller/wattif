"""Ingest tests.

Fixtures here are synthetic by policy: no real ride ever goes into this repository.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from wattif.config import Paths
from wattif.ingest.export_zip import ExportArchive
from wattif.ingest.fit import (
    SEMICIRCLE_TO_DEG,
    _activity_id,
    _assign_laps,
    _lap_table,
    _pick,
)
from wattif.ingest.garmin_sync import _fit_bytes
from wattif.store import indexed_sources, load_catalog, upsert_catalog


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path).ensure()


def test_semicircles_convert_to_degrees():
    # A quarter turn of the semicircle range is 90 degrees.
    assert (2**31 // 2) * SEMICIRCLE_TO_DEG == pytest.approx(90.0)


def test_pick_prefers_enhanced_field():
    record = {"enhanced_speed": 8.5, "speed": 8.4}
    assert _pick(record, ("enhanced_speed", "speed")) == 8.5


def test_pick_falls_back_when_enhanced_absent():
    assert _pick({"speed": 8.4}, ("enhanced_speed", "speed")) == 8.4


def test_pick_returns_none_when_nothing_present():
    assert _pick({}, ("enhanced_speed", "speed")) is None


def test_activity_id_falls_back_to_filename():
    assert _activity_id({}, Path("garmin-12345.fit.gz")) == "garmin-12345"


def test_fit_bytes_unwraps_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activity.fit", b"\x0e\x10\x00\x00\x00\x00\x00\x00.FITpayload")
    assert _fit_bytes(buffer.getvalue()) == b"\x0e\x10\x00\x00\x00\x00\x00\x00.FITpayload"


def test_fit_bytes_accepts_bare_fit():
    bare = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FITrest"
    assert _fit_bytes(bare) == bare


def test_fit_bytes_rejects_other_payloads():
    assert _fit_bytes(b"<html>not a fit file</html>") is None


def test_catalog_upsert_replaces_by_activity_id(paths: Paths):
    upsert_catalog(paths, [{"activity_id": "A", "start_time": "2026-01-01", "avg_hr": 150}])
    upsert_catalog(paths, [{"activity_id": "A", "start_time": "2026-01-01", "avg_hr": 142}])
    catalog = load_catalog(paths)
    assert catalog.height == 1
    assert catalog["avg_hr"][0] == 142


def test_catalog_keeps_distinct_activities(paths: Paths):
    upsert_catalog(paths, [{"activity_id": "A", "start_time": "2026-01-01"}])
    upsert_catalog(paths, [{"activity_id": "B", "start_time": "2026-01-02"}])
    assert set(load_catalog(paths)["activity_id"]) == {"A", "B"}


def test_catalog_sorts_newest_first(paths: Paths):
    upsert_catalog(
        paths,
        [
            {"activity_id": "old", "start_time": "2026-01-01"},
            {"activity_id": "new", "start_time": "2026-06-01"},
        ],
    )
    assert load_catalog(paths)["activity_id"][0] == "new"


def test_empty_catalog_is_empty_frame(paths: Paths):
    assert isinstance(load_catalog(paths), pl.DataFrame)
    assert load_catalog(paths).height == 0


def test_export_archive_extracts_only_fit_files(tmp_path: Path):
    archive_path = tmp_path / "export.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("activities/1.fit", b"one")
        archive.writestr("activities/2.fit.gz", b"two")
        archive.writestr("activities.csv", b"not a ride file")
        archive.writestr("__MACOSX/._3.fit", b"junk")

    dest = tmp_path / "raw"
    written = ExportArchive(archive_path).fetch(dest)
    assert {p.name for p in written} == {"1.fit", "2.fit.gz"}


def test_export_archive_skips_already_present(tmp_path: Path):
    archive_path = tmp_path / "export.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("1.fit", b"one")

    dest = tmp_path / "raw"
    source = ExportArchive(archive_path)
    assert len(source.fetch(dest)) == 1
    assert source.fetch(dest) == []


def test_export_archive_reads_a_directory(tmp_path: Path):
    src = tmp_path / "unpacked"
    src.mkdir()
    (src / "ride.fit").write_bytes(b"one")
    (src / "notes.txt").write_bytes(b"ignore me")

    written = ExportArchive(src).fetch(tmp_path / "raw")
    assert [p.name for p in written] == ["ride.fit"]


def test_missing_export_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ExportArchive(tmp_path / "nope.zip")


def test_activity_id_uses_device_time_created():
    # The id must come from the recording device, so the same ride from two sources
    # collapses to one entry. A filename-derived id would not.
    messages = {"file_id_mesgs": [{"time_created": datetime(2026, 8, 25, 14, 30, 5)}]}
    assert _activity_id(messages, Path("garmin-999.fit")) == "20260825T143005Z"


def test_same_ride_from_two_sources_shares_an_id():
    created = datetime(2026, 8, 25, 14, 30, 5)
    messages = {"file_id_mesgs": [{"time_created": created}]}
    from_garmin = _activity_id(messages, Path("garmin-999.fit"))
    from_strava = _activity_id(messages, Path("12345678.fit.gz"))
    assert from_garmin == from_strava


def test_indexed_sources_reports_known_filenames(paths: Paths):
    upsert_catalog(paths, [{"activity_id": "A", "source_file": "ride-1.fit"}])
    assert indexed_sources(paths) == {"ride-1.fit"}


def test_indexed_sources_empty_when_no_catalog(paths: Paths):
    assert indexed_sources(paths) == set()


def test_weight_series_interpolates_between_weigh_ins():
    from datetime import date as _date

    from wattif.ingest.weight import WeightSeries

    series = WeightSeries(
        pl.DataFrame({"date": [_date(2024, 1, 1), _date(2024, 1, 11)], "kg": [80.0, 90.0]})
    )
    assert series.at(_date(2024, 1, 6), 0.0) == pytest.approx(85.0)


def test_weight_series_clamps_outside_the_log():
    from datetime import date as _date

    from wattif.ingest.weight import WeightSeries

    series = WeightSeries(
        pl.DataFrame({"date": [_date(2024, 1, 1), _date(2024, 1, 11)], "kg": [80.0, 90.0]})
    )
    assert series.at(_date(2020, 1, 1), 0.0) == 80.0
    assert series.at(_date(2030, 1, 1), 0.0) == 90.0


def test_weight_series_falls_back_when_empty():
    from datetime import date as _date

    from wattif.ingest.weight import WeightSeries

    assert WeightSeries(pl.DataFrame()).at(_date(2024, 1, 1), 77.0) == 77.0


def test_laps_are_read_with_their_trigger():
    start = datetime(2026, 9, 1, 8, 0)
    laps = _lap_table(
        {
            "lap_mesgs": [
                {"start_time": start, "total_elapsed_time": 300.0,
                 "total_distance": 2000.0, "lap_trigger": "manual"},
                {"start_time": start.replace(minute=5), "total_elapsed_time": 300.0,
                 "total_distance": 2000.0, "lap_trigger": "distance"},
            ]
        }
    )
    assert laps["lap"].to_list() == [0, 1]
    assert laps["trigger"].to_list() == ["manual", "distance"]


def test_a_file_without_laps_gives_an_empty_table():
    laps = _lap_table({})
    assert laps.height == 0
    assert "trigger" in laps.columns


def test_records_are_tagged_with_the_lap_they_fall_in():
    start = datetime(2026, 9, 1, 8, 0)
    frame = pl.DataFrame(
        {"timestamp": [start.replace(minute=m) for m in (0, 3, 6, 9)], "speed_ms": [7.0] * 4}
    )
    laps = _lap_table(
        {
            "lap_mesgs": [
                {"start_time": start, "lap_trigger": "manual"},
                {"start_time": start.replace(minute=5), "lap_trigger": "manual"},
            ]
        }
    )
    tagged = _assign_laps(frame, laps)
    assert tagged["lap"].to_list() == [0, 0, 1, 1]


def test_records_survive_a_file_with_no_laps():
    frame = pl.DataFrame({"timestamp": [datetime(2026, 9, 1, 8, 0)], "speed_ms": [7.0]})
    tagged = _assign_laps(frame, _lap_table({}))
    assert tagged["lap"].to_list() == [None]


def test_reindexing_keeps_annotations_the_files_do_not_carry(paths: Paths):
    upsert_catalog(paths, [{"activity_id": "a", "distance_m": 30_000.0}])
    # A label attached later, of the sort only the rider or another service knows.
    catalog = load_catalog(paths).with_columns(gear=pl.lit("Test Gravel"))
    catalog.write_parquet(paths.catalog)

    # Re-decoding the same ride must not wipe it.
    merged = upsert_catalog(paths, [{"activity_id": "a", "distance_m": 30_000.0}])
    assert merged["gear"].to_list() == ["Test Gravel"]
