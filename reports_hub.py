"""
reports_hub.py -- Listing Reports panel support: registry fetch, period-id
math, and Constant Contact v3 send/stats integration.

Used by app.py's /api/reports/* routes. See app.py for the state file
(.reports_state.json, same on-disk pattern as .dashboard_settings.json) and
route wiring; this module holds the parts that talk to GitHub raw content
and Constant Contact.

Credential resolution for Constant Contact (never committed, never logged):
    1. $CC_TOKENS_FILE  -- path to a JSON blob
    2. ./cc_seller_reports_token.json (beside app.py)
Blob shape (matches the seller-reports pipeline's own CC credential file):
    {"access_token": "...", "refresh_token": "...", "expires_at": "...",
     "client_id": "...", "client_secret": "..."}
Rotated tokens (new access_token/expires_at from a refresh) are written back
to the same file so subsequent calls reuse them without re-authenticating.

CC_DRY_RUN=1 short-circuits every Constant Contact network call: it logs
what would have happened and returns fabricated ids so the full
approve -> send -> stats flow can be exercised locally without touching a
real CC account or sending a real email. Defaults OFF -- only set this in a
dev/test environment, never in production.

Safety: send_report() is the only path in this codebase that ever calls
Constant Contact's send/schedule endpoint, and it is only ever invoked from
app.py's POST /api/reports/send route, which requires @login_required
(Cameron's click). There is no scheduled job, webhook, or background thread
anywhere that calls it.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html as html_lib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent

CC_API_BASE = "https://api.cc.email/v3"
CC_TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"

REGISTRY_URL = "https://raw.githubusercontent.com/TorabiC/mcg-listings/main/seller-reports/config/listings.json"
REPORTS_BASE_URL = "https://torabic.github.io/mcg-listings/reports"

SEND_FROM_EMAIL = "Torabi@MasonCapitalGroup.com"


def dry_run() -> bool:
    return os.getenv("CC_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def current_period_id(period_type: str, today: dt.date | None = None) -> str:
    """ISO week id (2026-W29) for 'weekly', calendar month id (2026-07) for
    'monthly', computed server-side so the hub and the seller-reports
    pipeline always agree on 'today's period'."""
    today = today or dt.date.today()
    if period_type == "weekly":
        iso_year, iso_week, _ = today.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period_type == "monthly":
        return f"{today.year}-{today.month:02d}"
    raise ValueError(f"unknown period_type {period_type!r}")


def period_type_of(period_id: str) -> str:
    if re.match(r"^\d{4}-W\d{2}$", period_id):
        return "weekly"
    if re.match(r"^\d{4}-\d{2}$", period_id):
        return "monthly"
    return "weekly"


def prior_period_ids(period_type: str, current_id: str, n: int = 8) -> list[str]:
    """The n periods immediately preceding current_id, most recent first."""
    out: list[str] = []
    if period_type == "weekly":
        year_s, week_s = current_id.split("-W")
        base = dt.date.fromisocalendar(int(year_s), int(week_s), 1)
        for i in range(1, n + 1):
            prior = base - dt.timedelta(weeks=i)
            iso_year, iso_week, _ = prior.isocalendar()
            out.append(f"{iso_year}-W{iso_week:02d}")
    elif period_type == "monthly":
        year, month = (int(x) for x in current_id.split("-"))
        for i in range(1, n + 1):
            m = month - i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            out.append(f"{y}-{m:02d}")
    return out


def report_url(slug: str, token: str, period_id: str) -> str:
    return f"{REPORTS_BASE_URL}/{slug}-{token}/{period_id}/"


def flyer_url(slug: str, token: str, period_id: str) -> str:
    return f"{REPORTS_BASE_URL}/{slug}-{token}/{period_id}/flyer.html"


def insights_url(slug: str, token: str, period_id: str) -> str:
    return f"{REPORTS_BASE_URL}/{slug}-{token}/{period_id}/insights.json"


# ---------------------------------------------------------------------------
# Registry (10-minute cache)
# ---------------------------------------------------------------------------
_registry_cache: dict = {"data": None, "fetched_at": 0.0}
REGISTRY_TTL_SECONDS = 600


def get_registry(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _registry_cache["data"] is not None and (now - _registry_cache["fetched_at"]) < REGISTRY_TTL_SECONDS:
        return _registry_cache["data"]
    try:
        resp = requests.get(REGISTRY_URL, timeout=15)
        resp.raise_for_status()
        listings = resp.json().get("listings", [])
        _registry_cache["data"] = listings
        _registry_cache["fetched_at"] = now
        return listings
    except Exception as e:
        logger.warning(f"[reports_hub] registry fetch failed: {e}")
        # Serve the last good copy rather than an empty panel, if we have one.
        return _registry_cache["data"] or []


def get_listing(slug: str) -> dict | None:
    for l in get_registry():
        if l.get("slug") == slug:
            return l
    return None


def mask_email(email: str) -> str:
    """'jane@gmail.com' -> 'j***@gmail.com', for the collapsed list view.
    The full address is still returned separately for the edit field --
    this is an authenticated admin API, not seller-facing."""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    first = local[0] if local else ""
    return f"{first}***@{domain}"


def fetch_insights(slug: str, token: str, period_id: str) -> dict | None:
    """Best-effort fetch of the private insights.json for a period. Returns
    None (never raises) if it isn't published yet -- older periods rendered
    before this feature shipped won't have one."""
    try:
        resp = requests.get(insights_url(slug, token, period_id), timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def report_exists(slug: str, token: str, period_id: str) -> bool:
    try:
        resp = requests.get(report_url(slug, token, period_id), timeout=15)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Constant Contact credentials
# ---------------------------------------------------------------------------
def _tokens_path() -> Path:
    env_path = os.getenv("CC_TOKENS_FILE")
    if env_path:
        return Path(env_path)
    return APP_DIR / "cc_seller_reports_token.json"


def load_cc_creds() -> dict | None:
    p = _tokens_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def save_cc_creds(creds: dict) -> None:
    p = _tokens_path()
    p.write_text(json.dumps(creds, indent=2))


def get_access_token(creds: dict) -> str:
    expires_at = creds.get("expires_at")
    if expires_at:
        try:
            exp = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp > dt.datetime.now(dt.timezone.utc):
                return creds["access_token"]
        except ValueError:
            pass
    resp = requests.post(
        CC_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds.get("refresh_token"),
            "client_id": creds.get("client_id"),
            "client_secret": creds.get("client_secret"),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    creds["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    expires_in = data.get("expires_in", 3600)
    creds["expires_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(expires_in))).isoformat()
    save_cc_creds(creds)
    return creds["access_token"]


class CCError(Exception):
    """Raised for any Constant Contact failure. Routes catch this and
    surface `error` to the dashboard rather than crashing the process."""


def _cc_headers() -> dict:
    creds = load_cc_creds()
    if not creds:
        raise CCError(
            "Constant Contact is not configured -- set CC_TOKENS_FILE or add "
            "cc_seller_reports_token.json beside app.py."
        )
    token = get_access_token(creds)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Contact / list upsert
# ---------------------------------------------------------------------------
def ensure_contact(email: str, name: str, list_id: str | None = None) -> str | None:
    if dry_run():
        logger.info(f"[CC_DRY_RUN] would upsert contact {email!r} ({name!r}) into list {list_id}")
        return "dry-contact-id"
    headers = _cc_headers()
    first, _, last = (name or "").strip().partition(" ")
    # CC v3 sign_up_form is a non-destructive upsert; it requires exactly one
    # of `source` / `list_memberships` — passing the list here also replaces
    # the old PUT /contacts/{id} membership update, which was a full-replace
    # that could wipe fields on pre-existing contacts.
    payload = {
        "email_address": email,  # sign_up_form takes a plain string, not the {address,...} object
        "first_name": first,
        "last_name": last,
    }
    if list_id:
        payload["list_memberships"] = [list_id]
    else:
        payload["source"] = "Account"
    resp = requests.post(f"{CC_API_BASE}/contacts/sign_up_form", headers=headers, json=payload, timeout=20)
    if resp.status_code >= 400:
        raise CCError(f"contact upsert failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json().get("contact_id")


def ensure_list(name: str, contact_id: str | None = None) -> str | None:
    if dry_run():
        logger.info(f"[CC_DRY_RUN] would ensure list {name!r}, add contact {contact_id}")
        return "dry-list-id"
    headers = _cc_headers()
    resp = requests.get(f"{CC_API_BASE}/contact_lists", headers=headers, params={"limit": 500}, timeout=20)
    resp.raise_for_status()
    list_id = None
    for lst in resp.json().get("lists", []):
        if lst.get("name") == name:
            list_id = lst.get("list_id")
            break
    if not list_id:
        create = requests.post(
            f"{CC_API_BASE}/contact_lists",
            headers=headers,
            json={"name": name, "description": "MCG Listing Reports -- auto-created by the Listing Reports hub"},
            timeout=20,
        )
        if create.status_code >= 400:
            raise CCError(f"list create failed ({create.status_code}): {create.text[:300]}")
        list_id = create.json().get("list_id")

    # Membership is applied by ensure_contact() via sign_up_form's
    # list_memberships (non-destructive) — no PUT /contacts/{id} here, which
    # is a full-replace and would clear fields on pre-existing contacts.
    return list_id


# ---------------------------------------------------------------------------
# Flyer HTML + personal note
# ---------------------------------------------------------------------------
def fetch_flyer_html(slug: str, token: str, period_id: str) -> str:
    url = flyer_url(slug, token, period_id)
    try:
        resp = requests.get(url, timeout=20)
    except requests.RequestException as e:
        raise CCError(f"could not fetch flyer HTML at {url}: {e}") from e
    if resp.status_code != 200:
        raise CCError(f"flyer HTML not found at {url} (HTTP {resp.status_code}) -- run bin/generate.py for this period first")
    return resp.text


_NOTE_BLOCK = """<tr><td class="mcg-px" style="padding:20px 28px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F7F5F0;border-left:4px solid #C4A35A;border-radius:4px;">
    <tr>
      <td style="padding:16px 20px;font-family:Lato,Arial,sans-serif;font-size:13px;color:#16162A;line-height:1.6;">
        <div style="font-family:'Playfair Display',Georgia,'Times New Roman',serif;font-weight:800;font-size:12px;color:#AB012E;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">A note from Cameron</div>
        {note_html}
      </td>
    </tr>
  </table>
</td></tr>"""


def build_email_html(flyer_html: str, note: str | None) -> str:
    """Flyer HTML, with an optional MCG-styled (navy/gold) personal-note
    block inserted as the first row of the flyer's container table."""
    if not note or not note.strip():
        return flyer_html
    note_html = html_lib.escape(note.strip()).replace("\n", "<br>")
    block = _NOTE_BLOCK.format(note_html=note_html)
    marker = 'class="mcg-container"'
    idx = flyer_html.find(marker)
    if idx == -1:
        return flyer_html
    tag_end = flyer_html.find(">", idx) + 1
    if tag_end <= 0:
        return flyer_html
    return flyer_html[:tag_end] + block + flyer_html[tag_end:]


# ---------------------------------------------------------------------------
# Campaign create / send / stats
# ---------------------------------------------------------------------------
def create_campaign(name: str, subject: str, html_content: str) -> dict:
    if dry_run():
        logger.info(f"[CC_DRY_RUN] would create campaign {name!r} subject={subject!r} ({len(html_content)} bytes html)")
        return {"campaign_id": f"dry-campaign-{int(time.time())}", "campaign_activity_id": f"dry-activity-{int(time.time())}"}
    headers = _cc_headers()
    payload = {
        "name": name,
        "email_campaign_activities": [{
            "format_type": 5,  # custom-code campaign
            "from_email": SEND_FROM_EMAIL,
            "from_name": "Cameron Torabi -- Mason Capital Group",
            "reply_to_email": SEND_FROM_EMAIL,
            "subject": subject,
            "html_content": html_content,
        }],
    }
    resp = requests.post(f"{CC_API_BASE}/emails", headers=headers, json=payload, timeout=30)
    if resp.status_code >= 400:
        raise CCError(f"campaign create failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    activities = data.get("campaign_activities") or []
    return {
        "campaign_id": data.get("campaign_id"),
        "campaign_activity_id": (activities[0].get("campaign_activity_id") if activities else None),
    }


def set_activity_recipients(campaign_activity_id: str, list_id: str) -> None:
    if dry_run():
        logger.info(f"[CC_DRY_RUN] would set list {list_id} as recipient of activity {campaign_activity_id}")
        return
    headers = _cc_headers()
    resp = requests.put(
        f"{CC_API_BASE}/emails/activities/{campaign_activity_id}",
        headers=headers,
        json={"contact_list_ids": [list_id]},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise CCError(f"set recipients failed ({resp.status_code}): {resp.text[:300]}")


def send_now(campaign_activity_id: str) -> None:
    """Schedules the campaign activity to send immediately, per CC v3 docs
    (POST /emails/activities/{id}/schedules with the current UTC time)."""
    if dry_run():
        logger.info(f"[CC_DRY_RUN] would schedule campaign activity {campaign_activity_id} to send NOW")
        return
    headers = _cc_headers()
    scheduled_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    resp = requests.post(
        f"{CC_API_BASE}/emails/activities/{campaign_activity_id}/schedules",
        headers=headers,
        json={"scheduled_date": scheduled_date},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise CCError(f"send failed ({resp.status_code}): {resp.text[:300]}")


def get_stats(campaign_activity_id: str) -> dict:
    if dry_run() or (campaign_activity_id or "").startswith("dry-"):
        return {"sends": 0, "opens_unique": 0, "clicks_unique": 0}
    headers = _cc_headers()
    resp = requests.get(
        f"{CC_API_BASE}/reports/stats/email_campaign_activities/{campaign_activity_id}",
        headers=headers,
        timeout=20,
    )
    if resp.status_code >= 400:
        raise CCError(f"stats failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    return {
        "sends": data.get("em_sends", 0),
        "opens_unique": data.get("em_opens_all_unique", 0),
        "clicks_unique": data.get("em_clicks_all_unique", 0),
    }


# ---------------------------------------------------------------------------
# Top-level orchestration -- the ONLY function anywhere in this codebase
# that actually sends a seller report. Called exclusively from app.py's
# POST /api/reports/send route (@login_required -- Cameron's click).
# ---------------------------------------------------------------------------
def send_report(listing: dict, period_id: str, note: str | None) -> dict:
    slug = listing["slug"]
    token = listing.get("report_token", "")
    seller = listing.get("seller") or {}
    email = seller.get("email")
    name = seller.get("name") or "Seller"
    if not email:
        raise CCError(f"no seller e-mail on file for {slug!r} -- add one to the registry before sending")

    if not report_exists(slug, token, period_id):
        raise CCError(f"report page not found at {report_url(slug, token, period_id)} -- refusing to send")

    flyer_html = fetch_flyer_html(slug, token, period_id)
    email_html = build_email_html(flyer_html, note)
    sha256 = hashlib.sha256(email_html.encode("utf-8")).hexdigest()

    street = (listing.get("address") or slug).split(",")[0].strip()
    period_type = period_type_of(period_id)
    period_label = "Weekly" if period_type == "weekly" else "Monthly"

    list_id = ensure_list(f"Seller Reports — {street}")
    contact_id = ensure_contact(email, name, list_id)

    campaign_name = f"Listing Intelligence — {street} — {period_id}"
    subject = f"Your {period_label} Marketing Report — {street}"
    result = create_campaign(campaign_name, subject, email_html)

    activity_id = result.get("campaign_activity_id")
    if list_id and activity_id:
        set_activity_recipients(activity_id, list_id)
    if activity_id:
        send_now(activity_id)

    return {
        "cc_campaign_id": result.get("campaign_id"),
        "cc_activity_id": activity_id,
        "sent_html_sha256": sha256,
        "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Auto-provision: register a newly-published listing for seller reports.
#
# Called from app.py's listing-creation routes (/api/generate,
# /api/generate-job, /api/publish) right after a successful Webflow publish,
# so a new listing starts getting seller reports with zero manual steps --
# the next scheduled bin/generate.py + bin/deploy.py run picks it up
# automatically once it's in the registry.
#
# NEVER raises: every failure path (no token, network error, bad API
# response) is logged and returns False so the caller can surface a soft
# "reports_registered": false in its response without failing the listing
# creation itself.
# ---------------------------------------------------------------------------
GITHUB_API_BASE = "https://api.github.com"
REGISTRY_REPO = "TorabiC/mcg-listings"
REGISTRY_PATH = "seller-reports/config/listings.json"
REGISTRY_BRANCH = "main"

_GITHUB_TOKEN_RE = re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")


def registry_dry_run() -> bool:
    """Forces auto-registration to log-only, even if a real GITHUB_TOKEN is
    configured -- for local testing. Defaults OFF: with no token configured
    at all, register_listing_for_reports() already skips real API calls on
    its own (see below), so this flag only matters when you want to test the
    full build_registry_entry()/idempotency logic against a *fake* token
    without risking a real GitHub API call."""
    return os.getenv("REGISTRY_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def _github_token() -> str | None:
    """$GITHUB_TOKEN, or the file at $GITHUB_TOKEN_FILE (never hardcoded --
    mirrors seller-reports/bin/deploy.py's own token resolution)."""
    env_val = os.environ.get("GITHUB_TOKEN")
    if env_val:
        return env_val.strip()
    file_path = os.environ.get("GITHUB_TOKEN_FILE")
    if not file_path:
        return None
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        raw = p.read_text().strip()
    except OSError:
        return None
    m = _GITHUB_TOKEN_RE.search(raw)
    return m.group(0) if m else (raw or None)


def infer_listing_type(listing: dict) -> str:
    """Best-effort residential/commercial/land classification from whatever
    the scrape/generate pipeline populated. Never raises; defaults to
    'residential' (the common case) when nothing else matches."""
    raw = " ".join(str(listing.get(k) or "") for k in ("property_type", "type")).strip().lower()
    if any(k in raw for k in ("land", "lot", "acreage", "acre")):
        return "land"
    if any(k in raw for k in ("commercial", "retail", "office", "industrial", "mixed use", "mobile home", "mhp")):
        return "commercial"
    return "residential"


def default_sources_for_type(ptype: str) -> dict:
    portals = ["homes.com"] if ptype == "residential" else ["crexi", "loopnet"]
    return {"idx": True, "cc": True, "ga4": True, "tawk": True, "portals": portals}


def build_registry_entry(listing: dict, webflow_url: str | None) -> dict:
    """Shape matches the existing entries in seller-reports/config/listings.json
    (see that file). seller.name/email are ALWAYS null here, by design --
    seller PII is entered later via the dashboard's Listing Reports panel
    (POST /api/reports/seller) and lives only in reports_state.json, never
    in this public repo."""
    ptype = infer_listing_type(listing)
    photos = listing.get("photos") or []
    links = listing.get("links") or {}
    price_type = listing.get("price_type") or ("lease_monthly" if "lease" in str(listing.get("price_type", "")).lower() else "sale")
    return {
        "slug": listing.get("slug", ""),
        "address": listing.get("address_full") or listing.get("address_street") or listing.get("slug", ""),
        "type": ptype,
        "county": listing.get("county") or "",
        "price": listing.get("price") or 0,
        "price_type": price_type,
        "beds": listing.get("beds"),
        "baths": listing.get("baths"),
        "status": "active",
        "list_date": listing.get("list_date"),
        "mls_id": listing.get("mls_number") or listing.get("mls_id"),
        "mls_entries": [],
        "seller": {"name": None, "email": None},
        "report_token": secrets.token_hex(4),
        "links": {
            "idx": links.get("idx"),
            "webflow_page": webflow_url or links.get("webflow_page"),
            "marketing_page": links.get("marketing_page"),
            "hero_image": photos[0] if photos else links.get("hero_image"),
        },
        "sources": default_sources_for_type(ptype),
        "notes": f"Auto-registered by the marketing hub on {dt.date.today().isoformat()}.",
    }


def register_listing_for_reports(listing: dict, webflow_url: str | None) -> bool:
    """Idempotently appends a registry entry for `listing` to
    seller-reports/config/listings.json in torabic/mcg-listings via the
    GitHub contents API (GET current file + sha, append if the slug isn't
    already present, PUT). Returns True on success OR if the slug was
    already registered; False if it could not be registered for any reason.
    Never raises."""
    slug = listing.get("slug")
    if not slug:
        return False

    token = _github_token()
    dry = registry_dry_run()
    if not token and not dry:
        logger.info(f"[reports_hub] no GITHUB_TOKEN/GITHUB_TOKEN_FILE configured -- skipping auto-registration for {slug!r}")
        return False

    # In dry-run mode without a real token, the lookup GET still runs
    # (unauthenticated -- reading a public repo's contents needs no token)
    # so the full idempotency-check/build_registry_entry() logic can be
    # exercised end to end in tests; only the mutating PUT is ever skipped
    # when a token isn't present, per registry_dry_run() below.
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    api_url = f"{GITHUB_API_BASE}/repos/{REGISTRY_REPO}/contents/{REGISTRY_PATH}"

    try:
        get_resp = requests.get(api_url, headers=headers, params={"ref": REGISTRY_BRANCH}, timeout=20)
        if get_resp.status_code != 200:
            logger.warning(f"[reports_hub] registry GET failed ({get_resp.status_code}) -- skipping auto-registration for {slug!r}")
            return False
        payload = get_resp.json()
        sha = payload.get("sha")
        current = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        listings = current.get("listings", [])

        if any(l.get("slug") == slug for l in listings):
            logger.info(f"[reports_hub] {slug!r} already registered -- skipping (idempotent)")
            return True

        entry = build_registry_entry(listing, webflow_url)
        listings.append(entry)
        current["listings"] = listings
        new_content = json.dumps(current, indent=2) + "\n"

        if registry_dry_run():
            logger.info(f"[REGISTRY_DRY_RUN] would PUT {REGISTRY_PATH} adding {slug!r}: {json.dumps(entry)[:300]}")
            return True

        put_resp = requests.put(
            api_url,
            headers=headers,
            json={
                "message": f"hub: auto-register listing {slug} for seller reports",
                "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
                "sha": sha,
                "branch": REGISTRY_BRANCH,
            },
            timeout=20,
        )
        if put_resp.status_code >= 300:
            logger.warning(f"[reports_hub] registry PUT failed ({put_resp.status_code}): {put_resp.text[:300]}")
            return False
        logger.info(f"[reports_hub] auto-registered {slug!r} for seller reports")
        return True
    except Exception as e:  # noqa: BLE001 -- must never break listing creation
        logger.warning(f"[reports_hub] auto-registration error for {slug!r} (non-fatal): {e}")
        return False
