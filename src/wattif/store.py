"""Local storage: one parquet of streams per activity, one catalog row per ride.

Parquet rather than a database because the streams are columnar and mostly read whole,
and because a directory of files is trivially inspectable and deletable. Deleting your
data means deleting a folder.
"""

from __future__ import annotations

import polars as pl

from .config import Paths
from .ingest.fit import Activity

CATALOG_KEY = "activity_id"


def activity_path(paths: Paths, activity_id: str):
    return paths.activities / f"{activity_id}.parquet"


def has_activity(paths: Paths, activity_id: str) -> bool:
    return activity_path(paths, activity_id).is_file()


def write_activity(paths: Paths, activity: Activity) -> None:
    """Write one activity's streams. Overwrites, so re-importing is safe."""
    activity.records.write_parquet(activity_path(paths, activity.activity_id))


def load_catalog(paths: Paths) -> pl.DataFrame:
    if not paths.catalog.is_file():
        return pl.DataFrame()
    return pl.read_parquet(paths.catalog)


def upsert_catalog(paths: Paths, summaries: list[dict]) -> pl.DataFrame:
    """Merge summary rows into the catalog, newest write winning per activity."""
    if not summaries:
        return load_catalog(paths)

    incoming = pl.DataFrame(summaries, infer_schema_length=None)
    existing = load_catalog(paths)
    if existing.height:
        keep = existing.filter(~pl.col(CATALOG_KEY).is_in(incoming[CATALOG_KEY].to_list()))
        combined = pl.concat([keep, incoming], how="diagonal_relaxed")
    else:
        combined = incoming

    if "start_time" in combined.columns:
        combined = combined.sort("start_time", descending=True, nulls_last=True)
    combined.write_parquet(paths.catalog)
    return combined
