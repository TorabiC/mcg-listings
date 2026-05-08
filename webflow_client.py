"""
MCG Webflow API Client
Creates and publishes listing pages to masoncapitalgroup.com via Webflow v2 API.
Strategy: Creates a static page and injects the full listing HTML via custom code.
"""

import json
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

WEBFLOW_BASE = "https://api.webflow.com/v2"


class WebflowClient:
    def __init__(self, api_token: str, site_id: str):
        self.api_token = api_token
        self.site_id = site_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ── Core API helpers ────────────────────────────────────────────────────

    def _get(self, path: str) -> dict:
        resp = self.session.get(f"{WEBFLOW_BASE}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self.session.post(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        if not resp.ok:
            logger.error(f"Webflow POST {path} failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        resp = self.session.put(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        if not resp.ok:
            logger.error(f"Webflow PUT {path} failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        resp = self.session.patch(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Site ────────────────────────────────────────────────────────────────

    def get_site(self) -> dict:
        return self._get(f"/sites/{self.site_id}")

    def list_pages(self) -> list:
        data = self._get(f"/sites/{self.site_id}/pages")
        return data.get("pages", [])

    # ── Page creation ───────────────────────────────────────────────────────

    # Featured Listings folder on masoncapitalgroup.com
    FEATURED_LISTINGS_FOLDER = "699cb0fbe650ce10414495f1"

    def create_listing_page(self, listing: dict, full_html: str) -> dict:
        """
        Creates a new static page inside the Featured Listings folder on
        masoncapitalgroup.com, with the full listing HTML injected as custom code.
        The page renders with the MCG nav and footer embedded in the HTML.
        """
        slug = _make_slug(listing)
        title = listing.get("page_title") or _fallback_title(listing)
        seo_desc = listing.get("seo_description") or ""
        photo = (listing.get("photos") or [None])[0]

        # Check if page already exists in the folder
        existing = self._find_page_by_slug(slug)
        if existing:
            logger.info(f"Page exists ({existing['id']}) — updating")
            return self._update_page(existing["id"], full_html, title, seo_desc, slug, photo)

        page_data = {
            "title": title,
            "slug": slug,
            "parentId": self.FEATURED_LISTINGS_FOLDER,
            "draft": False,
            "seo": {
                "title": title,
                "description": seo_desc,
                "noIndex": False,
            },
            "openGraph": {
                "title": title,
                "description": seo_desc,
            },
        }

        page = self._post(f"/sites/{self.site_id}/pages", page_data)
        page_id = page["id"]
        logger.info(f"Created page {page_id} at /{slug}")

        self._inject_custom_code(page_id, full_html, listing)
        self._publish_site()

        return {
            "page_id": page_id,
            "url": f"https://masoncapitalgroup.com/property-listings/featured-listings/{slug}",
            "slug": slug,
            "title": title,
        }

    def _update_page(self, page_id: str, full_html: str, title: str,
                     seo_desc: str, slug: str, photo: Optional[str]) -> dict:
        self._patch(f"/pages/{page_id}", {
            "title": title,
            "seo": {"title": title, "description": seo_desc},
        })
        self._inject_custom_code(page_id, full_html, {})
        self._publish_site()
        return {
            "page_id": page_id,
            "url": f"https://masoncapitalgroup.com/property-listings/featured-listings/{slug}",
            "slug": slug,
            "title": title,
        }

    def _inject_custom_code(self, page_id: str, full_html: str, listing: dict):
        """
        Injects the complete self-contained listing HTML (nav + content + footer)
        into the Webflow page. The page was created blank via the API so there are
        no native Webflow elements — our HTML IS the entire page content.
        """
        # Extract everything inside <head> (styles, fonts, schema.org, meta)
        head_match = re.search(r"<head[^>]*>(.*?)</head>", full_html, re.DOTALL)
        head_extras = head_match.group(1) if head_match else ""
        # Strip <title> and <meta> from head_extras — Webflow manages those via SEO settings
        head_extras = re.sub(r"<title[^>]*>.*?</title>", "", head_extras, flags=re.DOTALL)
        head_extras = re.sub(r"<meta\s+(?:name=\"(?:description|viewport)\"[^>]*)>", "", head_extras)
        head_extras = re.sub(r"<meta\s+(?:charset[^>]*)>", "", head_extras)

        # Extract <body> content
        body_match = re.search(r"<body[^>]*>(.*?)</body>", full_html, re.DOTALL)
        body_content = body_match.group(1) if body_match else full_html

        # Head code: fonts, styles, schema.org JSON-LD, OG tags
        head_code = head_extras.strip()

        # Body code: full listing page (nav + gallery + details + footer)
        body_code = f'<div id="mcg-listing-root" style="margin:0;padding:0">{body_content}</div>'

        payload = {
            "headCode": head_code,
            "footerCode": body_code,
        }

        try:
            self._put(f"/pages/{page_id}/custom-code", payload)
            logger.info(f"Custom code injected for page {page_id}")
        except Exception as e:
            logger.warning(f"Custom code PUT failed ({e}), trying v2 envelope")
            try:
                self._put(f"/pages/{page_id}/custom-code", {
                    "customCode": {
                        "head": {"enabled": True, "code": head_code},
                        "body": {"enabled": True, "location": "footer", "code": body_code},
                    }
                })
            except Exception as e2:
                logger.error(f"Both custom code attempts failed: {e2}")
                raise

    def _find_page_by_slug(self, slug: str) -> Optional[dict]:
        try:
            pages = self.list_pages()
            for page in pages:
                if page.get("slug") == slug and page.get("parentId") == self.FEATURED_LISTINGS_FOLDER:
                    return page
        except Exception:
            pass
        return None

    def _publish_site(self):
        """Trigger a site publish so changes go live."""
        try:
            self._post(f"/sites/{self.site_id}/publish", {
                "publishToWebflowSubdomain": True,
                "customDomains": ["masoncapitalgroup.com"],
            })
            logger.info("Site publish triggered")
            time.sleep(2)  # Brief pause for publish to queue
        except Exception as e:
            logger.warning(f"Publish trigger failed (may need manual publish): {e}")

    # ── CMS Collection approach (alternative) ──────────────────────────────

    def get_collections(self) -> list:
        data = self._get(f"/sites/{self.site_id}/collections")
        return data.get("collections", [])

    def find_collection(self, name_contains: str) -> Optional[dict]:
        for col in self.get_collections():
            if name_contains.lower() in col.get("displayName", "").lower():
                return col
        return None

    def create_cms_listing(self, collection_id: str, listing: dict) -> dict:
        """
        Alternative: Creates a CMS item in the Property Listings collection.
        Use this if the site has a CMS collection template that matches the design.
        """
        slug = _make_slug(listing)
        fields = {
            "isArchived": False,
            "isDraft": False,
            "fieldData": {
                "name": f"{listing['address_street']}, {listing['address_city']}, {listing['address_state']}",
                "slug": slug,
                "address": listing.get("address_street", ""),
                "city": listing.get("address_city", ""),
                "state": listing.get("address_state", ""),
                "zip": listing.get("address_zip", ""),
                "price": listing.get("price", 0),
                "price-display": listing.get("price_formatted", ""),
                "status": listing.get("status", "Active"),
                "beds": listing.get("beds", 0),
                "baths": listing.get("baths", 0),
                "square-feet": listing.get("sqft", 0),
                "lot-acres": listing.get("lot_acres_display", ""),
                "year-built": listing.get("year_built", ""),
                "mls-number": listing.get("mls_number", ""),
                "description": listing.get("description", ""),
                "property-type": listing.get("property_type", ""),
                "county": listing.get("county", ""),
                "hoa-fee": listing.get("hoa_fee", 0),
                "main-photo": {"url": listing["photos"][0]} if listing.get("photos") else None,
            },
        }

        result = self._post(f"/collections/{collection_id}/items", fields)
        return result


# ── Utilities ───────────────────────────────────────────────────────────────

def _make_slug(listing: dict) -> str:
    """Address-based slug matching the Featured Listings folder convention."""
    parts = [
        listing.get("address_street", ""),
        listing.get("address_city", ""),
        listing.get("address_state", ""),
    ]
    combined = "-".join(p for p in parts if p)
    slug = re.sub(r"[^a-z0-9]+", "-", combined.lower()).strip("-")
    return slug[:80]


def _fallback_title(listing: dict) -> str:
    street = listing.get("address_street", "")
    city = listing.get("address_city", "")
    state = listing.get("address_state", "AR")
    return f"{street} | {city}, {state} | Mason Capital Group"
