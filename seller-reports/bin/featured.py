#!/usr/bin/env python3
"""
featured.py -- MCG Seller Activity Report System, Featured-Listings scoping.

Source of truth for "which listings should get a seller report this run":
the live MCG website Featured Listings page, backed by the Webflow CMS
collection "Featured Listings Cards"
  collection_id = 6a08a82391ef70c6df6c599c
  site_id       = 699cb0b733f309dd4bda1b56
via the Webflow Data API v2:
  GET https://api.webflow.com/v2/collections/{collection_id}/items?limit=100

Auth: Bearer token read from the file at config/sources.json ->
webflow.token_file (never printed, never logged, never committed).

FEATURED, per item, iff `not item["isDraft"] and not item["isArchived"]`.
Anything else (draft, archived, or both) is not currently live on the
Featured Listings page and is skipped.

Card slug -> seller-reports registry slug mapping lives in
config/featured_map.json (committed, hand-resolved -- see that file's
_comment and resolve_map_from_api() below for how it was derived/how to
re-derive it). get_featured_slugs() is deliberately dumb about *how* a
card maps to a registry slug -- it only trusts featured_map.json -- so a
CMS re-slug or a genuinely new listing card requires a human (or a re-run
of resolve_map_from_api()) to extend the map, not silent guessing.

get_featured_slugs() is the one function collect.py / generate.py need:
    from featured import get_featured_slugs
    result = get_featured_slugs()
    result.slugs           -> list[str] of registry slugs to process
    result.skipped_cards   -> [(card_slug, reason), ...] for logging
    result.source          -> "live" | "fallback"
    result.warning         -> str | None (set when source == "fallback")

Never returns an empty slug list silently: if the Webflow API is
unreachable, unauthenticated, or returns something unparseable, this
degrades to "all registry listings" (every slug in config/listings.json)
with a loud stderr warning and result.source == "fallback" -- callers can
inspect that flag but should NOT treat a fallback as fatal, per this
system's "missing/uncredentialed sources never raise, never silently
zero" policy (see collect.py / generate.py module docstrings).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover - requests is a stated dependency
    requests = None

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
CONFIG_DIR = ROOT / "config"

COLLECTION_ID = "6a08a82391ef70c6df6c599c"
SITE_ID = "699cb0b733f309dd4bda1b56"
API_BASE = "https://api.webflow.com/v2"
HTTP_TIMEOUT = 15
PAGE_LIMIT = 100


@dataclass
class FeaturedResult:
    slugs: list[str] = field(default_factory=list)
    skipped_cards: list[tuple[str, str]] = field(default_factory=list)
    source: str = "live"          # "live" | "fallback"
    warning: str | None = None
    total_cards_seen: int = 0
    featured_cards_seen: int = 0


# ---------------------------------------------------------------------------
# Config / token
# ---------------------------------------------------------------------------
def _load_sources_cfg() -> dict:
    path = CONFIG_DIR / "sources.json"
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _secrets_dir_fallback(p: Path) -> Path:
    """If MCG_SECRETS_DIR is set and configured path `p` doesn't exist on
    disk (e.g. a local Mac run where secrets live under $HOME/Documents/
    Second Brain/.secrets instead of the cloud-session path baked into
    config/sources.json), retry by basename inside MCG_SECRETS_DIR. No-op
    when MCG_SECRETS_DIR is unset or the fallback file also doesn't exist
    -- callers already treat a non-existent path as "credential missing"
    and never raise, so this never changes cloud-session behavior."""
    secrets_dir = os.environ.get("MCG_SECRETS_DIR")
    if not secrets_dir:
        return p
    candidate = Path(secrets_dir) / p.name
    return candidate if candidate.exists() else p


def _resolve_token() -> str | None:
    cfg = _load_sources_cfg().get("webflow", {})
    token_file = cfg.get("token_file") or cfg.get("credential_env_or_path")
    if not token_file:
        return None
    p = Path(token_file)
    if not p.exists():
        p = _secrets_dir_fallback(p)
    if not p.exists():
        return None
    raw = p.read_text().strip()
    return raw or None


def load_featured_map() -> dict:
    path = CONFIG_DIR / "featured_map.json"
    if not path.exists():
        return {}
    with open(path, "r") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def load_registry_slugs() -> list[str]:
    path = CONFIG_DIR / "listings.json"
    if not path.exists():
        return []
    with open(path, "r") as f:
        data = json.load(f)
    return [l["slug"] for l in data.get("listings", [])]


# ---------------------------------------------------------------------------
# Webflow API
# ---------------------------------------------------------------------------
def fetch_collection_items(token: str, collection_id: str = COLLECTION_ID) -> list[dict]:
    """Fetch every item in the collection (paginated), raising on any
    network/auth/parse failure -- callers (get_featured_slugs) catch and
    fall back. Never called with sample/demo data; this always hits the
    real API."""
    if requests is None:
        raise RuntimeError("the 'requests' package is required for featured.py")

    items: list[dict] = []
    offset = 0
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    while True:
        resp = requests.get(
            f"{API_BASE}/collections/{collection_id}/items",
            headers=headers,
            params={"limit": PAGE_LIMIT, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        page_items = body.get("items", [])
        items.extend(page_items)
        pagination = body.get("pagination", {}) or {}
        total = pagination.get("total", len(items))
        offset += len(page_items)
        if not page_items or offset >= total:
            break
    return items


def is_featured(item: dict) -> bool:
    return not item.get("isDraft") and not item.get("isArchived")


# ---------------------------------------------------------------------------
# Diagnostic helper -- NOT used at runtime by collect.py/generate.py, but
# kept here so a future maintainer can re-derive/audit featured_map.json
# without hand-copying JSON out of a curl response. Matches each card's
# fieldData['webflow-page-url'] against each registry listing's
# links.webflow_page (exact suffix match) -- this is the same method used
# to originally resolve config/featured_map.json on 2026-08-05, including
# the 3 cards (hwy-49-storage-facility, hwy-49-ar-72-corridor,
# downtown-rogers-corner) that couldn't be resolved by slug alone.
# ---------------------------------------------------------------------------
def resolve_map_from_api(token: str) -> tuple[dict, list[str]]:
    items = fetch_collection_items(token)
    registry = load_registry_slugs()
    reg_by_path = {}
    listings_cfg = json.load(open(CONFIG_DIR / "listings.json"))["listings"]
    for l in listings_cfg:
        wp = (l.get("links") or {}).get("webflow_page") or ""
        path = wp.split("masoncapitalgroup.com", 1)[-1].rstrip("/")
        if path:
            reg_by_path[path] = l["slug"]

    mapping, unmatched = {}, []
    for it in items:
        fd = it.get("fieldData", {})
        card_slug = fd.get("slug")
        url = (fd.get("webflow-page-url") or "").rstrip("/")
        reg_slug = reg_by_path.get(url)
        if reg_slug:
            mapping[card_slug] = reg_slug
        else:
            unmatched.append(card_slug)
    return mapping, unmatched


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------
def get_featured_slugs() -> FeaturedResult:
    """Live-fetch the Featured Listings Cards collection, map FEATURED
    items through config/featured_map.json, and return the resulting
    registry slugs. Never raises; never silently returns zero listings --
    any failure degrades to "all registry listings" with a loud warning."""
    registry_slugs = load_registry_slugs()

    token = _resolve_token()
    if not token:
        msg = ("[featured] WARNING: no Webflow CMS token configured "
               "(config/sources.json -> webflow.token_file) -- FALLING BACK "
               "to all registry listings. Reports will run for every listing "
               "in config/listings.json, including any not currently featured "
               "on the website. Fix the credential and re-run to restore "
               "featured-only scoping.")
        print(msg, file=sys.stderr)
        return FeaturedResult(slugs=list(registry_slugs), source="fallback", warning=msg)

    try:
        items = fetch_collection_items(token)
    except Exception as exc:  # noqa: BLE001 -- any failure here must degrade, not raise
        msg = (f"[featured] WARNING: Webflow CMS collection fetch failed ({exc!r}) -- "
               "FALLING BACK to all registry listings. Reports will run for every "
               "listing in config/listings.json, including any not currently "
               "featured on the website. Re-run once the API is reachable to "
               "restore featured-only scoping.")
        print(msg, file=sys.stderr)
        return FeaturedResult(slugs=list(registry_slugs), source="fallback", warning=msg)

    fmap = load_featured_map()
    result = FeaturedResult(source="live", total_cards_seen=len(items))

    seen_registry_slugs: set[str] = set()
    for item in items:
        fd = item.get("fieldData", {})
        card_slug = fd.get("slug") or item.get("id", "<unknown>")
        if not is_featured(item):
            reason = "draft" if item.get("isDraft") else "archived"
            result.skipped_cards.append((card_slug, f"not featured on site ({reason})"))
            continue
        result.featured_cards_seen += 1
        reg_slug = fmap.get(card_slug)
        if not reg_slug:
            result.skipped_cards.append((card_slug, "no featured_map.json entry -- unmapped card"))
            continue
        if reg_slug not in registry_slugs:
            result.skipped_cards.append((card_slug, f"maps to {reg_slug!r} which is not in config/listings.json"))
            continue
        seen_registry_slugs.add(reg_slug)

    result.slugs = sorted(seen_registry_slugs)

    if not result.slugs:
        # Every item fetched successfully but nothing resolved to a live,
        # mapped, registry-known listing -- still never zero silently.
        msg = ("[featured] WARNING: Webflow fetch succeeded but resolved ZERO "
               "featured registry listings -- FALLING BACK to all registry "
               "listings rather than running reports for nobody. Check "
               "config/featured_map.json and the Featured Listings CMS "
               "collection.")
        print(msg, file=sys.stderr)
        return FeaturedResult(
            slugs=list(registry_slugs), source="fallback", warning=msg,
            skipped_cards=result.skipped_cards, total_cards_seen=result.total_cards_seen,
            featured_cards_seen=result.featured_cards_seen,
        )

    return result


def _main() -> int:
    """Standalone smoke test: print the live featured slug list + mapping
    evidence. Not used by collect.py/generate.py (they import
    get_featured_slugs directly) -- this is for `python3 bin/featured.py`."""
    result = get_featured_slugs()
    print(f"[featured] source={result.source}  cards_seen={result.total_cards_seen}  "
          f"featured_cards_seen={result.featured_cards_seen}  registry_slugs={len(result.slugs)}")
    print()
    print("Featured registry slugs:")
    for s in result.slugs:
        print(f"  - {s}")
    if result.skipped_cards:
        print()
        print("Skipped cards:")
        for card_slug, reason in result.skipped_cards:
            print(f"  - {card_slug}: {reason}")
    if result.warning:
        print()
        print(f"WARNING: {result.warning}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
