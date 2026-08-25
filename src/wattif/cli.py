"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Paths, Profile
from .ingest.export_zip import ExportArchive
from .ingest.fit import find_fit_files, read_fit
from .metrics.ride import analyse as analyse_ride
from .store import has_activity, indexed_sources, load_catalog, upsert_catalog, write_activity
from .weather.open_meteo import WeatherArchive

app = typer.Typer(help="Ask what-if of your own ride data.", no_args_is_help=True)


def _index(paths: Paths, files: list[Path], reindex: bool = False) -> int:
    """Decode FIT files into the store. Returns how many activities were added."""
    summaries: list[dict] = []
    skipped = 0
    already = set() if reindex else indexed_sources(paths)

    for path in files:
        if path.name in already:
            continue
        try:
            activity = read_fit(path)
        except ValueError as exc:
            typer.echo(f"  skipped {path.name}: {exc}")
            skipped += 1
            continue

        if has_activity(paths, activity.activity_id) and not reindex:
            continue

        write_activity(paths, activity)
        summaries.append(activity.summary)

    upsert_catalog(paths, summaries)
    if skipped:
        typer.echo(f"  {skipped} file(s) could not be decoded")
    return len(summaries)


@app.command()
def sync(
    limit: int = typer.Option(50, help="How many recent activities to look at. 0 means all."),
    all_sports: bool = typer.Option(False, "--all-sports", help="Not just rides."),
) -> None:
    """Pull recent activities from Garmin Connect.

    Uses Garmin's private endpoints, which they tolerate rather than support. If this stops
    working, use `wattif import` with an official export instead.
    """
    from .ingest.garmin_sync import GarminConnect

    paths = Paths.default()
    source = GarminConnect(cycling_only=not all_sports)
    typer.echo("Downloading from Garmin Connect...")
    files = source.fetch(paths.raw, limit=limit or None)
    typer.echo(f"{len(files)} new file(s) downloaded.")
    added = _index(paths, files or find_fit_files(paths.raw))
    typer.echo(f"{added} activity(s) added to the store.")

    from datetime import date

    from .ingest import weight as weight_log

    weigh_ins = weight_log.fetch_weigh_ins(source.client, date(2000, 1, 1), date.today())
    weight_log.save(paths, weigh_ins)
    typer.echo(f"{weigh_ins.height} weigh-in(s) recorded.")


@app.command("import")
def import_export(
    path: Path = typer.Argument(..., help="A bulk-export .zip, or a directory of FIT files."),
    reindex: bool = typer.Option(False, "--reindex", help="Re-decode activities already stored."),
) -> None:
    """Import a Garmin or Strava bulk export. Needs no credentials and never stops working."""
    paths = Paths.default()
    source = ExportArchive(path)
    files = source.fetch(paths.raw)
    typer.echo(f"{len(files)} new file(s) unpacked.")
    added = _index(paths, files or find_fit_files(paths.raw), reindex=reindex)
    typer.echo(f"{added} activity(s) added to the store.")


@app.command("list")
def list_activities(limit: int = typer.Option(20, help="How many to show.")) -> None:
    """Show what is in the store."""
    catalog = load_catalog(Paths.default())
    if not catalog.height:
        typer.echo("Nothing stored yet. Run `wattif sync` or `wattif import <export.zip>`.")
        raise typer.Exit()

    typer.echo(f"{catalog.height} activity(s) stored.\n")
    for row in catalog.head(limit).iter_rows(named=True):
        distance = (row.get("distance_m") or 0) / 1000
        moving = (row.get("moving_s") or 0) / 3600
        speed = distance / moving if moving else 0
        start = str(row.get("start_time") or "")[:16]
        hr = row.get("avg_hr")
        typer.echo(
            f"  {start}  {distance:6.1f} km  {speed:5.1f} km/h"
            f"  {f'{hr:>3} bpm' if hr else '      -'}  {row.get('sport') or ''}"
        )


@app.command()
def analyse(
    min_km: float = typer.Option(0.0, help="Ignore rides shorter than this."),
    no_weather: bool = typer.Option(False, "--no-weather", help="Skip the wind correction."),
) -> None:
    """Correct every stored ride for wind and terrain, then show the trend.

    Raw average speed conflates the rider with the weather. `equivalent` is the speed the
    same effort would have produced on flat ground in still air, which is what makes two
    rides on different days comparable.
    """
    import polars as pl

    paths = Paths.default()
    profile = Profile.load()
    catalog = load_catalog(paths)
    if not catalog.height:
        typer.echo("Nothing stored yet.")
        raise typer.Exit()

    from .ingest import weight as weight_log

    archive = None if no_weather else WeatherArchive(paths.root / "weather")
    weights = weight_log.WeightSeries(weight_log.load(paths))
    # The profile's rider_kg is "you as you ride", so it already carries kit. The weigh-in
    # log is bare body mass; keep the difference and apply it to every historical date.
    kit_kg = 0.0
    if weights:
        from datetime import date as _date

        kit_kg = profile.rider_kg - weights.at(_date.today(), profile.rider_kg)
        typer.echo(f"Using logged body mass per ride (+{kit_kg:.1f} kg kit).")
    rows = []
    considered = catalog.filter(pl.col("distance_m") >= min_km * 1000)
    typer.echo(f"Analysing {considered.height} ride(s)...")

    for i, row in enumerate(considered.iter_rows(named=True), 1):
        path = paths.activities / f"{row['activity_id']}.parquet"
        if not path.is_file():
            continue
        ride_mass = None
        if weights:
            ride_mass = (
                weights.at(row["start_time"].date(), profile.rider_kg)
                + kit_kg
                + profile.bike_kg
            )
        metrics = analyse_ride(
            row["activity_id"], pl.read_parquet(path), profile, archive, mass_kg=ride_mass
        )
        if metrics is None:
            continue
        rows.append({
            "activity_id": metrics.activity_id,
            "start_time": row["start_time"],
            "km": metrics.distance_m / 1000,
            "raw_kmh": metrics.raw_kmh,
            "equivalent_kmh": metrics.equivalent_kmh,
            "power_w": metrics.mean_power_w,
            "hr": metrics.mean_hr,
            "headwind_ms": metrics.mean_headwind_ms,
            "ascent_m": metrics.ascent_m,
            "efficiency": metrics.efficiency,
            "weather": metrics.weather,
        })
        if i % 50 == 0:
            typer.echo(f"  {i}/{considered.height}")

    if not rows:
        typer.echo("No ride had enough data to analyse.")
        raise typer.Exit()

    frame = pl.DataFrame(rows).sort("start_time")
    frame.write_parquet(paths.root / "metrics.parquet")
    covered = int(frame["weather"].sum())
    typer.echo(f"\n{frame.height} ride(s) analysed, {covered} with weather.\n")

    quarterly = (
        frame.with_columns(
            quarter=pl.col("start_time").dt.year().cast(str)
            + "Q"
            + pl.col("start_time").dt.quarter().cast(str)
        )
        .group_by("quarter")
        .agg(
            n=pl.len(),
            raw=pl.col("raw_kmh").mean(),
            equivalent=pl.col("equivalent_kmh").mean(),
            hr=pl.col("hr").mean(),
            headwind=pl.col("headwind_ms").mean(),
            efficiency=pl.col("efficiency").mean(),
        )
        .sort("quarter")
    )
    typer.echo("quarter   n    raw   equiv     hr   headwind   efficiency")
    for r in quarterly.iter_rows(named=True):
        typer.echo(
            f"{r['quarter']}  {r['n']:>3}  {r['raw']:5.1f}  {r['equivalent']:6.1f}"
            f"  {r['hr']:5.0f}  {r['headwind']:+7.2f}     {r['efficiency']:.4f}"
        )


if __name__ == "__main__":
    app()
