# MCG Seller Activity Report System

Automated weekly / monthly / quarterly activity reports for every active
Mason Capital Group listing: an interactive report page, a print-ready PDF,
and a Constant Contact email flyer, published to GitHub Pages
(`torabic/mcg-listings`) and (once Constant Contact credentials exist)
drafted as an email campaign for Cameron to review and send himself.

Full data contract and design spec: see `SPEC.md` in this repo (or the
parent build spec it was generated from). This README covers how to
actually run the system.

## How it fits together

```
config/listings.json      canonical listing registry (address, price, seller
                           contact, report_token, which sources apply)
config/sources.json        per-source credential slots + enabled flags
config/market-nwa.json     NWA county DOM / pricing-tier reference data

intake/<slug>/             manual drop folder for homes.com / Crexi /
                           LoopNet numbers (no API for these -- see
                           intake/README.md)

bin/collect.py             sources -> data/<slug>/<period_id>/metrics.json
bin/generate.py            metrics.json -> report page + PDF + CC flyer HTML
bin/deploy.py               copies rendered pages into a torabic/mcg-listings
                           clone under reports/, commits, pushes
bin/cc_flyers.py           metrics/flyer HTML -> Constant Contact DRAFT
                           campaign (never sends)

templates/report.html      the interactive seller report page (Jinja2)
templates/flyer.html       the email-safe CC flyer (Jinja2)

data/, out/                generated artifacts (gitignored in this tree;
                           what bin/deploy.py publishes lives in the
                           separate mcg-listings repo, under reports/)
```

Every step is idempotent and safe to re-run: missing/uncredentialed sources
degrade to zeros with `data_quality: "missing"` rather than failing, and
`bin/deploy.py` no-ops (no commit, no push) when nothing under `reports/`
actually changed.

## One-command run sequence

From a fresh clone of this tree, for a given period (`weekly`, `monthly`,
or `quarterly`):

```bash
pip install jinja2 requests --break-system-packages   # only real dependency

# 1. Collect -- writes data/<slug>/<period_id>/metrics.json for every
#    active listing in config/listings.json.
python3 bin/collect.py --period weekly [--sample]

# 2. Generate -- renders the report page + PDF + CC flyer for each listing,
#    reading the period_id collect.py just computed/used.
python3 bin/generate.py --period-id <period_id printed by step 1>

# 3. Deploy -- publishes the rendered pages to GitHub Pages.
export GITHUB_TOKEN=<token>            # or --token-file /path/to/token
python3 bin/deploy.py --period-id <period_id> --repo-dir /path/to/mcg-listings-clone

# 4. (Optional, once Constant Contact creds exist) draft the email flyers.
python3 bin/cc_flyers.py --period-id <period_id>
```

`--period` accepts an explicit `--period-id` too (e.g. `--period-id
2026-W29`) if you need to (re)generate a specific past/future period rather
than "the period ending yesterday", which is the default when `--period-id`
is omitted.

Run `--slug all` (the default) for every active listing, or `--slug
<slug>` to run a single one -- useful when iterating on one listing's
report without regenerating all seven.

## Sample vs. live mode

- **Sample mode** (`bin/collect.py ... --sample`): every source is filled
  with deterministic, seeded demo numbers (seeded on `slug + period_id`, so
  re-running with `--sample` reproduces the same numbers rather than
  randomizing every time). Every affected section of the report is badged
  "Sample data". Use this for demos, dry runs, and any period where a
  source isn't wired up yet.
- **Live mode** (no `--sample`): each source adapter (IDX Broker, Constant
  Contact, GA4, tawk.to) attempts a real API call using whatever credential
  is configured in `config/sources.json`. Any source that's disabled,
  uncredentialed, or that errors out degrades to zeros with
  `data_quality: "missing"` for that section -- it never crashes the run.
  Portal numbers (homes.com / Crexi / LoopNet) are always "live" when a
  matching file exists under `intake/<slug>/`, live mode or not, since
  there's no portal API to call either way.

You can mix and match at the listing level too: `sources.<listing>.idx` /
`.cc` / `.ga4` / `.tawk` / `.portals` in `config/listings.json` controls
which sources are even attempted for that listing.

## Config explanation

- **`config/listings.json`** -- one entry per listing: `slug` (used in
  every file path and the published URL), `address`, `type`, `price`,
  `county`, `seller.email` (drafted CC campaigns are addressed here),
  `report_token` (an 8-hex-char unguessable suffix so seller report URLs
  aren't sequentially guessable), and `sources` (which adapters apply to
  this listing, and which portal(s) to expect intake files for).
- **`config/sources.json`** -- one block per source
  (`idx_broker` / `constant_contact` / `ga4` / `tawk` / `portals`), each
  with `enabled` (flip to `true` once credentials exist) and
  `credential_env_or_path` (an environment variable name to check first,
  then a file path). See **`docs/credentials-needed.md`** for exactly what
  credential each source needs and where to get it -- that's the
  activation checklist to hand to Cameron.
- **`config/market-nwa.json`** -- county-level median days-on-market and
  pricing-tier reference numbers used for the market-positioning section
  of each report; update this periodically as NWA market conditions shift.

## Credential activation

Every live source starts disabled. To turn one on, follow
**`docs/credentials-needed.md`** step by step for that source, then flip
its `enabled` flag in `config/sources.json` to `true`. Nothing needs to
change in `bin/collect.py` itself -- once the credential resolves, the
next `bin/collect.py` run (without `--sample`) will report `data_quality:
"live"` for that section instead of `"missing"`.

## Publishing / GitHub Pages convention

`torabic/mcg-listings` serves GitHub Pages from the root of `main` (no
`docs/` dir, no custom domain -- `https://torabic.github.io/mcg-listings/`),
matching the existing `presentations/<slug>/` and
`listing-presentations/<slug>/` marketing pages already in that repo.
`bin/deploy.py` follows the same convention and only ever touches the
`reports/` prefix:

```
reports/<slug>-<report_token>/<period_id>/index.html   -- this period's report
reports/<slug>-<report_token>/latest/index.html         -- always the newest period
```

## Constant Contact flyers

`bin/cc_flyers.py` creates one **draft** email campaign per listing per
period from `out/flyers/<slug>-<period_id>.html`, addressed to that
listing's `seller.email` (or a `constant_contact.listing_segments.<slug>`
override in `config/sources.json` if you'd rather target a CC list/segment
instead of a single contact). It never schedules or sends -- the campaign
is left in DRAFT status in Constant Contact for Cameron to review. If
Constant Contact credentials aren't configured yet, it prints `flyer HTML
ready at <path>; CC draft skipped (no credentials)` for each listing and
exits `0` -- the flyer HTML itself is always generated by `bin/generate.py`
regardless of whether CC is wired up.
