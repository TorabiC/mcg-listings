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
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--virtual-time-budget=10000",
        f"file://{html_path.resolve()}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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
