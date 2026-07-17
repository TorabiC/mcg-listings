# Portal Intake — for Cameron

homes.com, Crexi, and LoopNet do not give us API access. Until that changes
(or an OCR step is built for portal screenshots), drop the numbers here by
hand each reporting period and `collect.py` will pick them up automatically.

There are two intake shapes. Both work, and you can mix them:

- **Simple, period-scoped** (original format) -- a couple of numbers per
  period. See "Where files go" below.
- **Rich, portal-scoped snapshot** (v2) -- the full portal dashboard export
  (views, display ads, publications, visitor map, milestones for homes.com;
  search score, funnel, e-blast performance for Crexi). This is what powers
  the "National Exposure", "Where Your Property Is Advertised", "Buyer
  Interest Map", "Momentum & Milestones", and Crexi performance sections of
  the v2 report. See "Rich portal snapshots (v2)" below.

## Where files go (simple, period-scoped)

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

## Rich portal snapshots (v2)

Drop the *entire* export from the portal dashboard into one of these files
(not period-scoped — portal dashboards only expose an all-time/current
snapshot, not a historical per-week breakdown, so one file covers every
period until you refresh it):

```
intake/<listing-slug>/homes_com.json
intake/<listing-slug>/crexi.json
intake/<listing-slug>/loopnet.json      (same shape as crexi.json)
```

Every field beyond the basic `views`/`saves`/`leads` is passed straight
through into `metrics.json` under `sources.portals.<portal>` verbatim, so
new report sections can render it. `collect.py` also derives the classic
`views`/`saves` (homes.com) or `views`/`leads` (Crexi/LoopNet) pair from the
rich object automatically — every existing chart and stat tile keeps working
unchanged, even on a rich-format file.

**homes.com** (`homes_com.json`) — object shape:

```json
{
  "portal": "homes.com",
  "views": 24063,
  "saves": 1,
  "summary": {
    "total_views": 24063, "display_ad_views": 2152, "detail_page_views": 86,
    "top_of_search_results": 2659, "favorites": 1,
    "matterport_views": 9, "matterport_view_time_min": 18, "floor_plan_views": 9
  },
  "traffic_sources": [ {"source": "Property Search Page", "pct": 89.3, "views": 21489} ],
  "daily": { "2026-06-22": 248, "2026-06-23": 490 },
  "display_ads": {
    "retargeting": {"ad_views": 854, "sites_displayed_on": 303, "users_reached": 284},
    "contact_list_targeting": {"ad_views": 1298, "sites_displayed_on": 108, "users_reached": 137, "uploaded_contacts": 7739},
    "publications": ["facebook.com", "cnn.com", "..."],
    "publication_logo_cdn": {"facebook.com": "https://imagescdn.homes.com/.../image.png?p=1"}
  },
  "milestones": [ {"date": "2026-06-28", "event": "Now considered a Hot Property"} ],
  "visitor_map": {
    "total_mapped_views": 20603,
    "markers": [ {"n": 4990, "x": 0.5033, "y": 0.517} ]
  }
}
```

`visitor_map.markers[].x/y` are normalized 0-1 within the portal's
continental-US map viewport (read directly off the dashboard's marker pixel
position divided by the viewport width/height) — values outside 0-1 are
markers the dashboard renders off the visible US frame (e.g. Alaska/Hawaii
insets or map padding) and the report clips them rather than guessing a
position.

**Crexi** (`crexi.json`) — object shape:

```json
{
  "portal": "crexi",
  "views": 1121,
  "leads": 25,
  "name": "3300 Blue Hill Road",
  "search_score": 95,
  "page_views": 1121,
  "visitors": 209,
  "om_flyer_opens": 25,
  "offers": 0,
  "dashboard_deep": {
    "impressions_all_time": 51183,
    "leads": {"visited_page": 209, "saved_property": 30, "opened_om_flyer": 25, "requested_info": 5, "clicked_phone": 14},
    "marketing_blasts": {"total_sent": 3055, "delivered": 3041, "delivered_pct": 99.5, "opened": 1535, "open_pct": 50.5, "clicked": 28, "click_pct": 1.8}
  },
  "secondary_listing": {
    "name": "3500B SW Regional Airport Blvd",
    "page_views": 41, "visitors": 3, "om_flyer_opens": 2
  }
}
```

`dashboard_deep` is optional — only fill it in when you've opened the
per-property "View Dashboard" page on Crexi (the My Listings table alone
only gives you `page_views`/`visitors`/`om_flyer_opens`/`search_score`).
`secondary_listing` is only for parcels that are dual-listed on Crexi under
a second address/suite (e.g. a building with two marketed units) — put the
second row's numbers there rather than creating a second listing slug.

If both a rich snapshot file and a legacy period-scoped file exist for the
same portal, the rich snapshot wins for that portal; the legacy file still
covers any *other* portal it mentions.

## What happens if nothing is dropped

If no intake file exists for a listing/period, `collect.py` reports zeros
for that portal and marks it `data_quality: "missing"` in the generated
report — it does not guess or carry forward old numbers.
