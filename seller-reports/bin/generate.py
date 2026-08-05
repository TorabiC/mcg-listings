#!/usr/bin/env python3
"""
generate.py -- render seller activity report pages, CC flyers, and PDFs
from metrics.json files, per seller-reports/SPEC.md.

Usage:
    python bin/generate.py --period-id 2026-W29 --slug all --outdir docs/reports
    python bin/generate.py --period-id 2026-W29 --slug 1715-n-garland \
        --listings config/listings.sample.json --outdir /tmp/out

Dependencies: jinja2 + stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "jinja2 is required. Install with: pip install jinja2 --break-system-packages\n"
    )
    raise

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# MCG brand / value-proposition constants
# Source: mcg-value-proposition skill, verified July 7, 2026 (single national
# syndication platform, first ~90 days of premium placement). Firm-level
# facts ($2.4B+, 30+ yrs) are standing MCG brand facts. Do not name the
# analytics source platform in any client-facing copy.
# ---------------------------------------------------------------------------
MCG_PROOF = {
    "years": "30+",
    "transactions": "$2.4B+",
    "views_90d": "nearly 1.4 million",
    "views_90d_compact": "1.4M+",
    "featured_sites": "187",
    "top_of_search": "nearly 46,000",
    "as_of": "Q3 2026",
    "tagline": "Northwest Arkansas real estate advisory -- brokerage, development, "
               "property management, and investment services.",
}

# Cameron's agent card, shown on the report hero. Standing contact facts --
# not sourced from a metrics.json.
AGENT = {
    "name": "Cameron Torabi",
    "firm": "Mason Capital Group Real Estate Investment & Trust",
    "phone": "(479) 925-3333",
    "phone_tel": "+14799253333",
}

# ---------------------------------------------------------------------------
# Anonymization -- CONTENT POLICY, not just a formatting nicety.
#
# Seller-facing copy never names the third-party platforms MCG uses to
# execute marketing (no "homes.com", "Crexi", "Constant Contact", "IDX
# Broker", "Google"/"GA4"/"Google Analytics" anywhere a seller can read it --
# chart labels, section notes, glossary, insights narrative). Sellers see
# results, not vendor/strategy names. The single exception: publications
# where display ads actually *ran* (WSJ, CNN, ESPN, ...) are shown by name --
# that's the impressive, non-strategic part.
#
# This is the ONE place channel/source display names and the anonymizing
# text-scrub live. Every code path that turns a raw source key or a raw
# metrics.json free-text field (insights, market notes, activity/showings
# text -- which collect.py's adapters sometimes write with a vendor name
# baked in) into seller-visible copy must route through CHANNEL_LABELS /
# anonymize_text() / anonymize_source_label() below rather than
# interpolating raw source keys or raw text directly.
# ---------------------------------------------------------------------------
CHANNEL_LABELS = {
    "idx": "the MCG website",
    "ga4": "MCG website analytics",
    "cc": "MCG's email marketing program",
    "tawk": "live chat",
    "portals": "MCG's national syndication partners",
    "homes.com": "MCG's national syndication partners",
    "crexi": "MCG's commercial marketplace network",
    "loopnet": "MCG's commercial marketplace network",
    # Clarity is folded into the same "MCG website analytics" channel as
    # GA4 everywhere it's surfaced (see build_channel_performance) -- same
    # anonymized label here for consistency.
    "clarity": "MCG website analytics",
}

SYNDICATION_BLURB = (
    "MCG's national syndication partners — the most capable platforms in "
    "residential and commercial real estate marketing."
)

# Ordered, most-specific-first. Possessive forms handled before the bare
# noun so we don't leave a dangling "'s". Applied case-insensitively.
_ANON_PATTERNS = [
    (re.compile(r"homes\.com's", re.IGNORECASE), "the network's"),
    (re.compile(r"homes\.com", re.IGNORECASE), "MCG's national syndication network"),
    (re.compile(r"Constant Contact", re.IGNORECASE), "MCG's email marketing program"),
    (re.compile(r"IDX Broker", re.IGNORECASE), "the MCG website"),
    (re.compile(r"\bIDX\b", re.IGNORECASE), "the MCG website"),
    (re.compile(r"Crexi's", re.IGNORECASE), "the network's"),
    (re.compile(r"\bCrexi\b", re.IGNORECASE), "MCG's commercial marketplace network"),
    (re.compile(r"\bGoogle Analytics\b", re.IGNORECASE), "MCG's website analytics"),
    (re.compile(r"\bGA4\b", re.IGNORECASE), "website analytics"),
    (re.compile(r"\bGoogle\b", re.IGNORECASE), "leading search engines"),
]


def anonymize_text(s: str | None) -> str | None:
    """Scrub vendor/platform names out of free-text fields written by
    collect.py's adapters (insights, market notes, activity/showing text)
    before they reach the template. See CHANNEL_LABELS block above."""
    if not s:
        return s
    out = s
    for pattern, repl in _ANON_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def anonymize_source_label(raw: str | None) -> str:
    """Map a raw GA4/portal traffic-source string to a seller-safe label.
    Structured-data counterpart to anonymize_text() -- used for chart
    labels built from source keys rather than prose."""
    s = (raw or "").strip().lower()
    if not s:
        return "Other"
    if s == "google":
        return "Search Engines"
    if s in ("(direct)", "direct"):
        return "Direct"
    if s in ("(not set)", "(data not available)", "not set", "not available"):
        return "Other"
    if any(tok in s for tok in ("brevo", "mailchimp", "constantcontact", "sp1", "sendgrid")):
        return "Email Campaigns"
    cleaned = anonymize_text(raw) or raw
    return cleaned.title()

# ---------------------------------------------------------------------------
# Harvest freshness -- as-of captions
#
# collect.py's source_freshness block (see SCHEMA.md) tells us whether a
# source's numbers were actually captured inside this reporting window
# ("fresh"), captured before it ("stale" -- real, just not current), or
# never captured ("missing", handled entirely by the existing stand-in
# machinery). freshness_caption() turns a "stale" entry into the visible
# "as of {date}" copy a tile/section must show rather than silently
# implying the figures are this period's activity.
# ---------------------------------------------------------------------------
def freshness_caption(source_freshness: dict | None, key: str) -> str | None:
    entry = (source_freshness or {}).get(key) or {}
    if entry.get("status") != "stale":
        return None
    as_of = entry.get("as_of")
    if as_of:
        return f"Figures as of {fmt_date_display(as_of)} (most recent snapshot on file)."
    return "Figures reflect the most recent snapshot on file, not necessarily this period."


PERIOD_TYPES = ["weekly", "monthly", "quarterly"]
PERIOD_ID_PATTERNS = {
    "weekly": re.compile(r"^\d{4}-W\d{2}$"),
    "monthly": re.compile(r"^\d{4}-\d{2}$"),
    "quarterly": re.compile(r"^\d{4}-Q[1-4]$"),
}

CHROMIUM_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_int(n) -> str:
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_pct(n, signed=False) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0%"
    sign = "+" if signed and n > 0 else ""
    return f"{sign}{n:.1f}%"


def fmt_price(listing: dict) -> str:
    price = listing.get("price") or 0
    price_type = (listing.get("price_type") or "").lower()
    is_lease = (
        "lease" in price_type
        or listing.get("type", "") == "lease"
        or listing.get("lease") is True
    )
    if is_lease:
        return f"${price:,.0f}/mo"
    return f"${price:,.0f}"


def fmt_date_display(iso_date: str | None) -> str:
    if not iso_date:
        return ""
    try:
        d = dt.date.fromisoformat(iso_date)
        return d.strftime("%b %-d, %Y") if hasattr(d, "strftime") else iso_date
    except ValueError:
        return iso_date


def fmt_date_short(iso_date: str | None) -> str:
    if not iso_date:
        return ""
    try:
        d = dt.date.fromisoformat(iso_date)
        return d.strftime("%b %-d")
    except ValueError:
        return iso_date


def type_display(type_str: str) -> str:
    mapping = {
        "residential": "Residential",
        "land": "Land",
        "commercial": "Commercial",
        "lease": "Lease",
        "mobile_home_park": "Mobile Home Park",
    }
    return mapping.get(type_str, (type_str or "Listing").replace("_", " ").title())


def period_type_label(t: str) -> str:
    return {"weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly"}.get(t, t.title())


def days_between(start_iso: str | None, end_iso: str | None) -> int | None:
    if not start_iso or not end_iso:
        return None
    try:
        s = dt.date.fromisoformat(start_iso)
        e = dt.date.fromisoformat(end_iso)
    except ValueError:
        return None
    return max((e - s).days, 0)


# ---------------------------------------------------------------------------
# View-model construction
# ---------------------------------------------------------------------------
def build_period_links(data_dir: Path, slug: str, current_period: dict, outdir: Path, slug_token: str) -> list[dict]:
    """Scan data/<slug>/ for available period folders of each type and build
    switcher entries. Links point to the same output convention generate.py
    writes to, whether or not that period has been rendered yet."""
    listing_data_dir = data_dir / slug
    available_by_type: dict[str, list[str]] = {t: [] for t in PERIOD_TYPES}
    if listing_data_dir.is_dir():
        for child in listing_data_dir.iterdir():
            if not child.is_dir():
                continue
            pid = child.name
            if not (child / "metrics.json").exists():
                continue
            for ptype, pattern in PERIOD_ID_PATTERNS.items():
                if pattern.match(pid):
                    available_by_type[ptype].append(pid)

    links = []
    for ptype in PERIOD_TYPES:
        ids = sorted(available_by_type[ptype])
        is_current_type = current_period["type"] == ptype
        if is_current_type:
            chosen_id = current_period["id"]
            available = True
        elif ids:
            chosen_id = ids[-1]  # latest available
            available = True
        else:
            chosen_id = None
            available = False
        entry = {
            "type": ptype,
            "label": period_type_label(ptype),
            "id": chosen_id,
            "available": available,
            "active": is_current_type,
            "url": f"../../{slug_token}/{chosen_id}/index.html" if available else None,
        }
        links.append(entry)
    return links


def cap_top_n_with_other(items: list[dict], key_value: str, key_label: str, n: int) -> list[dict]:
    items_sorted = sorted(items, key=lambda x: x.get(key_value, 0), reverse=True)
    top = items_sorted[:n]
    rest = items_sorted[n:]
    result = list(top)
    if rest:
        other_val = sum(r.get(key_value, 0) for r in rest)
        if other_val > 0:
            result.append({key_label: "Other", key_value: other_val})
    return result


# Fixed categorical order per brand palette (MCG listing-page design system:
# crimson / navy / gold / teal family). Always paired with direct labels and
# a legend (never color-alone identification) -- see dataviz skill.
#
# These are NOT the raw brand chrome hex values (--navy/--gold in report.html)
# -- validated 2026-07-18 with the dataviz skill's validate_palette.js: the
# raw navy (#16162a) and gold (#c4a35a) fail the categorical lightness/chroma
# floors when used as data marks on a white card (they read as near-black /
# near-gray). These four steps are brand-hue-family variants re-stepped into
# the passing OKLCH band, confirmed ALL CHECKS PASS on --pairs all (light
# mode): worst all-pairs CVD ΔE 9.1 (protan), normal-vision floor 22.9.
# Per the skill's series cap, only 4 slots are safe for all-pairs contexts
# (bar list + wrapped legend); anything past 4 folds into "Other", which
# takes CATEGORICAL_OTHER_COLOR instead of cycling back through the identity
# hues (an "Other" residual is not a 5th identity to confuse with the first).
CATEGORICAL_COLORS = ["#ab012e", "#3568c9", "#eda100", "#1baf7a"]
CATEGORICAL_OTHER_COLOR = "#9a9aa6"


def _categorical_color(item: dict, label_key: str, i: int) -> str:
    """Slot-1..4 identity hue by position, except the 'Other' residual
    bucket, which always takes the reserved neutral (never cycles back
    through the identity colors -- see CATEGORICAL_COLORS comment)."""
    if item.get(label_key) == "Other":
        return CATEGORICAL_OTHER_COLOR
    return CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)]


def build_traffic_sources_chart(top_sources: list[dict]) -> dict:
    """Legacy single-source chart (GA4 top_sources only) -- kept for the
    flyer/back-compat callers. See build_traffic_sources_merged for the
    report page's merged portals+GA4 view."""
    capped = cap_top_n_with_other(top_sources, "users", "source", 4)
    max_val = max((c.get("users", 0) for c in capped), default=0) or 1
    bars = []
    for i, c in enumerate(capped):
        label = "Other" if c.get("source") == "Other" else anonymize_source_label(c.get("source"))
        bars.append({
            "label": label,
            "value": c.get("users", 0),
            "pct": round((c.get("users", 0) / max_val) * 100, 1),
            "color": _categorical_color(c, "source", i),
        })
    return {"bars": bars, "available": bool(bars)}


def build_traffic_sources_merged(portals_raw: dict, ga4_top_sources: list[dict]) -> dict:
    """Homes.com-style 'Top Traffic Sources' bar list, merged across every
    portal's traffic_sources[] and GA4's top_sources[] -- anonymized labels,
    combined into one ranked list capped to the top 4 + Other (4 is the
    dataviz-skill-validated all-pairs-safe series count for this brand
    palette; see CATEGORICAL_COLORS comment above)."""
    combined: dict[str, int] = {}
    for portal in (portals_raw or {}).values():
        for row in (portal.get("traffic_sources") or []):
            label = anonymize_source_label(row.get("source"))
            combined[label] = combined.get(label, 0) + int(row.get("views", 0) or 0)
    for row in (ga4_top_sources or []):
        label = anonymize_source_label(row.get("source"))
        combined[label] = combined.get(label, 0) + int(row.get("users", 0) or 0)

    items = [{"source": k, "value": v} for k, v in combined.items() if v > 0]
    capped = cap_top_n_with_other(items, "value", "source", 4)
    total = sum(c.get("value", 0) for c in capped) or 1
    bars = []
    for i, c in enumerate(capped):
        bars.append({
            "label": c.get("source", "Other"),
            "value": c.get("value", 0),
            "pct": round((c.get("value", 0) / total) * 100, 1),
            "color": _categorical_color(c, "source", i),
        })
    return {"bars": bars, "available": bool(bars)}


# ---------------------------------------------------------------------------
# Homes.com-mirror variant -- Cameron-approved pixel-faithful clone of
# homes.com's Listing Analytics page for residential listings whose portals
# include homes.com data. Commercial (Crexi) listings never set homes_mirror
# and keep rendering the original report.html layout below, unchanged.
# ---------------------------------------------------------------------------

# Single source of truth for the "Top Traffic Sources" row labels on the
# homes-mirror Views section -- maps homes.com's raw traffic_sources[]
# source strings to the anonymized, vendor-free labels. Never surface a raw
# source string (e.g. "GreatSchools", "Niche.com") to a seller; anything not
# in this map falls back through anonymize_source_label().
HOMES_TRAFFIC_LABEL_MAP = {
    "property search page": "Partner Search Network",
    "display ads": "Display Ads",
    "greatschools": "Family & Schools Network",
    "multiple sources": "Multiple Sources",
    "detail page views": "Detail Page Views",
    "niche.com": "Lifestyle Network",
}

# NOTE: an earlier version of this file approximated a per-day Agent/Consumer
# split for the Activity chart by applying a single observed ratio (from a
# different listing/period) to each day's combined total. That produced a
# plausible-looking but fabricated daily series -- the homes.com intake never
# actually reports a per-day agent/consumer breakdown, only per-day COMBINED
# views (the `daily` object) and, separately, real Agent/Consumer period
# TOTALS (the `activity` object). build_homes_mirror_activity below now only
# charts the real daily Combined series and surfaces the real Agent/Consumer
# totals as stat chips -- see that function's docstring.

HOMES_GLOSSARY = [
    ("Total Views", "Every time a home-shopper opened this listing across MCG's national syndication network during the reporting period."),
    ("Display Ad Views", "Impressions of this listing's retargeting ad shown to prior visitors and matched contacts on partner publications."),
    ("Detail Page Views", "Views of the listing's full detail page, as opposed to a search-results thumbnail."),
    ("Top of Search Results", "Times this listing appeared in the first position of a buyer's search results."),
    ("Favorites", "Buyers who saved this listing to their account for later."),
    ("3D Tour Views / View Time", "Sessions -- and cumulative minutes -- spent in the listing's virtual walkthrough."),
    ("Floor Plan Views", "Views of the listing's floor plan graphic."),
    ("Agent Views", "Views attributed to real-estate-agent accounts rather than the general public."),
    ("Consumer Views", "Views attributed to the general home-shopping public."),
    ("Retargeting Ad Views", "Display-ad impressions served to people who had already visited this listing, shown again on other sites."),
    ("Contact List Targeting", "Display ads served to MCG's uploaded buyer/investor contact list, matched to this listing."),
    ("Users Reached", "Unique individuals served at least one ad impression for this listing."),
]


def homes_traffic_label(raw_source: str | None) -> str:
    key = (raw_source or "").strip().lower()
    if key in HOMES_TRAFFIC_LABEL_MAP:
        return HOMES_TRAFFIC_LABEL_MAP[key]
    return anonymize_source_label(raw_source)


# Each row in this list carries its own direct text label + value (no
# adjacent shared-legend swatch key), so it isn't subject to the 4-slot
# all-pairs-CVD cap that applies to charts + wrapped legends (see
# CATEGORICAL_COLORS comment) -- homes.com's own traffic-source list uses a
# distinct color per row and this clones that, brand-hue-family only.
TRAFFIC_ROW_COLORS = ["#AB012E", "#16162A", "#C4A35A", "#3568C9", "#1BAF7A", "#EDA100"]

# ROUND 2 hero card style switch -- Cameron asked for three simplified
# overlay-card variants (property card + agent card, homes-mirror hero) to
# choose between: 'a' ultra-minimal white, 'b' frosted glass, 'c' navy
# panel. All three variants share identical simplified content/markup
# (see .hcv-card in templates/report.html) -- only the CSS theme differs,
# selected by this one constant. Once Cameron picks a winner, flip this
# single line and every report picks it up; no template edits needed.
# HERO_CARD_STYLE_OVERRIDE env var is a dev-only convenience for rendering
# side-by-side variant comparisons without editing this file each time --
# production runs are driven by the HERO_CARD_STYLE constant only.
HERO_CARD_STYLE = os.environ.get("HERO_CARD_STYLE_OVERRIDE", "a")


def build_homes_mirror_traffic(traffic_sources_raw: list[dict]) -> dict:
    """homes.com-style 'Top Traffic Sources' stacked-bar list -- keeps the
    portal's own order (already largest-first) and its own pct/views, since
    this section specifically clones homes.com's own traffic-sources module
    rather than the merged multi-channel chart used elsewhere in the
    report."""
    rows = []
    total = 0
    for i, row in enumerate(traffic_sources_raw or []):
        views = int(row.get("views", 0) or 0)
        total += views
        rows.append({
            "label": homes_traffic_label(row.get("source")),
            "pct": row.get("pct", 0.0),
            "views": views,
            "color": TRAFFIC_ROW_COLORS[i % len(TRAFFIC_ROW_COLORS)],
        })
    return {"rows": rows, "total": total, "available": bool(rows)}


def build_homes_mirror_activity(
    daily: dict,
    daily_by_type: dict | None = None,
    activity_totals: dict | None = None,
) -> dict:
    """Activity chart view-model for the homes-mirror Activity section's
    Combined/Consumer/Agent selector -- ROUND 2: the selector must always be
    engage-able (no disabled options) and each category must render its own
    TRUE data, never a value derived from another category.

    Three real data shapes can back a category, checked in this priority
    order:
      1. `daily_by_type[<series>]` -- a real per-day breakdown for that
         category (see intake/README.md's `daily_by_type` section). When a
         category's daily series covers every date the combined series
         covers, the selector draws real daily bars for it, each in that
         category's brand color, with its own y-axis scale.
      2. `activity[<series>_views]` -- a real period TOTAL with no daily
         breakdown. When a category has a total but not a complete daily
         series, selecting it renders an honest summary panel (period total
         + share-of-combined bar, still in that category's color) instead
         of fabricating daily bars.
      3. Neither -- the summary panel renders a "no data yet" state. Still
         not a disabled option; there's just nothing real to show.

    `daily_by_type.combined`, when present, supersedes the legacy `daily`
    object as the Combined series' source (a listing can be re-harvested
    with a fresher/longer daily_by_type snapshot while `daily` still holds
    an older partial range -- using `daily_by_type.combined` keeps all
    three categories on the same date range instead of mixing two
    different-length series under one x-axis)."""
    daily_by_type = daily_by_type or {}
    combined_daily = daily_by_type.get("combined") or daily or {}
    if not combined_daily:
        return {"available": False}

    dates = sorted(combined_daily.keys())
    consumer_daily = daily_by_type.get("consumer") or {}
    agent_daily = daily_by_type.get("agent") or {}
    consumer_daily_complete = bool(consumer_daily) and all(d in consumer_daily for d in dates)
    agent_daily_complete = bool(agent_daily) and all(d in agent_daily for d in dates)

    bars = []
    for d in dates:
        row = {
            "date": d,
            "date_short": fmt_date_short(d),
            "combined": int(combined_daily.get(d) or 0),
        }
        if consumer_daily_complete:
            row["consumer"] = int(consumer_daily[d] or 0)
        if agent_daily_complete:
            row["agent"] = int(agent_daily[d] or 0)
        bars.append(row)

    n = len(bars)
    label_every = max(1, round(n / 7))
    label_idxs = set(range(0, n, label_every))
    if n - 1 not in label_idxs:
        label_idxs.add(n - 1)
    for i, b in enumerate(bars):
        b["show_label"] = i in label_idxs

    daily_series_available = {
        "combined": True,
        "consumer": consumer_daily_complete,
        "agent": agent_daily_complete,
    }

    activity_totals = activity_totals or {}
    total_combined = sum(int(v or 0) for v in combined_daily.values())
    if consumer_daily_complete:
        total_consumer = sum(int(v or 0) for v in consumer_daily.values())
    else:
        raw = activity_totals.get("consumer_views")
        total_consumer = int(raw) if raw is not None else None
    if agent_daily_complete:
        total_agent = sum(int(v or 0) for v in agent_daily.values())
    else:
        raw = activity_totals.get("agent_views")
        total_agent = int(raw) if raw is not None else None

    totals_available = {
        "combined": True,
        "consumer": total_consumer is not None,
        "agent": total_agent is not None,
    }

    return {
        "available": True,
        "bars": bars,
        "daily_series_available": daily_series_available,
        "totals": {
            "combined": total_combined,
            "consumer": total_consumer or 0,
            "agent": total_agent or 0,
        },
        "totals_available": totals_available,
        # Legacy flat keys, kept so nothing else reading this view-model
        # (e.g. any narrative text elsewhere) breaks.
        "total_combined": total_combined,
        "total_consumer": total_consumer or 0,
        "total_agent": total_agent or 0,
        "agent_consumer_available": totals_available["consumer"] and totals_available["agent"],
    }


# Bounding box the homes.com intake's normalized x/y marker coordinates were
# captured against -- a map viewport that covered roughly the NWA region
# (Oklahoma City to Springfield MO). See build_homes_mirror_leaflet_markers.
VISITOR_MAP_LAT_MAX = 37.8
VISITOR_MAP_LAT_MIN = 34.2
VISITOR_MAP_LNG_MIN = -98.5
VISITOR_MAP_LNG_MAX = -91.8


def _homes_mirror_convert_markers(markers_raw: list[dict]) -> tuple[list[dict], int, int]:
    """Shared x/y -> lat/lng conversion for both the Total Views marker set
    and the optional Engaged Buyer Views cluster layer (see
    build_homes_mirror_leaflet_markers)."""
    clipped = [m for m in markers_raw if 0.0 <= m.get("x", -1) <= 1.0 and 0.0 <= m.get("y", -1) <= 1.0]
    out = []
    for m in clipped:
        x, y, n = m["x"], m["y"], m.get("n", 0)
        lat = VISITOR_MAP_LAT_MAX - y * (VISITOR_MAP_LAT_MAX - VISITOR_MAP_LAT_MIN)
        lng = VISITOR_MAP_LNG_MIN + x * (VISITOR_MAP_LNG_MAX - VISITOR_MAP_LNG_MIN)
        # Leaflet circleMarker radius is in screen pixels, not map units --
        # scaled down from the static-SVG map's coefficient (which draws
        # into a fixed 1000x520 viewBox) so the largest marker doesn't
        # dominate the interactive map at typical zoom levels.
        r = round(5 + (max(n, 0) ** 0.5) * 0.42, 1)
        out.append({"lat": round(lat, 5), "lng": round(lng, 5), "n": n, "r": r})
    return out, len(clipped), len(markers_raw) - len(clipped)


def build_homes_mirror_leaflet_markers(vm_raw: dict) -> dict:
    """Converts the homes.com intake's viewport-normalized x/y marker
    coordinates into lat/lng for the interactive Leaflet map, linearly
    against VISITOR_MAP_LAT/LNG_*. Clips out-of-range markers same as the
    static SVG buyer map (build_visitor_map).

    Also looks for an optional `engaged_clusters` array on the same
    visitor_map object (same {n,x,y} shape as `markers`, see
    intake/README.md) -- a higher-buyer-intent cluster breakdown the portal
    dashboard doesn't always expose. When present, it powers the Visitor
    Map's 'Engaged Buyer Views' toggle with a real second layer; when
    absent, `engaged.available` is False and report.html does not render
    that toggle button at all (never a disabled/dead button standing in for
    data that doesn't exist)."""
    markers_raw = vm_raw.get("markers") or []
    out, clipped_count, dropped_count = _homes_mirror_convert_markers(markers_raw)

    engaged_raw = vm_raw.get("engaged_clusters") or []
    engaged_out, engaged_clipped, engaged_dropped = _homes_mirror_convert_markers(engaged_raw)
    # Prefer the intake's own engaged_total_views (the ground-truth number
    # read directly off the portal's "Engaged Buyer Views" toggle) over
    # summing the post-clip marker set -- same convention as the Total
    # Views caption above, which uses vm_raw["total_mapped_views"] rather
    # than re-summing `markers`, so an out-of-frame/clipped cluster (e.g.
    # an Alaska/Hawaii inset marker) doesn't quietly shrink the caption
    # below the portal's real total.
    engaged_total = vm_raw.get("engaged_total_views")
    if engaged_total is None:
        engaged_total = sum(m["n"] for m in engaged_out)
    engaged = {
        "available": bool(engaged_out),
        "markers": engaged_out,
        "total_mapped_views": engaged_total,
        "clipped_count": engaged_clipped,
        "dropped_count": engaged_dropped,
    }

    return {
        "available": bool(out),
        "markers": out,
        "total_mapped_views": vm_raw.get("total_mapped_views", 0),
        "clipped_count": clipped_count,
        "dropped_count": dropped_count,
        "engaged": engaged,
    }


def build_homes_mirror(homes_raw: dict, homes_exposure: dict) -> dict:
    """Assembles the full view-model the homes-mirror template branch reads.
    Only called when homes_mirror (residential + live homes.com portal data)
    is true -- see build_view_model."""
    stat_row_1 = [
        {"label": "Total Views", "value": fmt_int(homes_exposure["total_views"])},
        {"label": "Display Ad Views", "value": fmt_int(homes_exposure["display_ad_views"])},
        {"label": "Detail Page Views", "value": fmt_int(homes_exposure["detail_page_views"])},
        {"label": "Top of Search Results", "value": fmt_int(homes_exposure["top_of_search"])},
        {"label": "Favorites", "value": fmt_int(homes_exposure["favorites"])},
    ]
    stat_row_2 = [
        {"label": "3D Tour Views", "value": fmt_int(homes_exposure["matterport_views"])},
        {"label": "Floor Plan Views", "value": fmt_int(homes_exposure["floor_plan_views"])},
        {"label": "3D Tour View Time", "value": f"{fmt_int(homes_exposure['matterport_minutes'])} min"},
    ]
    pubs = homes_exposure.get("publications") or []
    display_ads_raw = homes_raw.get("display_ads") or {}
    retarget_raw = display_ads_raw.get("retargeting") or {}
    contact_raw = display_ads_raw.get("contact_list_targeting") or {}
    return {
        "available": True,
        "stat_row_1": stat_row_1,
        "stat_row_2": stat_row_2,
        "traffic": build_homes_mirror_traffic(homes_raw.get("traffic_sources") or []),
        "activity_chart": build_homes_mirror_activity(
            homes_raw.get("daily") or {},
            homes_raw.get("daily_by_type"),
            homes_raw.get("activity"),
        ),
        "leaflet": build_homes_mirror_leaflet_markers(homes_raw.get("visitor_map") or {}),
        "retargeting": {
            "ad_views": retarget_raw.get("ad_views", 0),
            "sites_displayed_on": retarget_raw.get("sites_displayed_on", 0),
            "users_reached": retarget_raw.get("users_reached", 0),
        },
        "contact_targeting": {
            "ad_views": contact_raw.get("ad_views", 0),
            "sites_displayed_on": contact_raw.get("sites_displayed_on", 0),
            "users_reached": contact_raw.get("users_reached", 0),
            "uploaded_contacts": contact_raw.get("uploaded_contacts", 0),
        },
        "publications": pubs,
        "publication_count": len(pubs),
        "glossary": HOMES_GLOSSARY,
    }


def build_email_chart(campaigns: list[dict]) -> dict:
    max_sent = max((c.get("sent", 0) for c in campaigns), default=0) or 1
    rows = []
    for i, c in enumerate(campaigns):
        sent = c.get("sent", 0)
        opens = c.get("opens", 0)
        rows.append({
            "name": anonymize_text(c.get("name", "Campaign")),
            "sent": sent,
            "opens": opens,
            "clicks": c.get("clicks", 0),
            "open_rate": c.get("open_rate", 0.0),
            "sent_pct": round((sent / max_sent) * 100, 1),
            "opens_pct": round((opens / max_sent) * 100, 1) if max_sent else 0,
            "color": CATEGORICAL_COLORS[0],
            "accent": CATEGORICAL_COLORS[2],
        })
    return {"rows": rows, "available": bool(rows)}


def build_views_comparison_chart(prior: int, current: int) -> dict:
    max_val = max(prior, current, 1)
    return {
        "prior": prior,
        "current": current,
        "prior_pct": round((prior / max_val) * 100, 1),
        "current_pct": round((current / max_val) * 100, 1),
        "available": True,
    }


def build_dom_gauge(listing: dict, market: dict, period_end: str) -> dict:
    area_dom = market.get("area_dom_days")
    county = market.get("county", "")
    listing_dom = days_between(listing.get("list_date"), period_end)
    if listing_dom is None or not area_dom:
        return {"available": False, "county": county, "area_dom": area_dom}
    ref_max = max(area_dom * 1.6, listing_dom * 1.2, 1)
    pct = min(listing_dom / ref_max, 1.0) * 100
    area_pct = min(area_dom / ref_max, 1.0) * 100
    ahead_days = area_dom - listing_dom
    return {
        "available": True,
        "listing_dom": listing_dom,
        "area_dom": area_dom,
        "county": county,
        "pct": round(pct, 1),
        "area_pct": round(area_pct, 1),
        "ahead_days": ahead_days,
        "pacing_good": ahead_days >= 0,
    }


def build_comps(comps: list[dict], listing_price: float) -> tuple[list[dict], str | None]:
    out = []
    active_prices = [c["price"] for c in comps if c.get("status") == "active" and c.get("price")]
    overpriced_note = None
    if active_prices and listing_price:
        avg_active = sum(active_prices) / len(active_prices)
        diff_pct = (listing_price - avg_active) / avg_active * 100
        if diff_pct > 3:
            overpriced_note = (
                f"Listed {diff_pct:.1f}% above the average of active comparables "
                f"({fmt_int(avg_active)})."
            )
    for c in comps:
        out.append({
            **c,
            "price_display": f"${c.get('price', 0):,.0f}" if c.get("price") else "--",
            "status_class": {"active": "status-active", "pending": "status-pending",
                              "sold": "status-sold"}.get(c.get("status"), ""),
        })
    return out, overpriced_note


# ---------------------------------------------------------------------------
# Portal exposure (v2) -- homes.com / Crexi rich analytics
# ---------------------------------------------------------------------------
INDUSTRY_BENCHMARK_NOTE = (
    "vs. ~30-35% industry average open rate (approximate, unsourced industry "
    "benchmark for commercial real estate e-blasts -- shown for context only)."
)


def grade_search_score(score) -> str | None:
    if score is None:
        return None
    try:
        score = float(score)
    except (TypeError, ValueError):
        return None
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Very Good"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Needs Improvement"


def build_daily_views_chart(daily: dict) -> dict:
    if not daily:
        return {"available": False}
    items = sorted(daily.items())
    max_val = max((v for _, v in items), default=0) or 1
    n = len(items)
    label_every = max(1, round(n / 7))
    # Selective direct labels without collisions: label every Nth bar, but
    # if the final bar would land too close to the last regular label,
    # replace that label with the final bar instead of adding a second one.
    label_idxs = list(range(0, n, label_every))
    if n - 1 not in label_idxs:
        if label_idxs and (n - 1 - label_idxs[-1]) <= max(1, label_every // 2):
            label_idxs[-1] = n - 1
        else:
            label_idxs.append(n - 1)
    label_set = set(label_idxs)
    bars = []
    for i, (d, v) in enumerate(items):
        bars.append({
            "date": d,
            "date_short": fmt_date_short(d),
            "value": v,
            "pct": round(v / max_val * 100, 1),
            "show_label": i in label_set,
        })
    return {
        "available": True,
        "bars": bars,
        "max_val": max_val,
        "total": sum(v for _, v in items),
    }


VISITOR_MAP_VB_W = 1000
VISITOR_MAP_VB_H = 520


def build_visitor_map(vm: dict) -> dict:
    markers_raw = vm.get("markers") or []
    total_mapped = vm.get("total_mapped_views", 0)
    if not markers_raw:
        return {"available": False}

    clipped = [m for m in markers_raw if 0.0 <= m.get("x", -1) <= 1.0 and 0.0 <= m.get("y", -1) <= 1.0]
    dropped = len(markers_raw) - len(clipped)
    top8 = set(
        idx for idx, _ in sorted(enumerate(clipped), key=lambda p: -p[1].get("n", 0))[:8]
    )

    markers = []
    for i, m in enumerate(clipped):
        n = m.get("n", 0)
        r = round(4 + (max(n, 0) ** 0.5) * 0.48, 1)
        markers.append({
            "cx": round(m["x"] * VISITOR_MAP_VB_W, 1),
            "cy": round(m["y"] * VISITOR_MAP_VB_H, 1),
            "r": r,
            "n": n,
            "label": i in top8,
        })
    # Draw smaller markers first so the biggest circles (and their labels)
    # sit on top and stay legible.
    markers.sort(key=lambda mk: mk["r"])

    return {
        "available": True,
        "markers": markers,
        "total_mapped_views": total_mapped,
        "clipped_count": len(clipped),
        "dropped_count": dropped,
        "viewbox_w": VISITOR_MAP_VB_W,
        "viewbox_h": VISITOR_MAP_VB_H,
    }


def build_homes_exposure(portals: dict, source_freshness: dict | None = None) -> dict | None:
    homes = portals.get("homes.com") or {}
    summary = homes.get("summary")
    if not summary:
        return None
    stale_caption = freshness_caption(source_freshness, "homes.com")

    display_ads = homes.get("display_ads") or {}
    publications = display_ads.get("publications") or []
    logo_cdn = display_ads.get("publication_logo_cdn") or {}
    pubs = []
    for p in publications:
        label = p.split(".")[0].replace("-", " ").title()
        pubs.append({"domain": p, "logo": logo_cdn.get(p), "name": label})

    retarget = display_ads.get("retargeting") or {}
    contact = display_ads.get("contact_list_targeting") or {}
    ad_views_total = summary.get("display_ad_views") or (
        retarget.get("ad_views", 0) + contact.get("ad_views", 0)
    )
    users_reached_total = retarget.get("users_reached", 0) + contact.get("users_reached", 0)

    milestones = sorted(homes.get("milestones") or [], key=lambda m: m.get("date", ""))
    for ms in milestones:
        ms["date_display"] = fmt_date_display(ms.get("date"))

    return {
        "available": True,
        "total_views": summary.get("total_views", 0),
        "top_of_search": summary.get("top_of_search_results", 0),
        "display_ad_views": summary.get("display_ad_views", 0),
        "matterport_views": summary.get("matterport_views", 0),
        "matterport_minutes": summary.get("matterport_view_time_min", 0),
        "favorites": summary.get("favorites", 0),
        "floor_plan_views": summary.get("floor_plan_views", 0),
        "detail_page_views": summary.get("detail_page_views", 0),
        "publications": pubs,
        "publication_count": len(pubs),
        "ad_views_total": ad_views_total,
        "sites_displayed_on": retarget.get("sites_displayed_on", 0),
        "users_reached_total": users_reached_total,
        "contacts_targeted": contact.get("uploaded_contacts", 0),
        "milestones": milestones,
        "latest_milestone": milestones[-1] if milestones else None,
        "daily_chart": build_daily_views_chart(homes.get("daily") or {}),
        "visitor_map": build_visitor_map(homes.get("visitor_map") or {}),
        "analytics_url": homes.get("analytics_url"),
        "days_on_market_portal": homes.get("days_on_market"),
        "listed_date": homes.get("listed"),
        "stale": bool(stale_caption),
        "stale_caption": stale_caption,
    }


def build_crexi_exposure(portals: dict, source_freshness: dict | None = None) -> dict | None:
    crexi = portals.get("crexi") or {}
    if crexi.get("search_score") is None and not crexi.get("page_views"):
        return None
    stale_caption = freshness_caption(source_freshness, "crexi")

    dashboard = crexi.get("dashboard_deep") or {}
    leads = dashboard.get("leads") or {}
    blasts = dashboard.get("marketing_blasts") or {}
    secondary = crexi.get("secondary_listing")

    funnel = []
    if leads:
        steps = [
            ("Visited page", leads.get("visited_page", 0)),
            ("Saved property", leads.get("saved_property", 0)),
            ("Opened OM / flyer", leads.get("opened_om_flyer", 0)),
            ("Requested info", leads.get("requested_info", 0)),
            ("Clicked phone", leads.get("clicked_phone", 0)),
        ]
        max_f = max((v for _, v in steps), default=0) or 1
        funnel = [{"label": l, "value": v, "pct": round(v / max_f * 100, 1)} for l, v in steps]

    score = crexi.get("search_score")
    eblast = None
    if blasts:
        eblast = {
            "total_sent": blasts.get("total_sent", 0),
            "delivered": blasts.get("delivered", 0),
            "delivered_pct": blasts.get("delivered_pct", 0),
            "opened": blasts.get("opened", 0),
            "open_pct": blasts.get("open_pct", 0),
            "clicked": blasts.get("clicked", 0),
            "click_pct": blasts.get("click_pct", 0),
            "benchmark_note": INDUSTRY_BENCHMARK_NOTE,
        }

    impressions = dashboard.get("impressions_all_time")
    return {
        "available": True,
        "search_score": score,
        "search_score_grade": grade_search_score(score),
        "search_score_pct": round((score or 0), 1),
        "impressions": impressions or crexi.get("page_views", 0),
        "impressions_is_deep": bool(impressions),
        "page_views": crexi.get("page_views", 0),
        "visitors": crexi.get("visitors", 0),
        "om_flyer_opens": crexi.get("om_flyer_opens", 0),
        "offers": crexi.get("offers", 0),
        "funnel": funnel,
        "eblast": eblast,
        "secondary_listing": secondary,
        "stale": bool(stale_caption),
        "stale_caption": stale_caption,
    }


def _fmt_n(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def build_channel_performance(src: dict, dq: dict, homes_exposure: dict | None,
                              crexi_exposure: dict | None, portals_raw: dict) -> dict:
    """Multi-channel performance blocks for the 'Full Marketing Footprint'
    section. One card per LIVE channel (never fabricate an inactive one),
    anonymized channel names per the content policy, plus a combined
    touchpoints headline. Windows differ by channel (site analytics are
    period-scoped; syndication counters run since listing) -- the section
    carries an honest footnote instead of pretending otherwise."""
    channels, combined = [], 0

    ga4 = src.get("ga4") or {}
    clarity = src.get("clarity") or {}
    # Clarity is folded into the same "MCG website analytics" channel as
    # GA4 (both instrument the same Webflow-hosted pages -- see the
    # CHANNEL_LABELS/Webflow note) as a supplemental, clearly-labeled
    # engagement signal, never its own vendor-named channel and never
    # treated as a primary view count (numOfDays is API-capped at 3 days).
    clarity_live = dq.get("clarity") in ("live", "sample") and clarity.get("sessions")
    if (dq.get("ga4") == "live" and (ga4.get("pageviews") or ga4.get("users"))) or clarity_live:
        top = (ga4.get("top_sources") or [])
        top_label = anonymize_source_label(top[0].get("source", "")) if top else None
        eng = ga4.get("avg_engagement_s") or 0
        stats = []
        if ga4.get("pageviews") or ga4.get("users"):
            stats.append({"label": "Listing page views this period", "value": _fmt_n(ga4.get("pageviews", 0))})
            stats.append({"label": "Unique visitors", "value": _fmt_n(ga4.get("users", 0))})
        if eng:
            stats.append({"label": "Avg. time engaged", "value": f"{int(eng // 60)}m {int(eng % 60)}s"})
        if top_label:
            stats.append({"label": "Top traffic source", "value": top_label})
        if clarity_live:
            coverage = clarity.get("coverage_days") or 0
            window_label = f"last {coverage} day{'s' if coverage != 1 else ''}" if coverage else "recent activity"
            stats.append({
                "label": f"Supplemental engagement signal ({window_label})",
                "value": f"{_fmt_n(clarity.get('sessions', 0))} sessions",
            })
        channels.append({
            "key": "website", "icon": "globe",
            "name": "MasonCapitalGroup.com",
            "tagline": "Your listing's dedicated page on the MCG website",
            "stats": stats,
        })
        combined += int(ga4.get("pageviews", 0) or 0)

    if homes_exposure:
        channels.append({
            "key": "syndication", "icon": "network",
            "name": "National Syndication Network",
            "tagline": "MCG's premier residential syndication partners",
            "stats": [
                {"label": "Total views since listing", "value": _fmt_n(homes_exposure.get("total_views", 0))},
                {"label": "Display ad impressions", "value": _fmt_n(homes_exposure.get("display_ad_views", 0))},
                {"label": "Top-of-search placements", "value": _fmt_n(homes_exposure.get("top_of_search", 0))},
                {"label": "Publications carrying your listing", "value": _fmt_n(homes_exposure.get("publication_count", 0))},
            ],
        })
        combined += int(homes_exposure.get("total_views", 0) or 0)

    if crexi_exposure:
        stats = [
            {"label": "Marketplace page views", "value": _fmt_n(crexi_exposure.get("page_views", 0))},
            {"label": "Unique visitors", "value": _fmt_n(crexi_exposure.get("visitors", 0))},
            {"label": "OM / flyer opens", "value": _fmt_n(crexi_exposure.get("om_flyer_opens", 0))},
        ]
        if crexi_exposure.get("search_score") is not None:
            stats.append({"label": "MCG placement score", "value": f"{crexi_exposure['search_score']} / 100"})
        if crexi_exposure.get("impressions"):
            stats.insert(0, {"label": "Marketplace impressions", "value": _fmt_n(crexi_exposure["impressions"])})
            combined += int(crexi_exposure.get("impressions") or 0)
        else:
            combined += int(crexi_exposure.get("page_views", 0) or 0)
        channels.append({
            "key": "commercial", "icon": "building",
            "name": "Commercial Marketplace Network",
            "tagline": "MCG's investor- and developer-facing marketplace partners",
            "stats": stats,
        })

    loopnet = portals_raw.get("loopnet") or {}
    if loopnet.get("views") or loopnet.get("leads"):
        channels.append({
            "key": "loopnet", "icon": "building",
            "name": "Commercial Marketplace Network — national reach",
            "tagline": "Extended commercial syndication coverage",
            "stats": [
                {"label": "Listing views", "value": _fmt_n(loopnet.get("views", 0))},
                {"label": "Buyer leads", "value": _fmt_n(loopnet.get("leads", 0))},
            ],
        })
        combined += int(loopnet.get("views", 0) or 0)

    cc = src.get("cc") or {}
    cc_totals = cc.get("totals") or {}
    cc_to_date = cc.get("email_campaigns_to_date") or {}
    if dq.get("cc") == "live" and (cc.get("campaigns") or cc_totals.get("sent") or cc_to_date.get("sends")):
        n_camp = len(cc.get("campaigns") or [])
        sent = cc_totals.get("sent", 0)
        opens = cc_totals.get("opens", 0)
        stats = [{"label": "Targeted campaigns featuring your property this period", "value": _fmt_n(n_camp)}]
        if sent:
            stats += [
                {"label": "Buyers & investors reached this period", "value": _fmt_n(sent)},
                {"label": "Opened your property's e-mail", "value": f"{_fmt_n(opens)} ({round(opens / sent * 100, 1)}%)" if sent else _fmt_n(opens)},
            ]
        elif not cc_to_date.get("sends"):
            stats.append({"label": "Status", "value": "Campaigns staged — results post after send"})
        # Cumulative reach from campaigns sent BEFORE this window (e.g. the
        # original just-listed announcement) -- kept separate from the
        # period totals above so it never masquerades as this period's
        # activity (see collect.py CCAdapter period-scoping).
        if cc_to_date.get("sends"):
            stats.append({
                "label": "Additional reach from prior campaigns (total since listing)",
                "value": _fmt_n(cc_to_date["sends"]),
            })
        channels.append({
            "key": "email", "icon": "mail",
            "name": "MCG Private Buyer Network",
            "tagline": "Direct e-mail program to MCG's curated buyer and investor lists",
            "stats": stats,
        })
        combined += int(sent or 0)

    idx = src.get("idx") or {}
    if dq.get("idx") == "live" and idx.get("leads"):
        channels.append({
            "key": "leads", "icon": "target",
            "name": "MCG Buyer Lead Pipeline",
            "tagline": "Prospective buyers captured and worked by your MCG team",
            "stats": [{"label": "Active buyer leads engaged this period", "value": _fmt_n(idx.get("leads", 0))}],
        })

    tawk = src.get("tawk") or {}
    if dq.get("tawk") == "live" and (tawk.get("chats") or tawk.get("inquiries_about_listing")):
        channels.append({
            "key": "chat", "icon": "chat",
            "name": "Live Buyer Concierge",
            "tagline": "Real-time website chat, answered for your listing",
            "stats": [
                {"label": "Live conversations", "value": _fmt_n(tawk.get("chats", 0))},
                {"label": "Inquiries about your property", "value": _fmt_n(tawk.get("inquiries_about_listing", 0))},
            ],
        })

    return {
        "available": len(channels) > 0,
        "channels": channels,
        "channel_count": len(channels),
        "combined_display": _fmt_n(combined),
        "footnote": ("Combined marketing touchpoints across all active MCG channels. "
                     "Website figures reflect this reporting period; syndication and "
                     "marketplace counters accrue from your listing date."),
    }


def build_stats(metrics: dict, dq: dict) -> list[dict]:
    src = metrics.get("sources", {})
    idx = src.get("idx", {})
    portals = src.get("portals", {})
    cc = src.get("cc", {})
    tawk = src.get("tawk", {})
    trend = metrics.get("trend", {})

    idx_missing = dq.get("idx") == "missing"
    portals_missing = dq.get("portals") == "missing"
    cc_missing = dq.get("cc") == "missing"
    tawk_missing = dq.get("tawk") == "missing"

    portal_views_total = sum(v.get("views", 0) for v in portals.values()) if not portals_missing else 0

    views_available = not (idx_missing and portals_missing)
    total_views = (0 if idx_missing else idx.get("views", 0)) + (0 if portals_missing else portal_views_total)
    delta_pct = trend.get("delta_views_pct", 0.0)

    idx_leads = 0 if idx_missing else idx.get("leads", 0)
    portal_leads = 0 if portals_missing else sum(v.get("leads", 0) for v in portals.values())
    chat_inquiries = 0 if tawk_missing else tawk.get("inquiries_about_listing", 0)
    inquiries_available = not (idx_missing and portals_missing and tawk_missing)
    total_inquiries = idx_leads + portal_leads + chat_inquiries

    showings_count = len(metrics.get("showings", []))

    email_sent = cc.get("totals", {}).get("sent", 0) if not cc_missing else 0
    email_opens = cc.get("totals", {}).get("opens", 0) if not cc_missing else 0
    email_open_rate = (email_opens / email_sent * 100) if email_sent else 0.0

    stats = [
        {
            "key": "views",
            "label": "Total views",
            "available": views_available,
            "value_display": fmt_int(total_views) if views_available else "--",
            "sub": (f"{fmt_int(idx.get('views', 0))} website + {fmt_int(portal_views_total)} "
                    f"syndication views" if views_available else "Sources unavailable this period"),
            "delta_display": fmt_pct(delta_pct, signed=True) if views_available else None,
            "delta_dir": "up" if delta_pct >= 0 else "down",
        },
        {
            "key": "inquiries",
            "label": "Inquiries",
            "available": inquiries_available,
            "value_display": fmt_int(total_inquiries) if inquiries_available else "--",
            "sub": (f"{fmt_int(chat_inquiries)} chat, {fmt_int(idx_leads + portal_leads)} lead forms"
                    if inquiries_available else "Sources unavailable this period"),
            "delta_display": None,
            "delta_dir": None,
        },
        {
            "key": "showings",
            "label": "Showings",
            "available": True,
            "value_display": fmt_int(showings_count),
            "sub": "Logged this period" if showings_count else "None logged this period",
            "delta_display": None,
            "delta_dir": None,
        },
        {
            "key": "email",
            "label": "Email reach",
            "available": not cc_missing,
            "value_display": fmt_int(email_sent) if not cc_missing else "--",
            "sub": (f"{email_open_rate:.1f}% open rate" if not cc_missing and email_sent
                    else "Sources unavailable this period"),
            "delta_display": None,
            "delta_dir": None,
        },
    ]
    return stats, total_views, total_inquiries, showings_count


def build_summary_tiles(metrics: dict, dq: dict, homes_exposure: dict | None,
                         crexi_exposure: dict | None, total_views: int,
                         total_inquiries: int, showings_count: int) -> list[dict]:
    """Homes.com-style stat-tile grid for the Summary section: each channel
    gets its own tile (never double-counted into another tile), plus one
    clearly-labeled combined headline. Order matches the target layout."""
    src = metrics.get("sources", {})
    ga4 = src.get("ga4", {})
    cc = src.get("cc", {})
    ga4_missing = dq.get("ga4") == "missing"
    cc_missing = dq.get("cc") == "missing"

    email_sent = cc.get("totals", {}).get("sent", 0) if not cc_missing else 0
    email_opens = cc.get("totals", {}).get("opens", 0) if not cc_missing else 0
    email_open_rate = (email_opens / email_sent * 100) if email_sent else 0.0

    tiles = [
        {
            "key": "total_views",
            "label": "Total Marketing Views",
            "available": True,
            "highlight": True,
            "value_display": fmt_int(total_views),
            "sub": "Combined listing views across MCG's on-site listing widget and "
                   "syndication network placements this period",
        },
    ]

    if homes_exposure:
        tiles.append({
            "key": "display_ads", "label": "Display Ad Views", "available": True,
            "value_display": fmt_int(homes_exposure["display_ad_views"]),
            "sub": "Off-site ad impressions on partner publications",
        })
        tiles.append({
            "key": "top_of_search", "label": "Top-of-Search Placements", "available": True,
            "value_display": fmt_int(homes_exposure["top_of_search"]),
            "sub": "Times the listing ranked first in buyer searches",
        })
    elif crexi_exposure:
        tiles.append({
            "key": "display_ads", "label": "Marketplace Impressions", "available": True,
            "value_display": fmt_int(crexi_exposure["impressions"]),
            "sub": "Impressions across the commercial marketplace network",
        })
        grade = crexi_exposure.get("search_score_grade")
        tiles.append({
            "key": "top_of_search", "label": "MCG Placement Score", "available": crexi_exposure.get("search_score") is not None,
            "value_display": f"{crexi_exposure['search_score']}/100" if crexi_exposure.get("search_score") is not None else "--",
            "sub": grade or "Commercial marketplace visibility",
        })
    else:
        tiles.append({"key": "display_ads", "label": "Display Ad Views", "available": False,
                       "value_display": "--", "sub": "Unavailable this period"})
        tiles.append({"key": "top_of_search", "label": "Top-of-Search Placements", "available": False,
                       "value_display": "--", "sub": "Unavailable this period"})

    tiles.append({
        "key": "website_views", "label": "Website Views", "available": not ga4_missing,
        "value_display": fmt_int(ga4.get("pageviews", 0)) if not ga4_missing else "--",
        "sub": (f"{fmt_int(ga4.get('users', 0))} unique visitors" if not ga4_missing
                else "Unavailable this period"),
    })
    tiles.append({
        "key": "email_reach", "label": "Email Reach", "available": not cc_missing,
        "value_display": fmt_int(email_sent) if not cc_missing else "--",
        "sub": (f"{email_open_rate:.1f}% open rate" if not cc_missing and email_sent
                else "Unavailable this period"),
    })
    tiles.append({
        "key": "inquiries", "label": "Inquiries & Leads", "available": True,
        "value_display": fmt_int(total_inquiries),
        "sub": f"{showings_count} showing{'s' if showings_count != 1 else ''} logged this period",
    })

    if homes_exposure:
        tiles.append({
            "key": "saved", "label": "Saved / Favorites", "available": True,
            "value_display": fmt_int(homes_exposure["favorites"]),
            "sub": "Buyers who saved this listing",
        })
    elif crexi_exposure and crexi_exposure.get("funnel"):
        saved = next((f["value"] for f in crexi_exposure["funnel"] if f["label"] == "Saved property"), None)
        tiles.append({
            "key": "saved", "label": "Saved / Favorites", "available": saved is not None,
            "value_display": fmt_int(saved) if saved is not None else "--",
            "sub": "Buyers who saved this listing",
        })
    else:
        tiles.append({"key": "saved", "label": "Saved / Favorites", "available": False,
                       "value_display": "--", "sub": "Unavailable this period"})

    if homes_exposure and homes_exposure.get("matterport_views"):
        tiles.append({
            "key": "tour", "label": "3D Tour Views", "available": True,
            "value_display": fmt_int(homes_exposure["matterport_views"]),
            "sub": f"{fmt_int(homes_exposure['matterport_minutes'])} min. of tour time logged",
        })

    return tiles


def build_activity_feed(activity: list[dict], milestones: list[dict] | None) -> list[dict]:
    """Merges the activity timeline with portal milestones into one
    homes.com-style feed (icon + description + right-aligned date),
    newest first."""

    def milestone_phrase(event: str | None) -> str:
        if not event:
            return "Your listing reached a new milestone"
        e = event.strip()
        low = e.lower()
        if low.startswith("reached "):
            return f"Your listing {e[0].lower()}{e[1:]}"
        if low.startswith("now considered"):
            return f"Your listing is {e[0].lower()}{e[1:]}"
        if low.startswith("listed as"):
            return f"Your listing was {e[0].lower()}{e[1:]}"
        return f"Your listing {e[0].lower()}{e[1:]}"

    feed = []
    for a in activity or []:
        feed.append({
            "date": a.get("date"),
            "date_display": fmt_date_display(a.get("date")),
            "kind": "activity",
            "channel": a.get("channel", "activity"),
            "text": anonymize_text(a.get("desc", "")),
        })
    for m in milestones or []:
        feed.append({
            "date": m.get("date"),
            "date_display": m.get("date_display") or fmt_date_display(m.get("date")),
            "kind": "milestone",
            "channel": "milestone",
            "text": milestone_phrase(m.get("event")),
        })
    feed.sort(key=lambda x: x.get("date") or "", reverse=True)
    return feed


# ---------------------------------------------------------------------------
# Market Position -- exposure rank + price context + DOM framing, built
# ONLY from data we legitimately have. No external comps, no invented
# market averages: if cross-portfolio data is unavailable this renders
# with just the price-context and DOM pieces.
# ---------------------------------------------------------------------------
def _listing_period_total_views(metrics: dict) -> int:
    src = metrics.get("sources", {})
    idx_v = int((src.get("idx") or {}).get("views", 0) or 0)
    ga4_v = int((src.get("ga4") or {}).get("pageviews", 0) or 0)
    portal_v = sum(int((p or {}).get("views", 0) or 0) for p in (src.get("portals") or {}).values())
    return idx_v + ga4_v + portal_v


def load_portfolio_ranking(data_dir: Path, listings: list[dict], period_id: str) -> list[dict]:
    """Total views for every active listing that has a metrics.json for
    this exact period_id -- the same total_views figure the Views section
    itself reports, so the rank agrees with what's on the page. Listings
    with no metrics.json for this period (e.g. newly added, or this period
    hasn't been collected for them) are simply absent from the ranking
    rather than assumed to be zero."""
    ranking = []
    for l in listings:
        if l.get("status") != "active":
            continue
        mp = data_dir / l["slug"] / period_id / "metrics.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text())
        except (ValueError, OSError):
            continue
        ranking.append({"slug": l["slug"], "total_views": _listing_period_total_views(m)})
    return ranking


def build_market_position(listing: dict, metrics: dict, portfolio_ranking: list[dict] | None) -> dict:
    period_end = metrics.get("period", {}).get("end")

    # (a) exposure rank within the MCG portfolio for this period.
    rank = {"available": False}
    if portfolio_ranking and len(portfolio_ranking) > 1:
        ordered = sorted(portfolio_ranking, key=lambda r: -r["total_views"])
        position = next((i for i, r in enumerate(ordered) if r["slug"] == listing["slug"]), None)
        if position is not None:
            rank = {
                "available": True,
                "position": position + 1,
                "of": len(ordered),
                "top_tier": (position + 1) <= max(1, round(len(ordered) / 3)),
                "sentence": (
                    f"Ranks #{position + 1} of {len(ordered)} active MCG listings for "
                    f"buyer views this period."
                ),
            }

    # (b) price context -- $/sqft or $/acre when the fields exist, else the
    # plain listing price. Never invented: only computed from fields
    # actually present on the listing record.
    price = listing.get("price") or 0
    acreage = listing.get("acreage") or listing.get("lot_acres")
    sqft = listing.get("sqft") or listing.get("square_feet")
    price_context = {"available": False}
    if price and acreage:
        per_acre = price / acreage
        price_context = {
            "available": True, "metric": "per_acre",
            "value_display": f"${per_acre:,.0f}/acre",
            "sentence": f"Positioned at ${per_acre:,.0f} per acre ({fmt_price(listing)} on {acreage:g} acres).",
        }
    elif price and sqft:
        per_sqft = price / sqft
        price_context = {
            "available": True, "metric": "per_sqft",
            "value_display": f"${per_sqft:,.0f}/sqft",
            "sentence": f"Positioned at ${per_sqft:,.0f} per square foot ({fmt_price(listing)} on {sqft:,} sqft).",
        }
    elif price:
        price_context = {
            "available": True, "metric": "price",
            "value_display": fmt_price(listing),
            "sentence": f"Listed at {fmt_price(listing)}.",
        }

    # (c) days-on-market, positive framing.
    dom_days = days_between(listing.get("list_date"), period_end)
    dom = {"available": dom_days is not None, "days": dom_days}
    if dom_days is not None:
        dom["sentence"] = f"Day {dom_days} of a sustained exposure campaign for this listing."

    return {
        "available": rank["available"] or price_context["available"] or dom["available"],
        "rank": rank,
        "price": price_context,
        "dom": dom,
    }


# ---------------------------------------------------------------------------
# Monthly/quarterly "Month in review" -- weekly breakdown table view-model
# ---------------------------------------------------------------------------
def build_weekly_breakdown_view(metrics: dict) -> dict:
    rows_raw = metrics.get("weekly_breakdown") or []
    rows = []
    for r in rows_raw:
        homes = r.get("portals", {}).get("homes.com", {})
        crexi = r.get("portals", {}).get("crexi", {})
        loopnet = r.get("portals", {}).get("loopnet", {})
        rows.append({
            "week_id": r.get("week_id"),
            "range_display": (
                f"{fmt_date_short(r.get('start'))} – {fmt_date_short(r.get('end'))}"
                if r.get("start") and r.get("end") else r.get("week_id")
            ),
            "total_views": fmt_int(r.get("total_views", 0)),
            "total_leads": fmt_int(r.get("total_leads", 0)),
            "syndication_views": fmt_int((homes.get("views") or 0) + (crexi.get("views") or 0) + (loopnet.get("views") or 0))
                if any(v.get("views") is not None for v in (homes, crexi, loopnet)) else "--",
        })
    return {
        "available": bool(rows),
        "rows": rows,
        "missing_weeks": metrics.get("missing_weeks") or [],
    }


def build_view_model(listing: dict, metrics: dict, period_links: list[dict],
                      report_url: str, generated_display: str,
                      portfolio_ranking: list[dict] | None = None) -> dict:
    dq = metrics.get("data_quality", {})
    source_freshness = metrics.get("source_freshness", {})
    stats, total_views, total_inquiries, showings_count = build_stats(metrics, dq)

    market = metrics.get("market", {})
    comps, overpriced_note = build_comps(market.get("comps", []), listing.get("price", 0))
    comps = [{**c, "note": anonymize_text(c.get("note", ""))} for c in comps]
    overpriced_note = anonymize_text(overpriced_note)
    dom_gauge = build_dom_gauge(listing, market, metrics["period"]["end"])

    src = metrics.get("sources", {})

    if dq.get("idx") == "missing" and dq.get("portals") == "missing":
        views_chart = {"available": False, "reason": "missing"}
    else:
        views_chart = build_views_comparison_chart(
            metrics.get("trend", {}).get("prior_period", {}).get("total_views", 0),
            total_views,
        )
        views_chart["reason"] = None if views_chart["available"] else "empty"

    if dq.get("ga4") == "missing":
        traffic_chart = {"available": False, "reason": "missing"}
    else:
        traffic_chart = build_traffic_sources_chart(src.get("ga4", {}).get("top_sources", []))
        traffic_chart["reason"] = None if traffic_chart["available"] else "empty"

    if dq.get("cc") == "missing":
        email_chart = {"available": False, "reason": "missing"}
    else:
        email_chart = build_email_chart(src.get("cc", {}).get("campaigns", []))
        email_chart["reason"] = None if email_chart["available"] else "empty"

    # --- portal exposure (homes.com / Crexi) -- compute first so both the
    # merged traffic-sources chart and the summary tiles can use it. ---
    portals_raw = src.get("portals", {})
    homes_exposure = build_homes_exposure(portals_raw, source_freshness)
    crexi_exposure = build_crexi_exposure(portals_raw, source_freshness)
    exposure_available = bool(homes_exposure or crexi_exposure)
    channel_performance = build_channel_performance(src, dq, homes_exposure, crexi_exposure, portals_raw)

    # --- homes.com-mirror layout gate ---------------------------------
    # Applies to listings whose portals include homes.com data (i.e.
    # residential -- Crexi/commercial listings never set this and always
    # render the original report.html layout, per Cameron's brief: he's
    # approving this one template before commercial gets adapted).
    homes_mirror = bool(homes_exposure) and listing.get("type") == "residential"
    hm = build_homes_mirror(portals_raw.get("homes.com") or {}, homes_exposure) if homes_mirror else None

    traffic_merged = build_traffic_sources_merged(portals_raw, src.get("ga4", {}).get("top_sources", []))
    traffic_merged["reason"] = None if traffic_merged["available"] else ("missing" if (dq.get("ga4") == "missing" and dq.get("portals") == "missing") else "empty")

    charts = {
        "views_comparison": views_chart,
        "traffic_sources": traffic_chart,
        "traffic_sources_merged": traffic_merged,
        "email_engagement": email_chart,
        "dom_gauge": dom_gauge,
    }

    sample_sections = [k for k, v in dq.items() if v == "sample"]
    missing_sections = [k for k, v in dq.items() if v == "missing"]

    analytics_all_missing = all(dq.get(k) == "missing" for k in ("idx", "cc", "ga4", "portals"))

    activity = sorted(metrics.get("activity", []), key=lambda a: a.get("date", ""))
    activity = [{**a, "desc": anonymize_text(a.get("desc", ""))} for a in activity]
    showings = sorted(metrics.get("showings", []), key=lambda s: s.get("date", ""))
    showings = [{**s, "feedback": anonymize_text(s.get("feedback", ""))} for s in showings]

    summary_tiles = build_summary_tiles(metrics, dq, homes_exposure, crexi_exposure,
                                         total_views, total_inquiries, showings_count)
    activity_feed = build_activity_feed(activity, (homes_exposure or {}).get("milestones"))

    if homes_exposure:
        exposure_headline = {
            "value": fmt_int(homes_exposure["total_views"]),
            "label": "Total marketing views across MCG's national syndication partners",
        }
    elif crexi_exposure:
        exposure_headline = {
            "value": fmt_int(crexi_exposure["impressions"]),
            "label": ("Total impressions across MCG's commercial marketplace network"
                      if crexi_exposure["impressions_is_deep"]
                      else "Total page views across MCG's commercial marketplace network"),
        }
    else:
        exposure_headline = None

    # --- insights narrative: collect.py sometimes bakes a vendor name into
    # these free-text fields (see anonymize_text() docstring). Scrub before
    # they reach the template. ---
    insights_raw = metrics.get("insights", {})
    insights = {
        "summary": anonymize_text(insights_raw.get("summary", "")),
        "recommendations": [anonymize_text(r) for r in insights_raw.get("recommendations", [])],
        "next_period_plan": [anonymize_text(n) for n in insights_raw.get("next_period_plan", [])],
    }

    # --- hero / live-listing link / agent card ---
    listing_links = listing.get("links", {}) or {}
    hero_image = listing_links.get("hero_image")
    live_listing_url = listing_links.get("webflow_page") or listing_links.get("marketing_page") or listing_links.get("idx")
    seller_name = ((listing.get("seller") or {}).get("name")) or "the owner"

    show_views = bool(views_chart.get("available") or (homes_exposure or {}).get("daily_chart", {}).get("available"))
    show_reach = exposure_available
    show_buyermap = bool((homes_exposure or {}).get("visitor_map", {}).get("available"))

    hero_list_date = listing.get("list_date") or (homes_exposure or {}).get("listed_date")
    hero_dom = dom_gauge.get("listing_dom") if dom_gauge.get("available") else (homes_exposure or {}).get("days_on_market_portal")

    return {
        "listing": listing,
        "period": metrics["period"],
        "period_label": period_type_label(metrics["period"]["type"]),
        "period_range_display": f"{fmt_date_display(metrics['period']['start'])} – {fmt_date_display(metrics['period']['end'])}",
        "generated_display": generated_display,
        "period_links": period_links,
        "report_url": report_url,
        "hero": {
            "address": listing.get("address", ""),
            "price_display": fmt_price(listing),
            "beds": listing.get("beds"),
            "baths": listing.get("baths"),
            "type_display": type_display(listing.get("type", "")),
            "status_display": (listing.get("status") or "active").title(),
            "list_date_display": fmt_date_display(hero_list_date),
            "dom": hero_dom,
            "image_url": hero_image,
        },
        "agent": AGENT,
        # Final-approval-round agent card ("Message Cameron" button) --
        # mailto with a per-listing subject line, address-specific.
        "agent_mailto": (
            "mailto:Torabi@MasonCapitalGroup.com?subject="
            + urllib.parse.quote(f"Listing Intelligence — {listing.get('address', '')}")
        ),
        "live_listing_url": live_listing_url,
        "seller_name": seller_name,
        "quality": dq,
        "sample_sections": sample_sections,
        "missing_sections": missing_sections,
        "any_sample": bool(sample_sections),
        "stats": stats,
        "summary_tiles": summary_tiles,
        "activity": activity,
        "activity_feed": activity_feed,
        "showings": showings,
        "charts": charts,
        "market": {
            "positioning": anonymize_text(market.get("positioning", "")),
            "county": market.get("county", ""),
            "area_dom_days": market.get("area_dom_days"),
            "comps": comps,
            "overpriced_note": overpriced_note,
        },
        "analytics_all_missing": analytics_all_missing,
        "insights": insights,
        "mcg_proof": MCG_PROOF,
        "syndication_blurb": SYNDICATION_BLURB,
        "exposure_available": exposure_available,
        "exposure_headline": exposure_headline,
        "homes_exposure": homes_exposure,
        "crexi_exposure": crexi_exposure,
        "show_views": show_views,
        "show_reach": show_reach,
        "show_buyermap": show_buyermap,
        "homes_mirror": homes_mirror,
        "hero_card_style": HERO_CARD_STYLE,
        "channel_performance": channel_performance,
        "hm": hm,
        "source_freshness": source_freshness,
        "market_position": build_market_position(listing, metrics, portfolio_ranking),
        "is_rollup": metrics.get("period", {}).get("type") != "weekly",
        "weekly_breakdown": build_weekly_breakdown_view(metrics),
        "period_activity": metrics.get("period_activity", {}),
    }


# ---------------------------------------------------------------------------
# insights.json "health" + "headline" -- the hub dashboard's per-report
# health chips and stats line. Published alongside the private talking
# points below (same file, same private/never-seller-facing status), but
# these two blocks are pure data (no strategy/rationale text) so the hub
# can render them directly.
# ---------------------------------------------------------------------------
def _health_from_freshness(source_freshness: dict) -> list[dict]:
    """metrics.json's source_freshness, reshaped for the hub: a LIST (not
    a dict keyed by label) because CHANNEL_LABELS is deliberately
    many-to-one -- crexi and loopnet both read "MCG's commercial
    marketplace network" -- so keying by label would silently drop one of
    them. Each row carries the anonymized display label (never a raw
    vendor name, even in this private file) plus a stable machine 'key'
    for the hub to match/filter/sort on, alongside the original
    as_of/status (and any extra fields, e.g. clarity's coverage_days)."""
    rows = []
    for key, entry in (source_freshness or {}).items():
        row = {"key": key, "label": CHANNEL_LABELS.get(key, key.replace("_", " ").replace(".", " ").title())}
        row.update(entry or {})
        rows.append(row)
    return rows


def build_window_label(period: dict) -> str:
    """Human-readable period window for the hub's stats line, e.g. 'Week
    of Jul 27 – Aug 2', 'July 2026', 'Q3 2026'."""
    ptype = period.get("type")
    start, end = period.get("start"), period.get("end")
    if ptype == "weekly" and start and end:
        return f"Week of {fmt_date_short(start)} – {fmt_date_short(end)}"
    if ptype == "monthly" and start:
        try:
            return dt.date.fromisoformat(start).strftime("%B %Y")
        except ValueError:
            pass
    if ptype == "quarterly":
        pid = period.get("id", "") or ""
        if "-Q" in pid:
            y, q = pid.split("-Q")
            return f"Q{q} {y}"
    return period.get("id", "") or ""


def _headline_from_vm(vm: dict, metrics: dict) -> dict:
    """total_views/total_leads reuse the exact same stats the seller-facing
    report shows (vm['stats'], built by build_stats()) so the hub's number
    never disagrees with the report's. page_engagement prefers GA4's
    average session duration; when GA4 is unavailable it falls back to
    Clarity's supplemental session count (clearly marked as such, never
    presented as a primary engagement metric) -- best-available, no
    fabrication either way."""
    stats_by_key = {s.get("key"): s for s in vm.get("stats", [])}
    views_stat = stats_by_key.get("views", {})
    inquiries_stat = stats_by_key.get("inquiries", {})
    total_views = views_stat.get("value_display", "--") if views_stat.get("available") else "--"
    total_leads = inquiries_stat.get("value_display", "--") if inquiries_stat.get("available") else "--"

    src = metrics.get("sources", {})
    dq = metrics.get("data_quality", {})
    ga4 = src.get("ga4") or {}
    clarity = src.get("clarity") or {}

    if dq.get("ga4") in ("live", "sample") and ga4.get("avg_engagement_s"):
        eng = ga4["avg_engagement_s"]
        page_engagement = {
            "metric": "avg_engagement_seconds",
            "value_display": f"{int(eng // 60)}m {int(eng % 60)}s",
            "available": True,
        }
    elif dq.get("clarity") in ("live", "sample") and clarity.get("sessions"):
        page_engagement = {
            "metric": "clarity_sessions_supplemental",
            "value_display": f"{fmt_int(clarity.get('sessions', 0))} sessions ({clarity.get('coverage_days', 0)}d)",
            "available": True,
        }
    else:
        page_engagement = {"metric": None, "value_display": "--", "available": False}

    return {
        "total_views": total_views,
        "total_leads": total_leads,
        "page_engagement": page_engagement,
        "window_label": build_window_label(metrics.get("period", {})),
    }


# ---------------------------------------------------------------------------
# Private insights (Cameron's eyes only -- see Listing Reports hub's
# "For your call — private" card in mcg-marketing-hub. Published alongside
# the seller-facing report/flyer at the same period URL so the hub can fetch
# it server-side, but it is NEVER linked from seller-facing HTML and NEVER
# rendered into report.html/flyer.html.
# ---------------------------------------------------------------------------
def build_insights_private(listing: dict, metrics: dict, vm: dict) -> dict:
    """3 talking-point bullets (biggest number, trend, next-step framing) +
    a pricing_flag (true when DOM exceeds the county average and views are
    trending down), each with a one-line rationale."""
    stats = vm.get("stats", [])
    dom_gauge = vm.get("charts", {}).get("dom_gauge", {})
    trend = metrics.get("trend", {})
    delta_pct = trend.get("delta_views_pct", 0.0)

    views_stat = next((s for s in stats if s.get("key") == "views"), None)
    if views_stat and views_stat.get("available"):
        biggest_number = f"{views_stat['value_display']} total views this period -- {views_stat['sub']}."
    else:
        biggest_number = "No view data available this period -- confirm the listing is still syndicating."

    if delta_pct > 0:
        trend_point = f"Views trending up {delta_pct:.1f}% vs. the prior period."
    elif delta_pct < 0:
        trend_point = f"Views trending down {abs(delta_pct):.1f}% vs. the prior period."
    else:
        trend_point = "Views roughly flat vs. the prior period."

    showings_count = len(metrics.get("showings", []))
    if dom_gauge.get("available") and not dom_gauge.get("pacing_good"):
        next_step = (f"On the market {dom_gauge['listing_dom']} days vs. a "
                     f"{dom_gauge['area_dom']}-day {dom_gauge.get('county', 'county')} average -- "
                     "worth a pricing/positioning check-in call with the seller.")
    elif showings_count:
        next_step = f"{showings_count} showing{'s' if showings_count != 1 else ''} logged -- follow up on buyer feedback with the seller."
    else:
        next_step = "Keep the seller warm with a call this week; no urgent flags this period."

    pricing_flag = bool(dom_gauge.get("available") and not dom_gauge.get("pacing_good") and delta_pct < 0)
    pricing_reason = None
    if pricing_flag:
        pricing_reason = (f"Days on market ({dom_gauge['listing_dom']}) exceeds the "
                           f"{dom_gauge.get('county', 'county')} average ({dom_gauge['area_dom']} days) "
                           f"and views are trending down {abs(delta_pct):.1f}% -- consider a pricing conversation.")

    return {
        "slug": listing.get("slug"),
        "period_id": metrics["period"]["id"],
        "talking_points": [biggest_number, trend_point, next_step],
        "pricing_flag": pricing_flag,
        "pricing_flag_reason": pricing_reason,
        "health": _health_from_freshness(metrics.get("source_freshness", {})),
        "headline": _headline_from_vm(vm, metrics),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Chromium / PDF
# ---------------------------------------------------------------------------
def find_chromium(explicit: str | None) -> str | None:
    if explicit:
        p = Path(explicit)
        return str(p) if p.exists() else None
    for cand in CHROMIUM_CANDIDATES:
        if Path(cand).exists():
            return cand
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render_pdf(chromium_bin: str, html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        chromium_bin,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # Force every hostname to resolve to 0.0.0.0 so third-party CDN
        # requests (the homes.com display-ad logo CDN referenced by the
        # publications grid, the Google Fonts CDN) fail fast instead of
        # hanging/retrying for the page-load timeout. file:// and data: URIs
        # never touch DNS, so this has no effect on them -- the MCG logo,
        # embedded as a data: URI in the header/footer (same asset as the
        # live listing pages), still renders. A blanket
        # "--blink-settings=imagesEnabled=false" (the prior approach) would
        # also have blocked that local logo image.
        "--host-resolver-rules=MAP * 0.0.0.0",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--virtual-time-budget=10000",
        f"file://{html_path.resolve()}",
    ]
    try:
        # 150s (was 90s): the homes.com-portal listings' publication-logo
        # grid (~40+ external <img> hosts, all now resolving to 0.0.0.0 per
        # the flags above) takes a real-but-bounded ~100s wall-clock to fail
        # out on this sandbox's network path -- 90s clipped it.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=150)
    except Exception as exc:  # noqa: BLE001
        return False, f"chromium invocation failed: {exc}"
    if result.returncode != 0 or not pdf_path.exists():
        return False, f"chromium exited {result.returncode}: {result.stderr[-500:]}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_listings(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data.get("listings", data if isinstance(data, list) else [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Render seller activity reports, flyers, and PDFs.")
    ap.add_argument("--period-id", required=True, help="e.g. 2026-W29, 2026-07, 2026-Q3")
    ap.add_argument("--slug", default="all", help="listing slug, or 'all'")
    ap.add_argument("--outdir", default=str(REPO_ROOT / "docs" / "reports"),
                     help="root output dir for rendered report pages")
    ap.add_argument("--listings", default=str(REPO_ROOT / "config" / "listings.json"),
                     help="path to listings.json (or listings.sample.json for testing)")
    ap.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    ap.add_argument("--templates-dir", default=str(REPO_ROOT / "templates"))
    ap.add_argument("--flyers-dir", default=str(REPO_ROOT / "out" / "flyers"))
    ap.add_argument("--base-url", default="https://torabic.github.io/mcg-listings",
                     help="root URL where reports/ is served, used for flyer CTA links")
    ap.add_argument("--pdf", dest="pdf", action="store_true", default=True)
    ap.add_argument("--no-pdf", dest="pdf", action="store_false")
    ap.add_argument("--pdf-dir", default=str(REPO_ROOT / "out" / "pdfs"))
    ap.add_argument("--chromium-bin", default=None)
    args = ap.parse_args()

    listings_path = Path(args.listings)
    if not listings_path.exists():
        print(f"ERROR: listings file not found: {listings_path}", file=sys.stderr)
        return 2
    listings = load_listings(listings_path)
    listings_by_slug = {l["slug"]: l for l in listings}

    if args.slug == "all":
        target_slugs = list(listings_by_slug.keys())
    else:
        if args.slug not in listings_by_slug:
            print(f"ERROR: slug '{args.slug}' not found in {listings_path}", file=sys.stderr)
            return 2
        target_slugs = [args.slug]

    data_dir = Path(args.data_dir)
    outdir = Path(args.outdir)
    flyers_dir = Path(args.flyers_dir)
    pdf_dir = Path(args.pdf_dir)
    templates_dir = Path(args.templates_dir)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    report_tmpl = env.get_template("report.html")
    flyer_tmpl = env.get_template("flyer.html")

    chromium_bin = find_chromium(args.chromium_bin) if args.pdf else None
    if args.pdf and not chromium_bin:
        print("WARNING: chromium binary not found; PDF generation will be skipped. "
              "Pass --chromium-bin or install at /opt/pw-browsers.", file=sys.stderr)

    generated_display = dt.datetime.now().strftime("%B %-d, %Y")

    # Cross-portfolio exposure ranking for the Market Position section --
    # computed once per run over every active listing's metrics.json for
    # this exact period_id (not just the slugs being rendered this
    # invocation), so a single-slug `--slug` run still ranks correctly
    # against the full portfolio.
    portfolio_ranking = load_portfolio_ranking(data_dir, listings, args.period_id)

    results = []
    for slug in target_slugs:
        listing = listings_by_slug[slug]
        token = listing.get("report_token", "notoken")
        slug_token = f"{slug}-{token}"
        metrics_path = data_dir / slug / args.period_id / "metrics.json"

        if not metrics_path.exists():
            results.append({"slug": slug, "status": "SKIPPED (no metrics.json)", "path": str(metrics_path)})
            continue

        metrics = json.loads(metrics_path.read_text())
        period_links = build_period_links(data_dir, slug, metrics["period"], outdir, slug_token)

        report_url = f"{args.base_url.rstrip('/')}/reports/{slug_token}/{args.period_id}/index.html"
        vm = build_view_model(listing, metrics, period_links, report_url, generated_display, portfolio_ranking)

        # --- render report page ---
        html = report_tmpl.render(**vm)
        period_dir = outdir / slug_token / args.period_id
        period_dir.mkdir(parents=True, exist_ok=True)
        index_path = period_dir / "index.html"
        index_path.write_text(html, encoding="utf-8")

        # --- latest/ copy ---
        latest_dir = outdir / slug_token / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        (latest_dir / "index.html").write_text(html, encoding="utf-8")

        # --- flyer (out/flyers/, back-compat for bin/cc_flyers.py) ---
        flyer_html = flyer_tmpl.render(**vm)
        flyers_dir.mkdir(parents=True, exist_ok=True)
        flyer_path = flyers_dir / f"{slug}-{args.period_id}.html"
        flyer_path.write_text(flyer_html, encoding="utf-8")

        # --- flyer.html + insights.json published into the SAME period
        # folder as index.html, so the Listing Reports hub (mcg-marketing-hub
        # app.py / reports_hub.py) can fetch both by simple relative path off
        # the public report URL. insights.json is fetched server-side only by
        # the hub for Cameron's private "For your call" card -- it is never
        # linked from index.html/flyer.html and carries no seller-facing copy.
        (period_dir / "flyer.html").write_text(flyer_html, encoding="utf-8")
        (latest_dir / "flyer.html").write_text(flyer_html, encoding="utf-8")
        insights_private = build_insights_private(listing, metrics, vm)
        insights_json = json.dumps(insights_private, indent=2)
        (period_dir / "insights.json").write_text(insights_json, encoding="utf-8")
        (latest_dir / "insights.json").write_text(insights_json, encoding="utf-8")

        # --- pdf ---
        pdf_status = "skipped (no chromium)"
        if args.pdf and chromium_bin:
            pdf_out = period_dir / "report.pdf"
            ok, msg = render_pdf(chromium_bin, index_path, pdf_out)
            if ok:
                pdf_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdf_out, pdf_dir / f"{slug}-{args.period_id}.pdf")
                pdf_status = f"ok ({pdf_out.stat().st_size:,} bytes)"
            else:
                pdf_status = f"FAILED: {msg}"

        results.append({
            "slug": slug,
            "status": "OK",
            "report_html": str(index_path),
            "report_bytes": index_path.stat().st_size,
            "flyer_html": str(flyer_path),
            "flyer_bytes": flyer_path.stat().st_size,
            "pdf": pdf_status,
        })

    print(json.dumps({"period_id": args.period_id, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
