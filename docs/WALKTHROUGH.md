# Walkthrough brief

Input for the exploratory walkthrough skill: an agent with a headless browser uses the live site
as a visitor would and reports what gets in the way. Keep the by-design list current, or the
walk re-reports the same intentional behaviour every time.

## Site

- Live: https://wattif.jakubwaller.eu (one static page, `docs/index.html`, served by GitHub Pages from `main:/docs`; this brief is excluded from the build by `docs/_config.yml`, so it is not published).
- Language: English only, no switch.
- What it is: the front page of `wattif`, a local command-line tool that reads a cyclist's Garmin FIT files and
  answers "am I getting fitter?" (wind- and terrain-corrected speed per heartbeat) and "what would make me faster?"
  (within-ride A/B tests), without a power meter. The tool itself runs on the visitor's own machine; the site only
  describes it. No forms, no accounts, no scripts.
- Sections, top to bottom: hero (trend chart, "Source on GitHub", "Install"), the two questions, "How it works"
  (watts chart, `wattif analyse` output), "Test a change inside one ride" (block diagram, ride-count table,
  `wattif ab` output, null test), "Private by design", `#install`, footer.
- Outbound links: the GitHub repo, the README, the author's site www.jakubwaller.eu, GitHub's privacy statement.

## Personas

1. **Garmin rider, phone.** Rides a road bike with a Garmin and a heart-rate strap but no power meter, and arrived
   from a link in a cycling forum. He wants to know whether this tells him if he is actually getting fitter, whether
   he can use it without a power meter, whether it is an app or something he has to install, and where his heart
   rate and Garmin password go. Done when he can say what he would need to do to try it, or why it is not for him.
2. **Developer who rides, desktop.** Wants to test hoods against drops on his commute. He reads how the A/B test works,
   how to set up the watch, how many rides he needs, and the install steps, then follows the README link one hop.
   Done when he has the exact commands for install, import and `wattif ab`, and knows to run the null test first.
3. **Résumé reader, desktop.** A hiring manager who clicked the project from the author's CV and is not a cyclist.
   Wants to understand in a minute what the tool does and how good the reasoning is, then who built it. Done when
   they have reached the author's site from the footer.

## Key flows

- Hero: headline, lead, "Source on GitHub" (one hop), "Install" (jumps to `#install`).
- Read every chart against its caption and the terminal output beside it: the trend chart's percentages against the
  `wattif analyse` table, the A/B output against the ride-count table.
- "Test a change inside one ride": the block diagram, the bullets, the table and the null-test paragraph.
- "Private by design" and the footer's hosting and privacy note.
- `#install`: the commands, "Python 3.11 or newer", the README link.
- Light and dark: load the page with `colorScheme: 'dark'` as well as light.
- `http://` and an unknown path (`/gibtsnicht`).

CLI-only, skip on a web walk: everything the page tells you to run (`git clone`, `pip install`, `wattif sync`,
`import`, `analyse`, `ab`). The walk judges whether the instructions are clear, not whether they work.

## By design — do not report

- **Static, no scripts, no web fonts, no cookies, no analytics, no external requests.** The page follows the system
  light/dark setting; there is no toggle.
- **The charts and terminal output are synthetic.** Every one is captioned "Sample output on synthetic rides"; the
  watts chart is computed from the tool's own model for the example profile. They are not anyone's real rides.
- **The A/B sample resolves faster than the table.** The synthetic rides are less noisy than real ones; the caption
  under the `wattif ab` output says so.
- **Two different ride counts.** "Twenty-five rides per arm" is for whole-ride comparisons and is the argument
  against them; the 4 / 10 / 23 table is for within-ride blocks. Both are stated on the page and in the README.
- **The ranked what-if table does not exist yet.** The card says "The ranked what-if table is still to come."
- **No hosted version, no account, no sign-up.** Heart rate is health data, so the tool stays local; the page says so.
- **`wattif sync` uses Garmin's private endpoints**, the same ones their phone app uses; the page states this and
  names the export ZIP as the fallback.
- **The install block is the short version** and points to the README for the rest ("More in the README").
- **The footer says GitHub Pages receives the visitor's IP**, with a link to GitHub's privacy statement.
- **`http://` redirects (301) to `https://`** (HTTPS is enforced on GitHub Pages).
- **The 404 page is `docs/404.html`**, in the page's own style; GitHub Pages serves it for every unknown path.
- **The install note says only what the repo proves.** CI runs on Linux; the shell lines assume a Unix shell and the page claims nothing about Windows.
- **Terminal output wraps at phone width** instead of scrolling sideways, so long lines break mid-command on a phone.

## Out of bounds

The site is static and has no way to write anywhere itself; the limits are on what its links lead to.

- **Do not run anything** from the page: no clone, no install, no `wattif` command, no Garmin login.
- **GitHub:** open the repo and the README one hop and read them. Do not sign in, star, fork, watch, open an issue or
  pull request, or download a release.
- **Garmin, Strava:** not linked; do not visit their login or export pages.
- **www.jakubwaller.eu:** open the landing page one hop and stop. Do not submit any form there or on any site it links to.
- Keep to a page every few seconds.
- Never use a real name or email address anywhere.
