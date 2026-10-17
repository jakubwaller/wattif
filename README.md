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

Ingest works, rides are corrected for wind and terrain, and `wattif ab` runs within-ride
A/B tests. The ranked what-if table is still to come.

## Install

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp config/profile.example.yaml config/profile.yaml   # then edit it
```

## Use

```sh
wattif sync                      # pull recent rides from Garmin Connect
wattif import ~/Downloads/x.zip  # or import a Garmin/Strava bulk export
wattif labels ~/Downloads/s.zip  # attach bike labels from a Strava export
wattif list                      # see what is stored
wattif analyse --min-km 20       # corrected trend, quarter by quarter
wattif ab --since 2026-09-01 --arms hoods,drops
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

## Testing a change

Whole-ride comparisons are a poor way to judge equipment. The scatter in speed-per-heartbeat
between rides is around 5%, so a 4% change needs roughly twenty-five rides per arm before it
clears the noise - by which time the seasons have moved underneath the experiment.

`wattif ab` puts both arms inside the same ride instead. Ride a few kilometres one way, a
few the other, alternating, and the weather, your fitness and the route are identical on
both sides of the comparison.

Two details make the estimate trustworthy:

- The opening of each block is discarded, because heart rate lags a change in position or
  effort by a minute or more and those samples still belong to the previous arm.
- Each block is compared against the mean of its two neighbours rather than against its
  arm's overall average, which cancels any linear drift across the ride exactly. On a null
  test - the same setup labelled as two arms - the uncorrected difference read -2.4% while
  the drift-corrected one read -0.3%.

Cut blocks by **distance, not time**. Measured on 18 real rides, the per-ride scatter of
the paired difference was 3.4% using 5 km auto-laps against 5.1% using 15-minute blocks and
12.6% using 5-minute ones. So set the watch's auto-lap to a round distance, change setup at
every beep, and change nothing else:

| to resolve | rides needed |
| --- | --- |
| 5% | 4 |
| 3% | 10 |
| 2% | 23 |

Sanity-check the rig before testing anything real: do a few rides with the same setup
throughout, still auto-lapping every 5 km, then run an arm against itself from the first of
those rides. `ab` has no end date, so run it before any real A/B rides join the window:

```sh
wattif ab --since 2026-05-01 --arms null_a,null_b   # date of the first same-setup ride
```

It should report roughly nothing, with an interval spanning zero. If it does not, the
blocks are not comparable and no equipment answer from them will be either.

## Privacy

Local only. No server, no account, no telemetry. Your activity data stays on your machine;
the only thing that leaves it is a coarsened coordinate for a historical weather lookup,
and that is cacheable and optional. Nothing under `data/` is ever committed.

Heart rate is health data. That is deliberate design pressure toward keeping this a local
tool rather than a hosted one.

## Licence

MIT, see [LICENSE](LICENSE).
