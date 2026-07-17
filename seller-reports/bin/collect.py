#!/usr/bin/env python3
"""
collect.py -- MCG Seller Activity Report System, data collector.

Reads config/listings.json + config/sources.json + config/market-nwa.json,
pulls per-listing activity from each configured source (IDX Broker,
Constant Contact, GA4, tawk.to, manual portal intake), and writes
data/<slug>/<period_id>/metrics.json for each active listing, matching the
metrics.json contract in SPEC.md exactly.

Dependencies: Python 3 stdlib + `requests`. GA4's real (non-sample,
non-missing) token exchange additionally needs the `cryptography` package
to RS256-sign the service-account JWT -- that import is optional and only
touched when GA4 creds are actually present; everything else here runs on
stdlib + requests alone.

Usage:
    python3 bin/collect.py --period weekly|monthly|quarterly \
        [--period-id 2026-W29] [--slug all|<slug>] [--sample]

Missing/disabled sources never raise: they resolve to zeroed blocks with
data_quality "missing". --sample produces deterministic (seeded on
slug+period) demo numbers scaled by property type/price, data_quality
"sample".
"""
import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover - requests is a stated dependency
    requests = None

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
INTAKE_DIR = ROOT / "intake"
DATA_DIR = ROOT / "data"

HTTP_TIMEOUT = 15


# --------------------------------------------------------------------------
# Period helpers
# --------------------------------------------------------------------------

def _iso_week_bounds(d):
    """Return (monday, sunday) date objects for the ISO week containing d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def period_from_anchor(period_type, anchor):
    """Compute (period_id, start, end) for the period containing `anchor`.

    Weekly periods are Mon-Sun. The anchor used for a default (no
    --period-id) run is "the day before the run" per SPEC.md, so a run
    executed any day this week still reports on this Mon-Sun week.
    """
    if period_type == "weekly":
        monday, sunday = _iso_week_bounds(anchor)
        iso_year, iso_week, _ = monday.isocalendar()
        return f"{iso_year}-W{iso_week:02d}", monday, sunday
    if period_type == "monthly":
        start = anchor.replace(day=1)
        end = _month_end(start)
        return anchor.strftime("%Y-%m"), start, end
    if period_type == "quarterly":
        q = (anchor.month - 1) // 3 + 1
        start_month = (q - 1) * 3 + 1
        start = date(anchor.year, start_month, 1)
        end = _quarter_end(start)
        return f"{anchor.year}-Q{q}", start, end
    raise ValueError(f"unknown period type {period_type!r}")


def _month_end(start):
    if start.month == 12:
        return date(start.year, 12, 31)
    return date(start.year, start.month + 1, 1) - timedelta(days=1)


def _quarter_end(start):
    end_month = start.month + 2
    if end_month == 12:
        return date(start.year, 12, 31)
    return date(start.year, end_month + 1, 1) - timedelta(days=1)


def period_from_id(period_type, period_id):
    """Compute (start, end) date objects from an explicit period_id string."""
    if period_type == "weekly":
        iso_year_s, wk_s = period_id.split("-W")
        monday = date.fromisocalendar(int(iso_year_s), int(wk_s), 1)
        return monday, monday + timedelta(days=6)
    if period_type == "monthly":
        y, m = period_id.split("-")
        start = date(int(y), int(m), 1)
        return start, _month_end(start)
    if period_type == "quarterly":
        y, q = period_id.split("-Q")
        start_month = (int(q) - 1) * 3 + 1
        start = date(int(y), start_month, 1)
        return start, _quarter_end(start)
    raise ValueError(f"unknown period type {period_type!r}")


def prior_period_id(period_type, period_id, start):
    """period_id of the period immediately preceding the given one."""
    if period_type == "weekly":
        prior_anchor = start - timedelta(days=1)  # the previous Sunday
        pid, _, _ = period_from_anchor("weekly", prior_anchor)
        return pid
    if period_type == "monthly":
        y, m = (int(x) for x in period_id.split("-"))
        return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    if period_type == "quarterly":
        y_s, q_s = period_id.split("-Q")
        y, q = int(y_s), int(q_s)
        return f"{y - 1}-Q4" if q == 1 else f"{y}-Q{q - 1}"
    raise ValueError(f"unknown period type {period_type!r}")


# --------------------------------------------------------------------------
# Config / IO helpers
# --------------------------------------------------------------------------

def load_json(path, default=None):
    if not path.exists():
        return default
    with open(path, "r") as f:
        return json.load(f)


def load_listings():
    cfg = load_json(CONFIG_DIR / "listings.json", {"listings": []})
    return cfg.get("listings", [])


def load_sources_cfg():
    return load_json(CONFIG_DIR / "sources.json", {})


def load_market_cfg():
    return load_json(CONFIG_DIR / "market-nwa.json", {})


def resolve_credential(slot):
    """A credential_env_or_path value is either an env var name or a file
    path. Try env var first (exact name), then treat as a path -- return
    the secret string, or None if neither resolves to something real."""
    if not slot:
        return None
    env_val = os.environ.get(slot)
    if env_val:
        return env_val
    p = Path(slot)
    if not p.is_absolute():
        p = ROOT / slot
    if p.exists():
        try:
            return p.read_text().strip()
        except OSError:
            return None
    return None


# --------------------------------------------------------------------------
# Deterministic sample RNG
# --------------------------------------------------------------------------

def sample_rng(slug, period_id, salt=""):
    seed_str = f"{slug}|{period_id}|{salt}"
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed_int)


def type_scale_factor(listing):
    """Scale sample volume by property type/price so a $3.8M land parcel or
    a commercial lease doesn't get the same traffic as a $650K house."""
    ltype = listing.get("type")
    price_type = listing.get("price_type", "sale")
    price = listing.get("price") or 0

    if ltype == "residential":
        if price < 500_000:
            return 1.3
        if price < 700_000:
            return 1.0
        if price < 1_000_000:
            return 0.8
        return 0.55
    if ltype == "land":
        return 0.4 if price < 2_500_000 else 0.25
    if ltype == "commercial":
        if price_type == "lease_monthly":
            return 0.22
        if price >= 1_000_000:
            return 0.2
        return 0.3
    return 0.5


PERIOD_LENGTH_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91}


def period_scale(period_type):
    return PERIOD_LENGTH_DAYS.get(period_type, 7) / 7.0


# --------------------------------------------------------------------------
# Source adapters
# --------------------------------------------------------------------------

class SourceUnavailable(Exception):
    """Raised by a .fetch_live() when creds are missing/invalid or the API
    call fails. Callers always catch this (and any other Exception) and
    fall back to zeros + data_quality 'missing' -- a live source must never
    crash the run."""


class IdxAdapter:
    key = "idx_broker"
    block = "idx"

    def zeros(self):
        return {"views": 0, "leads": 0, "saved_searches_matching": 0, "favorites": 0}

    def is_configured(self, sources_cfg):
        cfg = sources_cfg.get("idx_broker", {})
        return bool(cfg.get("enabled")) and bool(resolve_credential(cfg.get("credential_env_or_path")))

    def fetch_live(self, listing, start, end, sources_cfg):
        if requests is None:
            raise SourceUnavailable("requests not installed")
        cfg = sources_cfg.get("idx_broker", {})
        accesskey = resolve_credential(cfg.get("credential_env_or_path"))
        if not accesskey:
            raise SourceUnavailable("no IDX Broker accesskey")
        mls_id = listing.get("mls_id")
        if not mls_id:
            raise SourceUnavailable("listing has no mls_id to look up in IDX Broker")

        headers = {"accesskey": accesskey, "outputtype": "json"}
        base = "https://api.idxbroker.com"

        # Verified against Cameron's live account 2026-07-17: per-listing
        # view counters are NOT exposed on this plan (/clients/listingstat
        # is 400, /clients/listing/{id} needs a partner ancillarykey, and
        # /clients/featured returns 204 for this account). Views come from
        # GA4/portals instead. What IS real here is /leads/lead: full lead
        # records. We pull all leads, filter to the reporting window by
        # subscribeDate/lastActive, and attribute to this listing when the
        # lead record mentions its MLS number, street, or slug.
        leads_resp = requests.get(
            f"{base}/leads/lead", headers=headers, timeout=HTTP_TIMEOUT,
        )
        leads_resp.raise_for_status()
        leads_data = leads_resp.json() or []
        if isinstance(leads_data, dict):
            leads_data = list(leads_data.values())
        if not isinstance(leads_data, list):
            leads_data = []

        def _in_window(lead):
            for f in ("subscribeDate", "lastActivityDate", "lastLoginDate"):
                v = str(lead.get(f, ""))[:10]
                try:
                    d = date.fromisoformat(v)
                    return start <= d <= end
                except ValueError:
                    continue
            return False

        street = listing["address"].split(",")[0].lower()
        slug_words = listing["slug"].replace("-", " ")
        mls_ids = {str(mls_id)} | {
            str(e.get("mls")) for e in listing.get("mls_entries", []) if e.get("mls")
        }

        def _mentions(lead):
            blob = json.dumps(lead).lower()
            return (
                any(m and m in blob for m in mls_ids)
                or street in blob
                or slug_words in blob
            )

        window_leads = [l for l in leads_data if _in_window(l)]
        listing_leads = [l for l in window_leads if _mentions(l)]

        return {
            "views": 0,  # not available on this IDX plan; GA4/portals cover views
            "leads": len(listing_leads),
            "saved_searches_matching": 0,
            "favorites": 0,
        }

    def fetch_sample(self, listing, period_type, period_id, rng, scale):
        base_views = rng.randint(35, 90) * scale * period_scale(period_type)
        views = max(0, round(base_views))
        leads = max(0, round(views * rng.uniform(0.02, 0.06)))
        return {
            "views": views,
            "leads": leads,
            "saved_searches_matching": max(0, round(rng.randint(0, 6) * scale)),
            "favorites": max(0, round(views * rng.uniform(0.03, 0.08))),
        }


class ConstantContactAdapter:
    key = "constant_contact"
    block = "cc"

    def zeros(self):
        return {"campaigns": [], "totals": {"sent": 0, "opens": 0, "clicks": 0}}

    def is_configured(self, sources_cfg):
        cfg = sources_cfg.get("constant_contact", {})
        return bool(cfg.get("enabled")) and bool(resolve_credential(cfg.get("credential_env_or_path")))

    def _refresh_access_token(self, token_blob):
        """token_blob is the JSON string {access_token, refresh_token,
        expires_at, client_id, client_secret}. Returns a fresh access
        token, refreshing via OAuth2 if expired."""
        try:
            creds = json.loads(token_blob)
        except (ValueError, TypeError):
            raise SourceUnavailable("CC credential is not the expected JSON blob")

        expires_at = creds.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) > datetime.now(timezone.utc):
                    return creds["access_token"]
            except ValueError:
                pass

        resp = requests.post(
            "https://authz.constantcontact.com/oauth2/default/v1/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": creds.get("refresh_token"),
                "client_id": creds.get("client_id"),
                "client_secret": creds.get("client_secret"),
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _match_campaigns(self, activities, listing):
        """Match CC campaign activities to a listing by slug/address
        substring in the campaign name -- CC has no first-class 'listing'
        field, so naming convention is the join key."""
        needles = [listing["slug"].replace("-", " "), listing["address"].split(",")[0]]
        matched = []
        for a in activities:
            name = (a.get("name") or "").lower()
            if any(n.lower() in name for n in needles if n):
                matched.append(a)
        return matched

    def fetch_live(self, listing, start, end, sources_cfg):
        if requests is None:
            raise SourceUnavailable("requests not installed")
        cfg = sources_cfg.get("constant_contact", {})
        token_blob = resolve_credential(cfg.get("credential_env_or_path"))
        if not token_blob:
            raise SourceUnavailable("no Constant Contact OAuth token")
        access_token = self._refresh_access_token(token_blob)
        headers = {"Authorization": f"Bearer {access_token}"}

        resp = requests.get(
            "https://api.cc.email/v3/emails/activities",
            headers=headers,
            params={"created_since": start.isoformat(), "created_before": (end + timedelta(days=1)).isoformat()},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        activities = (resp.json() or {}).get("campaign_activities", [])
        matched = self._match_campaigns(activities, listing)

        campaigns, totals = [], {"sent": 0, "opens": 0, "clicks": 0}
        for a in matched:
            stats_resp = requests.get(
                f"https://api.cc.email/v3/reports/campaign_tracking_stats/{a.get('campaign_activity_id')}",
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            stats_resp.raise_for_status()
            s = stats_resp.json() or {}
            sent, opens, clicks = int(s.get("sends", 0)), int(s.get("opens", 0)), int(s.get("clicks", 0))
            campaigns.append({
                "name": a.get("name", ""),
                "sent": sent,
                "opens": opens,
                "clicks": clicks,
                "open_rate": round(opens / sent, 4) if sent else 0.0,
            })
            totals["sent"] += sent
            totals["opens"] += opens
            totals["clicks"] += clicks
        return {"campaigns": campaigns, "totals": totals}

    def fetch_sample(self, listing, period_type, period_id, rng, scale):
        n_campaigns = rng.choices([0, 1, 2], weights=[0.35, 0.45, 0.20])[0]
        campaigns, totals = [], {"sent": 0, "opens": 0, "clicks": 0}
        names = [
            f"MCG Buyer Newsletter -- {listing['address'].split(',')[0]}",
            f"Just Listed: {listing['address'].split(',')[0]}",
            "MCG Investor Brief",
        ]
        for i in range(n_campaigns):
            sent = max(50, round(rng.randint(250, 650) * scale))
            open_rate = rng.uniform(0.18, 0.34)
            opens = round(sent * open_rate)
            clicks = round(opens * rng.uniform(0.08, 0.22))
            campaigns.append({
                "name": names[i % len(names)],
                "sent": sent,
                "opens": opens,
                "clicks": clicks,
                "open_rate": round(opens / sent, 4) if sent else 0.0,
            })
            totals["sent"] += sent
            totals["opens"] += opens
            totals["clicks"] += clicks
        return {"campaigns": campaigns, "totals": totals}


class Ga4Adapter:
    key = "ga4"
    block = "ga4"

    def zeros(self):
        return {"pageviews": 0, "users": 0, "avg_engagement_s": 0, "top_sources": []}

    def is_configured(self, sources_cfg):
        cfg = sources_cfg.get("ga4", {})
        return bool(cfg.get("enabled")) and bool(cfg.get("property_id")) and bool(
            resolve_credential(cfg.get("credential_env_or_path"))
        )

    def _service_account_token(self, key_path):
        """Exchange a GA4 service-account JSON key for a bearer token via
        the standard JWT-bearer OAuth2 grant. RS256-signing the JWT needs
        the `cryptography` package -- an optional import used only when
        GA4 creds are actually configured; missing it degrades to
        SourceUnavailable (never a crash) rather than a hard ImportError."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            raise SourceUnavailable(
                "GA4 live auth needs the optional 'cryptography' package (pip install cryptography)"
            )
        import base64

        info = json.loads(Path(key_path).read_text()) if Path(key_path).exists() else json.loads(key_path)
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/analytics.readonly",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        def b64url(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=")

        signing_input = b64url(json.dumps(header).encode()) + b"." + b64url(json.dumps(claims).encode())
        private_key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        assertion = (signing_input + b"." + b64url(signature)).decode()

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def fetch_live(self, listing, start, end, sources_cfg):
        if requests is None:
            raise SourceUnavailable("requests not installed")
        cfg = sources_cfg.get("ga4", {})
        property_id = cfg.get("property_id")
        key_slot = cfg.get("credential_env_or_path")
        key_path_or_json = resolve_credential(key_slot) or (
            str(ROOT / key_slot) if key_slot and (ROOT / key_slot).exists() else None
        )
        if not property_id or not key_path_or_json:
            raise SourceUnavailable("GA4 property_id or service account key missing")

        token = self._service_account_token(key_path_or_json)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        page_path = None
        webflow_page = (listing.get("links") or {}).get("webflow_page")
        if webflow_page:
            from urllib.parse import urlparse
            page_path = urlparse(webflow_page).path

        body = {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "sessionSource"}],
            "metrics": [
                {"name": "screenPageViews"},
                {"name": "totalUsers"},
                {"name": "averageSessionDuration"},
            ],
        }
        if page_path:
            body["dimensionFilter"] = {
                "filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": page_path}}
            }

        resp = requests.post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
            headers=headers,
            json=body,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        report = resp.json() or {}
        rows = report.get("rows", [])

        pageviews = sum(int(r["metricValues"][0]["value"]) for r in rows) if rows else 0
        users = sum(int(r["metricValues"][1]["value"]) for r in rows) if rows else 0
        avg_engagement = (
            sum(float(r["metricValues"][2]["value"]) for r in rows) / len(rows) if rows else 0
        )
        top_sources = sorted(
            (
                {"source": r["dimensionValues"][0]["value"], "users": int(r["metricValues"][1]["value"])}
                for r in rows
            ),
            key=lambda x: -x["users"],
        )[:5]

        return {
            "pageviews": pageviews,
            "users": users,
            "avg_engagement_s": round(avg_engagement, 1),
            "top_sources": top_sources,
        }

    def fetch_sample(self, listing, period_type, period_id, rng, scale):
        pageviews = max(0, round(rng.randint(60, 180) * scale * period_scale(period_type)))
        users = max(0, round(pageviews * rng.uniform(0.55, 0.8)))
        avg_engagement_s = round(rng.uniform(35, 140), 1)
        pool = ["google", "direct", "homes.com", "facebook", "masoncapitalgroup.com (referral)"]
        rng.shuffle(pool)
        remaining = users
        top_sources = []
        for i, src in enumerate(pool[:3]):
            if i == 2:
                share = remaining
            else:
                share = round(remaining * rng.uniform(0.3, 0.6))
            share = max(0, min(share, remaining))
            top_sources.append({"source": src, "users": share})
            remaining -= share
        top_sources.sort(key=lambda x: -x["users"])
        return {
            "pageviews": pageviews,
            "users": users,
            "avg_engagement_s": avg_engagement_s,
            "top_sources": top_sources,
        }


class TawkAdapter:
    key = "tawk"
    block = "tawk"

    def zeros(self):
        return {"chats": 0, "inquiries_about_listing": 0}

    def is_configured(self, sources_cfg):
        cfg = sources_cfg.get("tawk", {})
        return bool(cfg.get("enabled")) and bool(resolve_credential(cfg.get("credential_env_or_path")))

    def _chat_mentions_listing(self, chat, listing):
        text = json.dumps(chat).lower()
        needles = [listing["slug"].replace("-", " "), listing["address"].split(",")[0].lower()]
        return any(n in text for n in needles if n)

    def fetch_live(self, listing, start, end, sources_cfg):
        if requests is None:
            raise SourceUnavailable("requests not installed")
        cfg = sources_cfg.get("tawk", {})
        api_key = resolve_credential(cfg.get("credential_env_or_path"))
        property_id = cfg.get("property_id")
        if not api_key or not property_id:
            raise SourceUnavailable("no tawk.to API key/property id")

        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(
            f"https://api.tawk.to/v3/property/{property_id}/chats",
            headers=headers,
            params={"startDate": start.isoformat(), "endDate": end.isoformat()},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        chats = (resp.json() or {}).get("chats", [])
        mentioning = [c for c in chats if self._chat_mentions_listing(c, listing)]
        return {"chats": len(chats), "inquiries_about_listing": len(mentioning)}

    def fetch_sample(self, listing, period_type, period_id, rng, scale):
        chats = max(0, round(rng.randint(0, 5) * scale * period_scale(period_type)))
        inquiries = max(0, min(chats, round(chats * rng.uniform(0.2, 0.6))))
        return {"chats": chats, "inquiries_about_listing": inquiries}


class PortalIntakeAdapter:
    """Manual-intake adapter for homes.com / Crexi / LoopNet -- no API, so
    this is the one adapter that is 'live' whenever a matching file exists
    on disk, regardless of config/sources.json enablement."""

    key = "portals"
    block = "portals"
    KNOWN_PORTALS = ("homes.com", "crexi", "loopnet")

    def zeros_all(self):
        return {
            "homes.com": {"views": 0, "saves": 0},
            "crexi": {"views": 0, "leads": 0},
            "loopnet": {"views": 0, "leads": 0},
        }

    def _parse_json(self, path):
        raw = json.loads(path.read_text())
        return raw if isinstance(raw, list) else [raw]

    def _parse_csv(self, path):
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    def read_intake(self, slug, period_type, period_id):
        """Returns (portal_data_dict, found: bool)."""
        result = self.zeros_all()
        listing_dir = INTAKE_DIR / slug
        if not listing_dir.exists():
            return result, False

        json_path = listing_dir / f"{period_type}-{period_id}.json"
        csv_path = listing_dir / f"{period_type}-{period_id}.csv"

        rows = None
        if json_path.exists():
            rows = self._parse_json(json_path)
        elif csv_path.exists():
            rows = self._parse_csv(csv_path)

        if rows is None:
            return result, False

        for row in rows:
            portal = str(row.get("portal", "")).strip().lower()
            if portal not in self.KNOWN_PORTALS:
                continue
            if portal == "homes.com":
                result[portal] = {
                    "views": int(row.get("views", 0) or 0),
                    "saves": int(row.get("saves", 0) or 0),
                }
            else:
                result[portal] = {
                    "views": int(row.get("views", 0) or 0),
                    "leads": int(row.get("leads", 0) or 0),
                }
        return result, True

    def fetch_sample(self, listing, period_type, period_id, rng, scale):
        result = self.zeros_all()
        for portal in listing.get("sources", {}).get("portals", []):
            if portal == "homes.com":
                views = max(0, round(rng.randint(40, 140) * scale * period_scale(period_type)))
                result[portal] = {"views": views, "saves": max(0, round(views * rng.uniform(0.03, 0.09)))}
            elif portal in ("crexi", "loopnet"):
                views = max(0, round(rng.randint(10, 55) * scale * period_scale(period_type)))
                result[portal] = {"views": views, "leads": max(0, round(views * rng.uniform(0.02, 0.08)))}
        return result


IDX = IdxAdapter()
CC = ConstantContactAdapter()
GA4 = Ga4Adapter()
TAWK = TawkAdapter()
PORTALS = PortalIntakeAdapter()


# --------------------------------------------------------------------------
# Per-source orchestration (live -> sample -> missing, never raises)
# --------------------------------------------------------------------------

def collect_source(adapter, listing, start, end, period_type, period_id, sources_cfg, sample_mode):
    slug = listing["slug"]
    # listings.json sources dict is keyed idx/cc/ga4/tawk, matching adapter.block.
    listing_wants_source = listing.get("sources", {}).get(adapter.block, True)

    if sample_mode:
        scale = type_scale_factor(listing)
        rng = sample_rng(slug, period_id, salt=adapter.block)
        return adapter.fetch_sample(listing, period_type, period_id, rng, scale), "sample"

    if not listing_wants_source or not adapter.is_configured(sources_cfg):
        return adapter.zeros(), "missing"

    try:
        return adapter.fetch_live(listing, start, end, sources_cfg), "live"
    except SourceUnavailable as e:
        print(f"[collect] {slug}: {adapter.block} unavailable: {e}", file=sys.stderr)
        return adapter.zeros(), "missing"
    except Exception as e:  # noqa: BLE001 - a source failure must never crash the run
        print(f"[collect] {slug}: {adapter.block} error: {e}", file=sys.stderr)
        return adapter.zeros(), "missing"


def collect_portals(listing, start, end, period_type, period_id, sample_mode):
    slug = listing["slug"]
    if sample_mode:
        scale = type_scale_factor(listing)
        rng = sample_rng(slug, period_id, salt="portals")
        return PORTALS.fetch_sample(listing, period_type, period_id, rng, scale), "sample"

    data, found = PORTALS.read_intake(slug, period_type, period_id)
    return data, ("live" if found else "missing")


# --------------------------------------------------------------------------
# Activity / showings (manual intake, optional)
# --------------------------------------------------------------------------

def read_optional_list(slug, filename_prefix, period_type, period_id):
    listing_dir = INTAKE_DIR / slug
    path = listing_dir / f"{filename_prefix}-{period_type}-{period_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else [data]
    except (ValueError, OSError):
        return []


def build_activity(listing, cc_block, start, end, period_type, period_id, sample_mode, rng_seed_salt="activity"):
    activity = []
    for camp in cc_block.get("campaigns", []):
        activity.append({
            "date": start.isoformat(),
            "channel": "email",
            "desc": f"Listing featured in \"{camp['name']}\" ({camp['sent']} recipients)",
        })
    activity.extend(read_optional_list(listing["slug"], "activity", period_type, period_id))

    if sample_mode and not activity:
        rng = sample_rng(listing["slug"], period_id, salt=rng_seed_salt)
        span = (end - start).days or 1
        sample_events = [
            "Featured on masoncapitalgroup.com homepage carousel",
            "Included in weekly MCG buyer-agent broadcast",
            "Professional photography refresh uploaded to IDX",
        ]
        chosen = rng.sample(sample_events, k=min(2, len(sample_events)))
        for i, desc in enumerate(chosen):
            d = start + timedelta(days=rng.randint(0, span))
            activity.append({"date": d.isoformat(), "channel": "marketing", "desc": desc})
    activity.sort(key=lambda a: a["date"])
    return activity


def build_showings(listing, start, end, period_type, period_id, sample_mode):
    showings = read_optional_list(listing["slug"], "showings", period_type, period_id)
    if sample_mode and not showings:
        rng = sample_rng(listing["slug"], period_id, salt="showings")
        scale = type_scale_factor(listing)
        n = rng.choices([0, 1, 2], weights=[0.5, 0.35, 0.15])[0]
        if listing.get("type") != "residential":
            n = rng.choices([0, 1], weights=[0.7, 0.3])[0]
        feedback_pool = [
            "Loved the layout; concerned about proximity to the road",
            "Strong interest, comparing against one other property this week",
            "Liked the lot; wants updated comps before offering",
            "Buyer's agent requested inspection history",
            "Positive walk-through; discussing financing timeline",
        ]
        span = (end - start).days or 1
        for _ in range(n):
            d = start + timedelta(days=rng.randint(0, span))
            showings.append({
                "date": d.isoformat(),
                "feedback": rng.choice(feedback_pool),
                "source": "manual",
            })
        showings.sort(key=lambda s: s["date"])
    return showings


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------

def aggregate_totals(sources_block):
    """total_views / total_leads across every source block, using each
    block's own vocabulary (views/pageviews/leads/inquiries)."""
    idx = sources_block.get("idx", {})
    ga4 = sources_block.get("ga4", {})
    portals = sources_block.get("portals", {})
    tawk = sources_block.get("tawk", {})

    total_views = idx.get("views", 0) + ga4.get("pageviews", 0)
    total_leads = idx.get("leads", 0) + tawk.get("inquiries_about_listing", 0)
    for portal_data in portals.values():
        total_views += portal_data.get("views", 0)
        total_leads += portal_data.get("leads", 0)
    return total_views, total_leads


def build_trend(slug, period_type, period_id, start, current_sources_block):
    prior_id = prior_period_id(period_type, period_id, start)
    prior_path = DATA_DIR / slug / prior_id / "metrics.json"
    prior_views, prior_leads = 0, 0
    prior = load_json(prior_path)
    if prior:
        prior_views, prior_leads = aggregate_totals(prior.get("sources", {}))

    current_views, _ = aggregate_totals(current_sources_block)
    if prior_views:
        delta_pct = round((current_views - prior_views) / prior_views * 100, 1)
    else:
        delta_pct = 0.0

    return {
        "prior_period": {"total_views": prior_views, "total_leads": prior_leads},
        "delta_views_pct": delta_pct,
    }


# --------------------------------------------------------------------------
# Market block
# --------------------------------------------------------------------------

def build_market(listing, market_cfg):
    counties = market_cfg.get("counties", {})
    county = listing.get("county")
    if county and county in counties:
        dom = counties[county]["dom_days"]
        county_label = county
    else:
        # No county on file for this listing (e.g. 407 Old Forge) -- fall
        # back to a blended Benton+Washington figure rather than guessing.
        dom_values = [c["dom_days"] for c in counties.values()] or [None]
        dom = round(sum(dom_values) / len(dom_values)) if dom_values[0] is not None else None
        county_label = "NWA (blended -- county unconfirmed)"

    barbell = market_cfg.get("pricing_barbell", {})
    price = listing.get("price") or 0
    if listing.get("price_type") == "lease_monthly":
        tier_note = "priced as a monthly lease, outside the sale-price barbell bands"
    elif price and price < 500_000:
        tier_note = f"in the sub-$500K tier, the strongest-performing band regionally ({barbell.get('sub_500k', {}).get('benton_yoy_pct', '?')}% YoY Benton / {barbell.get('sub_500k', {}).get('washington_yoy_pct', '?')}% YoY Washington)"
    elif price and price < 1_000_000:
        tier_note = "in the $500K-$999K tier, where regional demand is softening relative to sub-$500K"
    else:
        tier_note = "in the resilient luxury tier, which is holding relative to the mid-range"

    positioning = (
        f"{listing['address'].split(',')[0]} sits {tier_note}; "
        f"{county_label} County median DOM is currently {dom} days." if dom is not None else
        f"{listing['address'].split(',')[0]} sits {tier_note}; county DOM comparison unavailable."
    )

    return {
        "area_dom_days": dom,
        "county": county_label,
        "comps": [],  # placeholder -- no CMA/MLS comp feed wired up yet
        "positioning": positioning,
    }


# --------------------------------------------------------------------------
# Insights (deterministic narrative generator, no LLM calls)
# --------------------------------------------------------------------------

def build_insights(listing, sources_block, trend, market_block, data_quality, period_type):
    total_views, total_leads = aggregate_totals(sources_block)
    delta = trend["delta_views_pct"]
    dom = market_block.get("area_dom_days")
    county = market_block.get("county")
    addr_short = listing["address"].split(",")[0]

    # --- views vs prior sentence ---
    live_sources = [k for k, v in data_quality.items() if v == "live"]
    sample_sources = [k for k, v in data_quality.items() if v == "sample"]
    if sample_sources and not live_sources:
        quality_note = "Figures below are sample data pending live source connections."
    elif not live_sources and not sample_sources:
        quality_note = "No connected sources reported activity this period; figures are placeholders pending source setup."
    else:
        quality_note = ""

    if trend["prior_period"]["total_views"] == 0 and total_views == 0:
        views_sentence = f"{addr_short} has not yet accumulated tracked online activity this {period_type} period."
    elif trend["prior_period"]["total_views"] == 0:
        views_sentence = f"{addr_short} logged {total_views} tracked views this period, the first period with comparable data."
    elif delta > 0:
        views_sentence = f"{addr_short} logged {total_views} tracked views this period, up {delta}% from the prior period."
    elif delta < 0:
        views_sentence = f"{addr_short} logged {total_views} tracked views this period, down {abs(delta)}% from the prior period."
    else:
        views_sentence = f"{addr_short} logged {total_views} tracked views this period, flat versus the prior period."

    # --- DOM / positioning sentence (market_block['positioning'] already
    # states the county DOM figure, so it is used as-is rather than
    # prefixed with a second DOM mention). ---
    dom_sentence = market_block.get("positioning", "")

    # --- leads/inquiries sentence ---
    if total_leads > 0:
        lead_sentence = f"The period produced {total_leads} tracked lead{'s' if total_leads != 1 else ''} across IDX, portal, and chat channels."
    else:
        lead_sentence = "No tracked leads were attributed to this listing this period."

    summary_parts = [views_sentence, dom_sentence, lead_sentence]
    if quality_note:
        summary_parts.append(quality_note)
    summary = " ".join(p for p in summary_parts if p)

    recommendations = []
    if listing.get("type") == "residential":
        recommendations.append("Confirm current professional photography and floor plan are live across IDX and homes.com -- both measurably lift engagement.")
    if dom is not None and delta < 0:
        recommendations.append("Traffic softened this period; consider a coordinated push (refreshed photos, renewed CC send, portal boost) before the next report.")
    if total_leads == 0:
        recommendations.append("Zero tracked leads this period -- review pricing against comparable DOM and confirm all inquiry channels (chat, IDX, portals) are properly instrumented.")
    if not recommendations:
        recommendations.append("Maintain current marketing cadence; performance is tracking in line with expectations.")

    next_period_plan = [
        "Continue monitoring IDX, email, web, and portal channels for this listing.",
        "Log any showings and buyer feedback as they occur so next period's report reflects them.",
    ]
    if listing.get("price_type") != "lease_monthly" and dom is not None:
        next_period_plan.append(f"Revisit pricing positioning if DOM trends materially above the {county} County {dom}-day median.")

    return {"summary": summary, "recommendations": recommendations, "next_period_plan": next_period_plan}


# --------------------------------------------------------------------------
# Main per-listing build
# --------------------------------------------------------------------------

def build_metrics(listing, period_type, period_id, start, end, sources_cfg, market_cfg, sample_mode):
    slug = listing["slug"]

    idx_data, idx_q = collect_source(IDX, listing, start, end, period_type, period_id, sources_cfg, sample_mode)
    cc_data, cc_q = collect_source(CC, listing, start, end, period_type, period_id, sources_cfg, sample_mode)
    ga4_data, ga4_q = collect_source(GA4, listing, start, end, period_type, period_id, sources_cfg, sample_mode)
    tawk_data, tawk_q = collect_source(TAWK, listing, start, end, period_type, period_id, sources_cfg, sample_mode)
    portals_data, portals_q = collect_portals(listing, start, end, period_type, period_id, sample_mode)

    sources_block = {"idx": idx_data, "cc": cc_data, "ga4": ga4_data, "tawk": tawk_data, "portals": portals_data}
    data_quality = {"idx": idx_q, "cc": cc_q, "ga4": ga4_q, "tawk": tawk_q, "portals": portals_q}

    activity = build_activity(listing, cc_data, start, end, period_type, period_id, sample_mode)
    showings = build_showings(listing, start, end, period_type, period_id, sample_mode)
    trend = build_trend(slug, period_type, period_id, start, sources_block)
    market_block = build_market(listing, market_cfg)
    insights = build_insights(listing, sources_block, trend, market_block, data_quality, period_type)

    return {
        "listing_slug": slug,
        "period": {"type": period_type, "id": period_id, "start": start.isoformat(), "end": end.isoformat()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_quality": data_quality,
        "activity": activity,
        "showings": showings,
        "sources": sources_block,
        "trend": trend,
        "market": market_block,
        "insights": insights,
    }


def write_metrics(metrics):
    out_dir = DATA_DIR / metrics["listing_slug"] / metrics["period"]["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "metrics.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
        f.write("\n")
    return out_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect seller activity metrics for MCG listings.")
    parser.add_argument("--period", required=True, choices=["weekly", "monthly", "quarterly"])
    parser.add_argument("--period-id", default=None, help="Explicit period id, e.g. 2026-W29 / 2026-07 / 2026-Q3. Defaults to the period containing the day before today.")
    parser.add_argument("--slug", default="all", help="Listing slug, or 'all' (default).")
    parser.add_argument("--sample", action="store_true", help="Generate deterministic sample data instead of calling live sources.")
    args = parser.parse_args(argv)

    listings = load_listings()
    if not listings:
        print("[collect] no listings found in config/listings.json", file=sys.stderr)
        return 1

    if args.slug != "all":
        listings = [l for l in listings if l["slug"] == args.slug]
        if not listings:
            print(f"[collect] no listing with slug {args.slug!r}", file=sys.stderr)
            return 1

    if args.period_id:
        period_id = args.period_id
        start, end = period_from_id(args.period, period_id)
    else:
        anchor = date.today() - timedelta(days=1)
        period_id, start, end = period_from_anchor(args.period, anchor)

    sources_cfg = load_sources_cfg()
    market_cfg = load_market_cfg()

    written = []
    for listing in listings:
        if listing.get("status") != "active":
            continue
        metrics = build_metrics(listing, args.period, period_id, start, end, sources_cfg, market_cfg, args.sample)
        path = write_metrics(metrics)
        written.append(path)
        print(f"[collect] wrote {path.relative_to(ROOT)}")

    print(f"[collect] {len(written)} metrics.json file(s) written for period {period_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
