"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import Paths
from .ingest.export_zip import ExportArchive
from .ingest.fit import find_fit_files, read_fit
from .store import has_activity, indexed_sources, load_catalog, upsert_catalog, write_activity

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


if __name__ == "__main__":
    app()
