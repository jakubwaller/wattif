"""Ingest tests.

Fixtures here are synthetic by policy: no real ride ever goes into this repository.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import polars as pl
import pytest

from wattif.config import Paths
from wattif.ingest.export_zip import ExportArchive
from wattif.ingest.fit import SEMICIRCLE_TO_DEG, _activity_id, _pick
from wattif.ingest.garmin_sync import _fit_bytes
from wattif.store import load_catalog, upsert_catalog


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
