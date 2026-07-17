# Portal Intake — for Cameron

homes.com, Crexi, and LoopNet do not give us API access. Until that changes
(or an OCR step is built for portal screenshots), drop the numbers here by
hand each reporting period and `collect.py` will pick them up automatically.

## Where files go

```
intake/<listing-slug>/<period-type>-<period-id>.json
intake/<listing-slug>/<period-type>-<period-id>.csv
```

- `<listing-slug>` is the `slug` field from `config/listings.json` (e.g.
  `1715-n-garland`, `3300-blue-hill`).
- `<period-type>` is `weekly`, `monthly`, or `quarterly`.
- `<period-id>` matches what you're reporting on: `2026-W29` (weekly, ISO
  week), `2026-07` (monthly), or `2026-Q3` (quarterly).

Example: stats for 1715 N Garland for the week of 2026-07-13 go in:

```
intake/1715-n-garland/weekly-2026-W29.json
```

You can drop a `.json` or a `.csv` — whichever is easier to fill out. If
both exist for the same period, the JSON file wins.

## Residential listings (homes.com)

**JSON**, single object:

```json
{
  "portal": "homes.com",
  "views": 214,
  "saves": 9
}
```

**CSV** (header row required):

```csv
portal,views,saves
homes.com,214,9
```

## Commercial / land listings (Crexi and/or LoopNet)

A listing can have numbers from more than one portal in the same period —
use a JSON array or multiple CSV rows.

**JSON**, array of objects:

```json
[
  { "portal": "crexi", "views": 88, "leads": 3 },
  { "portal": "loopnet", "views": 41, "leads": 1 }
]
```

**CSV**:

```csv
portal,views,leads
crexi,88,3
loopnet,41,1
```

## Field notes

- `portal` must be exactly one of `homes.com`, `crexi`, `loopnet` (lowercase,
  matches `config/listings.json` → `sources.portals`).
- Residential (`homes.com`) numbers use `views` and `saves`.
- Commercial/land (`crexi`, `loopnet`) numbers use `views` and `leads`.
- Leave a field out (or `0`) if the portal dashboard doesn't show it that
  period — don't guess.
- Whole numbers only. If a portal only gives you a range or a screenshot,
  read the exact number off the dashboard; don't estimate.

If you only have a screenshot and no dashboard number to type, drop the
image in `intake/<slug>/screenshots/` (create the folder) named
`<period-type>-<period-id>-<portal>.png` — for example
`intake/3300-blue-hill/screenshots/weekly-2026-W29-crexi.png`. These aren't
read automatically yet; a future OCR step will parse them into the same
JSON/CSV format above. Until then, please also type the numbers into a
JSON/CSV file per the format above so the report isn't blank.

## What happens if nothing is dropped

If no intake file exists for a listing/period, `collect.py` reports zeros
for that portal and marks it `data_quality: "missing"` in the generated
report — it does not guess or carry forward old numbers.
