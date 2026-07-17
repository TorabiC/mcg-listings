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
import re
import shutil
import subprocess
import sys
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


# Fixed categorical order per brand palette. Always paired with direct labels
# and a legend (never color-alone identification) -- see dataviz skill.
CATEGORICAL_COLORS = ["#1B2A4A", "#A6192E", "#C9A227", "#5C7290", "#8A8580"]


def build_traffic_sources_chart(top_sources: list[dict]) -> dict:
    capped = cap_top_n_with_other(top_sources, "users", "source", 4)
    max_val = max((c.get("users", 0) for c in capped), default=0) or 1
    bars = []
    for i, c in enumerate(capped):
        bars.append({
            "label": c.get("source", "unknown").title(),
            "value": c.get("users", 0),
            "pct": round((c.get("users", 0) / max_val) * 100, 1),
            "color": CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)],
        })
    return {"bars": bars, "available": bool(bars)}


def build_email_chart(campaigns: list[dict]) -> dict:
    max_sent = max((c.get("sent", 0) for c in campaigns), default=0) or 1
    rows = []
    for i, c in enumerate(campaigns):
        sent = c.get("sent", 0)
        opens = c.get("opens", 0)
        rows.append({
            "name": c.get("name", "Campaign"),
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


def build_homes_exposure(portals: dict) -> dict | None:
    homes = portals.get("homes.com") or {}
    summary = homes.get("summary")
    if not summary:
        return None

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
    }


def build_crexi_exposure(portals: dict) -> dict | None:
    crexi = portals.get("crexi") or {}
    if crexi.get("search_score") is None and not crexi.get("page_views"):
        return None

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
    portal_names = ", ".join(k for k, v in portals.items() if v.get("views", 0) > 0)

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
            "sub": (f"{fmt_int(idx.get('views', 0))} IDX + {fmt_int(portal_views_total)} "
                    f"{portal_names or 'portal'}" if views_available else "Sources unavailable this period"),
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
    return stats, total_views


def build_view_model(listing: dict, metrics: dict, period_links: list[dict],
                      report_url: str, generated_display: str) -> dict:
    dq = metrics.get("data_quality", {})
    stats, total_views = build_stats(metrics, dq)

    market = metrics.get("market", {})
    comps, overpriced_note = build_comps(market.get("comps", []), listing.get("price", 0))
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

    charts = {
        "views_comparison": views_chart,
        "traffic_sources": traffic_chart,
        "email_engagement": email_chart,
        "dom_gauge": dom_gauge,
    }

    sample_sections = [k for k, v in dq.items() if v == "sample"]
    missing_sections = [k for k, v in dq.items() if v == "missing"]

    analytics_all_missing = all(dq.get(k) == "missing" for k in ("idx", "cc", "ga4", "portals"))

    activity = sorted(metrics.get("activity", []), key=lambda a: a.get("date", ""))
    showings = sorted(metrics.get("showings", []), key=lambda s: s.get("date", ""))

    # --- v2: portal exposure (homes.com / Crexi) ---
    portals_raw = src.get("portals", {})
    homes_exposure = build_homes_exposure(portals_raw)
    crexi_exposure = build_crexi_exposure(portals_raw)
    exposure_available = bool(homes_exposure or crexi_exposure)
    if homes_exposure:
        exposure_headline = {
            "value": fmt_int(homes_exposure["total_views"]),
            "label": "Total views on homes.com",
            "source": "homes.com",
        }
    elif crexi_exposure:
        exposure_headline = {
            "value": fmt_int(crexi_exposure["impressions"]),
            "label": "Total impressions on Crexi" if crexi_exposure["impressions_is_deep"] else "Total page views on Crexi",
            "source": "crexi",
        }
    else:
        exposure_headline = None

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
        },
        "quality": dq,
        "sample_sections": sample_sections,
        "missing_sections": missing_sections,
        "any_sample": bool(sample_sections),
        "stats": stats,
        "activity": activity,
        "showings": showings,
        "charts": charts,
        "market": {
            "positioning": market.get("positioning", ""),
            "county": market.get("county", ""),
            "area_dom_days": market.get("area_dom_days"),
            "comps": comps,
            "overpriced_note": overpriced_note,
        },
        "analytics_all_missing": analytics_all_missing,
        "insights": metrics.get("insights", {}),
        "mcg_proof": MCG_PROOF,
        "exposure_available": exposure_available,
        "exposure_headline": exposure_headline,
        "homes_exposure": homes_exposure,
        "crexi_exposure": crexi_exposure,
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
        # This sandbox's egress policy blocks third-party CDN hosts (e.g. the
        # homes.com display-ad logo CDN referenced by the publications
        # grid), so those <img> requests would otherwise hang/retry for the
        # full page-load timeout on every render. Disabling image loading
        # only for this internal PDF render keeps generation fast and
        # deterministic; it has no effect on the live HTML page, which
        # loads normally in a real browser with unrestricted internet
        # access.
        "--blink-settings=imagesEnabled=false",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--virtual-time-budget=10000",
        f"file://{html_path.resolve()}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
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
        vm = build_view_model(listing, metrics, period_links, report_url, generated_display)

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

        # --- flyer ---
        flyer_html = flyer_tmpl.render(**vm)
        flyers_dir.mkdir(parents=True, exist_ok=True)
        flyer_path = flyers_dir / f"{slug}-{args.period_id}.html"
        flyer_path.write_text(flyer_html, encoding="utf-8")

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
