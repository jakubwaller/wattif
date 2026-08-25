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

Early. Nothing works yet.

## Privacy

Local only. No server, no account, no telemetry. Your activity data stays on your machine;
the only thing that leaves it is a coarsened coordinate for a historical weather lookup,
and that is cacheable and optional. Nothing under `data/` is ever committed.

Heart rate is health data. That is deliberate design pressure toward keeping this a local
tool rather than a hosted one.

## Licence

MIT, see [LICENSE](LICENSE).
