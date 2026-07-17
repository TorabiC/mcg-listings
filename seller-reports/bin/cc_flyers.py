#!/usr/bin/env python3
"""
cc_flyers.py -- create Constant Contact DRAFT email campaigns from the
per-listing flyer HTML that bin/generate.py writes to out/flyers/.

This module NEVER schedules or sends a campaign. It only ever creates a
campaign in DRAFT status via the Constant Contact API v3
(POST /v3/emails). Cameron reviews and sends from the Constant Contact UI
himself.

Per listing per period, this:
  1. Reads out/flyers/<slug>-<period_id>.html (written by bin/generate.py).
  2. Resolves that listing's seller contact/segment (config/listings.json
     seller.email, or a CC list/segment id override in config/sources.json
     if one is set for that slug -- see NOTES below).
  3. If Constant Contact credentials are missing/disabled, or the listing
     has no addressable seller contact/segment, prints:
       "flyer HTML ready at <path>; CC draft skipped (no credentials)"
     and exits 0 (never a hard failure -- this mirrors collect.py's
     "missing source never crashes the run" contract).
  4. Otherwise creates the campaign + campaign activity as a DRAFT via the
     CC API v3 and prints the resulting campaign id / edit URL.

Usage:
    python3 bin/cc_flyers.py --period-id 2026-W29 [--slug all|<slug>]
        [--listings config/listings.json] [--sources config/sources.json]
        [--flyers-dir out/flyers] [--base-url https://torabic.github.io/mcg-listings]

Dependencies: stdlib + requests (only imported/used when credentials are
present and a draft is actually being created).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

CC_API_BASE = "https://api.cc.email/v3"
CC_TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"


# ---------------------------------------------------------------------------
# Config / credential helpers (mirrors bin/collect.py's resolve_credential)
# ---------------------------------------------------------------------------
def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def resolve_credential(slot: str | None):
    if not slot:
        return None
    env_val = os.environ.get(slot)
    if env_val:
        return env_val
    p = Path(slot)
    if not p.is_absolute():
        p = REPO_ROOT / slot
    if p.exists():
        try:
            return p.read_text().strip()
        except OSError:
            return None
    return None


class CCUnavailable(Exception):
    """Raised whenever a draft cannot be created -- caller always falls
    back to the 'flyer HTML ready; CC draft skipped' message and exit 0."""


def cc_configured(sources_cfg: dict) -> tuple[bool, dict | None, str | None]:
    cfg = sources_cfg.get("constant_contact", {})
    if not cfg.get("enabled"):
        return False, None, "constant_contact not enabled in config/sources.json"
    token_blob = resolve_credential(cfg.get("credential_env_or_path"))
    if not token_blob:
        return False, None, "no Constant Contact OAuth token configured"
    try:
        creds = json.loads(token_blob)
    except (ValueError, TypeError):
        return False, None, "CC credential is not the expected JSON blob"
    return True, creds, None


# ---------------------------------------------------------------------------
# Seller contact / segment resolution
# ---------------------------------------------------------------------------
def resolve_recipient(listing: dict, sources_cfg: dict) -> tuple[dict | None, str | None]:
    """Returns (recipient_spec, error). recipient_spec is either
    {"contact_email": "..."} (seller.email on file) or
    {"list_id": "..."} / {"segment_id": "..."} if config/sources.json
    defines a per-slug CC list/segment override under
    constant_contact.listing_segments.<slug>. Neither present -> error."""
    seller_email = (listing.get("seller") or {}).get("email")
    if seller_email:
        return {"contact_email": seller_email}, None

    overrides = sources_cfg.get("constant_contact", {}).get("listing_segments", {})
    slug_override = overrides.get(listing["slug"])
    if slug_override:
        if slug_override.get("list_id"):
            return {"list_id": slug_override["list_id"]}, None
        if slug_override.get("segment_id"):
            return {"segment_id": slug_override["segment_id"]}, None

    return None, f"no seller.email on file and no constant_contact.listing_segments override for {listing['slug']!r}"


# ---------------------------------------------------------------------------
# CC API v3 draft creation
# ---------------------------------------------------------------------------
def refresh_access_token(creds: dict, requests_mod) -> str:
    expires_at = creds.get("expires_at")
    if expires_at:
        import datetime as dt
        try:
            if dt.datetime.fromisoformat(expires_at) > dt.datetime.now(dt.timezone.utc):
                return creds["access_token"]
        except ValueError:
            pass
    resp = requests_mod.post(
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
    return resp.json()["access_token"]


def create_draft_campaign(listing: dict, period_id: str, flyer_html: str,
                           recipient: dict, creds: dict, base_url: str) -> dict:
    """Creates a DRAFT campaign (POST /v3/emails) with one campaign_activity
    carrying the flyer HTML, addressed to the resolved seller contact or
    segment. Status is left as the API default (DRAFT) -- no schedule/send
    call is ever made. Returns the created campaign JSON."""
    try:
        import requests
    except ImportError as e:
        raise CCUnavailable("requests not installed") from e

    access_token = refresh_access_token(creds, requests)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    addr_short = listing["address"].split(",")[0]
    campaign_name = f"Seller Activity Report -- {addr_short} -- {period_id}"

    activity = {
        "format_type": 5,  # custom-code campaign
        "from_email": recipient.get("from_email", "reports@masoncapitalgroup.com"),
        "from_name": "Mason Capital Group",
        "reply_to_email": recipient.get("from_email", "reports@masoncapitalgroup.com"),
        "subject": f"Your activity report is ready -- {addr_short}",
        "html_content": flyer_html,
    }

    if recipient.get("contact_email"):
        activity["contact_list_ids"] = []
        activity["contact_email_addresses"] = [recipient["contact_email"]]
    elif recipient.get("list_id"):
        activity["contact_list_ids"] = [recipient["list_id"]]
    elif recipient.get("segment_id"):
        activity["contact_segment_ids"] = [recipient["segment_id"]]

    payload = {
        "name": campaign_name,
        "email_campaign_activities": [activity],
    }

    resp = requests.post(f"{CC_API_BASE}/emails", headers=headers, json=payload, timeout=20)
    if resp.status_code >= 400:
        raise CCUnavailable(f"CC API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    # NOTE: intentionally no follow-up call to any schedule/send endpoint.
    # The campaign is created and left in DRAFT status for Cameron to
    # review in the Constant Contact UI.
    return data


# ---------------------------------------------------------------------------
# Per-listing driver
# ---------------------------------------------------------------------------
def process_listing(listing: dict, period_id: str, flyers_dir: Path, sources_cfg: dict,
                     base_url: str) -> dict:
    slug = listing["slug"]
    flyer_path = flyers_dir / f"{slug}-{period_id}.html"

    if not flyer_path.exists():
        msg = f"[cc_flyers] {slug}: no flyer HTML found at {flyer_path} -- run bin/generate.py first"
        print(msg, file=sys.stderr)
        return {"slug": slug, "status": "skipped", "reason": "no flyer html"}

    configured, creds, cfg_err = cc_configured(sources_cfg)
    if not configured:
        print(f"flyer HTML ready at {flyer_path}; CC draft skipped (no credentials)")
        return {"slug": slug, "status": "skipped", "reason": cfg_err, "flyer": str(flyer_path)}

    recipient, recip_err = resolve_recipient(listing, sources_cfg)
    if not recipient:
        print(f"flyer HTML ready at {flyer_path}; CC draft skipped (no credentials)")
        return {"slug": slug, "status": "skipped", "reason": recip_err, "flyer": str(flyer_path)}

    try:
        flyer_html = flyer_path.read_text(encoding="utf-8")
        result = create_draft_campaign(listing, period_id, flyer_html, recipient, creds, base_url)
        campaign_id = result.get("campaign_id") or result.get("id")
        print(f"[cc_flyers] {slug}: DRAFT campaign created (id={campaign_id}) from {flyer_path}")
        return {"slug": slug, "status": "draft_created", "campaign_id": campaign_id, "flyer": str(flyer_path)}
    except CCUnavailable as e:
        print(f"flyer HTML ready at {flyer_path}; CC draft skipped (no credentials)")
        print(f"[cc_flyers] {slug}: {e}", file=sys.stderr)
        return {"slug": slug, "status": "skipped", "reason": str(e), "flyer": str(flyer_path)}
    except Exception as e:  # noqa: BLE001 -- a CC failure must never crash the run
        print(f"flyer HTML ready at {flyer_path}; CC draft skipped (no credentials)")
        print(f"[cc_flyers] {slug}: unexpected error: {e}", file=sys.stderr)
        return {"slug": slug, "status": "skipped", "reason": str(e), "flyer": str(flyer_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create Constant Contact DRAFT campaigns from listing flyers.")
    ap.add_argument("--period-id", required=True, help="e.g. 2026-W29, 2026-07, 2026-Q3")
    ap.add_argument("--slug", default="all")
    ap.add_argument("--listings", default=str(REPO_ROOT / "config" / "listings.json"))
    ap.add_argument("--sources", default=str(REPO_ROOT / "config" / "sources.json"))
    ap.add_argument("--flyers-dir", default=str(REPO_ROOT / "out" / "flyers"))
    ap.add_argument("--base-url", default="https://torabic.github.io/mcg-listings")
    args = ap.parse_args(argv)

    listings_cfg = load_json(Path(args.listings), {"listings": []})
    listings = listings_cfg.get("listings", [])
    sources_cfg = load_json(Path(args.sources), {})
    flyers_dir = Path(args.flyers_dir)

    if args.slug != "all":
        listings = [l for l in listings if l["slug"] == args.slug]
        if not listings:
            print(f"[cc_flyers] no listing with slug {args.slug!r}", file=sys.stderr)
            return 1

    results = []
    for listing in listings:
        if listing.get("status") != "active":
            continue
        results.append(process_listing(listing, args.period_id, flyers_dir, sources_cfg, args.base_url))

    n_drafts = sum(1 for r in results if r["status"] == "draft_created")
    n_skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"[cc_flyers] {n_drafts} draft(s) created, {n_skipped} skipped, "
          f"{len(results) - n_drafts - n_skipped} other, for period {args.period_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
