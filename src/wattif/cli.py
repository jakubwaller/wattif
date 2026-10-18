"""Command line entry point."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import typer

from .config import Paths, Profile
from .ingest.export_zip import ExportArchive
from .ingest.fit import find_fit_files, read_fit
from .metrics.ride import analyse as analyse_ride
from .store import (
    has_activity,
    indexed_sources,
    load_catalog,
    load_laps,
    upsert_catalog,
    write_activity,
    write_laps,
)
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
        write_laps(paths, activity)
        summaries.append(activity.summary)

    upsert_catalog(paths, summaries)
    if skipped:
        typer.echo(f"  {skipped} file(s) could not be decoded")
    return len(summaries)


def _mass_lookup(paths: Paths, profile: Profile, announce: bool = False):
    """A function from ride date to total moving mass, or None if no weigh-ins are logged.

    The profile's rider_kg is "you as you ride" and already includes kit, while the weigh-in
    log is bare body mass. The difference is held constant and applied to every past date.
    """
    from datetime import date as _date

    from .ingest import weight as weight_log

    weights = weight_log.WeightSeries(weight_log.load(paths))
    if not weights:
        return None
    kit_kg = profile.rider_kg - weights.at(_date.today(), profile.rider_kg)
    if announce:
        typer.echo(f"Using logged body mass per ride (+{kit_kg:.1f} kg kit).")
    return lambda when: weights.at(when, profile.rider_kg) + kit_kg + profile.bike_kg


def _parse_duration(text: str) -> float:
    """Accept 300, 300s, 5m or 1h as a number of seconds."""
    value = text.strip().lower()
    scale = {"s": 1, "m": 60, "h": 3600}
    if value and value[-1] in scale:
        return float(value[:-1]) * scale[value[-1]]
    return float(value)


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

    archive = None if no_weather else WeatherArchive(paths.root / "weather")
    mass_for = _mass_lookup(paths, profile, announce=True)
    rows = []
    considered = catalog.filter(pl.col("distance_m") >= min_km * 1000)
    typer.echo(f"Analysing {considered.height} ride(s)...")

    for i, row in enumerate(considered.iter_rows(named=True), 1):
        path = paths.activities / f"{row['activity_id']}.parquet"
        if not path.is_file():
            continue
        ride_mass = mass_for(row["start_time"].date()) if mass_for else None
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


@app.command()
def labels(
    path: Path = typer.Argument(..., help="A Strava bulk-export .zip, or its unpacked directory."),
    tolerance: int = typer.Option(180, help="Seconds of slack when matching rides by start time."),
) -> None:
    """Attach Strava's bike and commute labels to rides already in the store.

    Strava's export carries something no device file does: which bike you were on. An
    equipment comparison across rides is worthless without it, since a rental bike or a
    second frame lands in the same average as the bike you are asking about.
    """
    import polars as pl

    from .ingest.strava import attach_labels, read_bikes, read_labels

    paths = Paths.default()
    frame = read_labels(path)
    typer.echo(f"{frame.height} ride(s) in the export.")

    catalog, matched = attach_labels(paths, frame, tolerance_s=tolerance)
    typer.echo(f"{matched} of {catalog.height} stored ride(s) matched a label.\n")

    bikes = read_bikes(path)
    if "gear" in catalog.columns:
        counts = (
            catalog.group_by("gear")
            .agg(n=pl.len(), km=pl.col("distance_m").sum())
            .sort("n", descending=True)
        )
        typer.echo("gear                     n       km")
        for row in counts.iter_rows(named=True):
            name = row["gear"] or "(unlabelled)"
            bike = bikes.get(name or "", {})
            detail = " ".join(x for x in (bike.get("brand"), bike.get("model")) if x)
            typer.echo(f"  {name[:20]:<20} {row['n']:>5}  {(row['km'] or 0)/1000:>7.0f}"
                       f"  {detail}")


@app.command()
def ab(
    activities: list[str] = typer.Argument(None, help="Activity ids. Defaults to --since."),
    arms: str = typer.Option("A,B", help="Comma-separated names for the two arms."),
    split: str = typer.Option("auto", help="Block source: auto, manual, laps or time."),
    every: str = typer.Option("5m", help="Block length when --split time."),
    warmup: str = typer.Option("90s", help="Discarded at the start of each block."),
    pattern: str = typer.Option("AB", help="Arm order: AB, or BA to start on the second arm."),
    since: str = typer.Option(None, help="Use every ride on or after this date (YYYY-MM-DD)."),
    no_weather: bool = typer.Option(False, "--no-weather", help="Skip the wind correction."),
) -> None:
    """Compare two setups tested within the same rides.

    Ride alternating blocks - one arm, then the other - and this measures the difference
    between them. Because both arms sit inside one ride, the weather, your fitness and the
    route are identical on both sides, which is what makes a handful of rides enough where
    comparing whole rides would need dozens.

    Blocks cut at a fixed *distance* are markedly quieter than blocks cut at a fixed time,
    so the watch's own auto-lap is the best block source there is: set it to a round
    distance, switch setups at every beep, and change nothing else.
    """
    import polars as pl

    from .metrics.blocks import (
        analyse_blocks,
        assign_arms,
        compare,
        rides_needed,
        split_by_laps,
        split_by_time,
    )

    names = tuple(x.strip() for x in arms.split(",") if x.strip())
    if len(names) != 2:
        typer.echo("Need exactly two arm names, e.g. --arms hoods,drops")
        raise typer.Exit(code=1)
    try:
        assign_arms(2, names, pattern)
    except ValueError as exc:
        typer.echo(f"--pattern {pattern}: {exc}.")
        raise typer.Exit(code=1) from None

    paths = Paths.default()
    profile = Profile.load()
    catalog = load_catalog(paths)
    if not catalog.height:
        typer.echo("Nothing stored yet.")
        raise typer.Exit(code=1)

    wanted = catalog
    if activities:
        wanted = catalog.filter(pl.col("activity_id").is_in(list(activities)))
    elif since:
        from datetime import datetime

        cutoff = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        wanted = catalog.filter(pl.col("start_time") >= cutoff)
    else:
        typer.echo("Give activity ids or --since YYYY-MM-DD.")
        raise typer.Exit(code=1)

    if not wanted.height:
        typer.echo("No matching rides.")
        raise typer.Exit(code=1)

    archive = None if no_weather else WeatherArchive(paths.root / "weather")
    mass_for = _mass_lookup(paths, profile)
    warmup_s = _parse_duration(warmup)
    every_s = _parse_duration(every)

    all_blocks = []
    for row in wanted.sort("start_time").iter_rows(named=True):
        activity_id = row["activity_id"]
        path = paths.activities / f"{activity_id}.parquet"
        if not path.is_file():
            continue
        records = pl.read_parquet(path)

        if split == "time":
            raw_blocks = split_by_time(records, every_s)
        else:
            laps = load_laps(paths, activity_id)
            if split == "auto":
                # Prefer the laps the rider pressed for; fall back to the watch's own.
                raw_blocks = split_by_laps(records, laps, ("manual",)) or split_by_laps(
                    records, laps, None
                )
            else:
                raw_blocks = split_by_laps(
                    records, laps, ("manual",) if split == "manual" else None
                )

        if len(raw_blocks) < 3:
            continue

        blocks = analyse_blocks(
            activity_id, raw_blocks, names, profile, archive,
            mass_kg=mass_for(row["start_time"].date()) if mass_for else None,
            warmup_s=warmup_s, pattern=pattern,
        )
        if blocks:
            all_blocks.extend(blocks)
            typer.echo(
                f"  {str(row['start_time'])[:16]}  {len(blocks)} block(s)"
                f"  {(row.get('distance_m') or 0)/1000:.1f} km"
            )

    if not all_blocks:
        hint = (
            "No usable blocks. With --split manual you must press the lap button at each "
            "change; try --split laps to use the watch's auto-laps, or --split time --every 5m."
        )
        typer.echo(hint)
        raise typer.Exit(code=1)

    result = compare(all_blocks, names)
    typer.echo(
        f"\n{result.n_blocks} block(s) over {result.n_rides} ride(s), "
        f"{result.n_pairs} drift-corrected pair(s).\n"
    )
    typer.echo("arm            equiv km/h     hr   efficiency")
    for arm in names:
        typer.echo(
            f"  {arm[:12]:<12} {result.mean_equivalent_kmh[arm]:>9.2f}"
            f"  {result.mean_hr[arm]:>5.0f}     {result.mean_efficiency[arm]:.4f}"
        )

    if result.n_pairs == 0:
        typer.echo("\nToo few consecutive blocks to pair. Ride more alternating blocks.")
        raise typer.Exit()

    low, high = result.ci_pct
    typer.echo(
        f"\n{names[1]} vs {names[0]}: {result.delta_pct:+.2f}%"
        + (f"  (95% CI {low:+.2f}% to {high:+.2f}%)" if low == low else "  (CI needs 2+ rides)")
    )
    typer.echo(f"uncorrected difference: {result.naive_delta_pct:+.2f}%")

    if result.n_rides > 1:
        deltas = result.ride_deltas
        mean = sum(deltas) / len(deltas)
        spread = (sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)) ** 0.5
        baseline = result.mean_efficiency[names[0]]
        cv = abs(spread / baseline) * 100 if baseline else 0.0
        typer.echo(f"\nper-ride scatter of the paired difference: {cv:.2f}%")
        for effect in (2.0, 3.0, 5.0):
            typer.echo(f"  to resolve {effect:.0f}%: {rides_needed(cv, effect)} ride(s)")

    if result.significant:
        typer.echo("\nThe difference is significant at 95%.")
    else:
        typer.echo("\nNot significant yet: the interval still spans zero.")


if __name__ == "__main__":
    app()
