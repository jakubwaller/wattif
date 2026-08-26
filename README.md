# wattif

Ask what-if of your own ride data.

`wattif` reads the FIT files from your Garmin account and answers two questions that most
cycling tools skip, both without needing a power meter.

**What would actually make me faster, and by how much?** Tyres, riding position, a
different bike, fewer stops, more fitness. Ranked, in km/h, estimated against your own
rides rather than a generic calculator.

**Am I getting fitter?** Not raw average speed, which wind and terrain drown out, but
speed per heartbeat, corrected for wind, gradient and equipment. So a fast day into a
tailwind stops looking like progress, and a hard slog into a headwind stops looking like
decline.

Both answers come out of the same physics: estimate power from speed, gradient and air
density, correct for the wind you actually rode through, then re-run the ride with one
variable changed.

## Status

Early. Ingest works; the analysis does not exist yet.

## Install

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config/profile.example.yaml config/profile.yaml   # then edit it
```

## Use

```sh
wattif sync                      # pull recent rides from Garmin Connect
wattif import ~/Downloads/x.zip  # or import a Garmin/Strava bulk export
wattif list                      # see what is stored
```

`sync` is the convenient path and `import` is the durable one. Garmin has no personal API,
so `sync` speaks to the same private endpoints their phone app uses. That is a grey area
under Garmin's terms and it has broken before: `garth`, the library the whole ecosystem
depended on, stopped working in March 2026 when Garmin changed the login flow. Sources are
kept behind one small interface so that when it happens again, the official export still
works and nothing downstream cares.

Your Garmin password is used once to obtain tokens and is never written to disk. Tokens go
to `~/.wattif/garmin`, owner-readable only.

Data lands in `./data` by default, or wherever `WATTIF_HOME` points. That directory is
gitignored, and deleting your data means deleting the folder.

## Privacy

Local only. No server, no account, no telemetry. Your activity data stays on your machine;
the only thing that leaves it is a coarsened coordinate for a historical weather lookup,
and that is cacheable and optional. Nothing under `data/` is ever committed.

Heart rate is health data. That is deliberate design pressure toward keeping this a local
tool rather than a hosted one.

## Licence

MIT, see [LICENSE](LICENSE).
