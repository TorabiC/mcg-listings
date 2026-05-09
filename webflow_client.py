"""
MCG Webflow API Client
Pushes listing data to the Property Listings CMS collection on masoncapitalgroup.com.
All fields (including EXPERIENCE images) are editable in the Webflow Editor.
"""

import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

WEBFLOW_BASE = "https://api.webflow.com/v2"

# Property Listings CMS collection ID
PROPERTY_LISTINGS_COLLECTION = "69b9720847f5977730d75f61"


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

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(f"{WEBFLOW_BASE}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self.session.post(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        if not resp.ok:
            logger.error(f"Webflow POST {path} failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        resp = self.session.patch(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        if not resp.ok:
            logger.error(f"Webflow PATCH {path} failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        resp = self.session.put(f"{WEBFLOW_BASE}{path}", json=body, timeout=30)
        if not resp.ok:
            logger.error(f"Webflow PUT {path} failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    # ── Site ────────────────────────────────────────────────────────────────

    def get_site(self) -> dict:
        return self._get(f"/sites/{self.site_id}")

    def list_pages(self) -> list:
        data = self._get(f"/sites/{self.site_id}/pages")
        return data.get("pages", [])

    def get_collections(self) -> list:
        data = self._get(f"/sites/{self.site_id}/collections")
        return data.get("collections", [])

    # ── CMS: push listing ───────────────────────────────────────────────────

    def push_listing_to_cms(self, listing: dict) -> dict:
        """
        Creates or updates a Property Listings CMS item and publishes it.
        All fields — including EXPERIENCE images — are editable in the Webflow Editor.
        Returns {"url": ..., "item_id": ..., "slug": ...}
        """
        collection_id = PROPERTY_LISTINGS_COLLECTION
        slug = listing.get("slug", "listing")
        field_data = self._build_cms_field_data(listing)

        existing = self._find_cms_item_by_slug(collection_id, slug)

        if existing:
            item_id = existing["id"]
            logger.info(f"CMS item exists ({item_id}) — updating")
            self._patch(f"/collections/{collection_id}/items/{item_id}", {
                "isArchived": False,
                "isDraft": False,
                "fieldData": field_data,
            })
        else:
            result = self._post(f"/collections/{collection_id}/items", {
                "isArchived": False,
                "isDraft": False,
                "fieldData": field_data,
            })
            item_id = result["id"]
            logger.info(f"Created CMS item {item_id}")

        # Publish the item
        try:
            self._post(f"/collections/{collection_id}/items/publish", {
                "itemIds": [item_id],
            })
            logger.info(f"Published CMS item {item_id}")
        except Exception as e:
            logger.warning(f"CMS item publish failed: {e}")

        # Trigger full site publish
        self._publish_site()

        return {
            "item_id": item_id,
            "slug": slug,
            "url": f"https://masoncapitalgroup.com/listings/{slug}",
        }

    def _build_cms_field_data(self, listing: dict) -> dict:
        photos = listing.get("photos") or []
        cards = listing.get("location_cards") or []
        agent = listing.get("agent") or {}

        # Description as RichText HTML
        desc_paras = listing.get("description_paragraphs") or []
        description_html = "".join(f"<p>{p}</p>" for p in desc_paras if p)

        # Features as RichText HTML
        features_list = listing.get("features") or []
        if isinstance(features_list, list):
            features_html = "<ul>" + "".join(f"<li>{f}</li>" for f in features_list if f) + "</ul>"
        else:
            features_html = str(features_list) if features_list else ""

        # Key features / highlights
        highlights = listing.get("highlights") or []
        if isinstance(highlights, list):
            highlights_html = "<ul>" + "".join(f"<li>{h}</li>" for h in highlights if h) + "</ul>"
        else:
            highlights_html = ""

        # listing-status option mapping
        raw_status = (listing.get("status") or "Active").strip().lower()
        status_options = {"active": "Active", "pending": "Pending", "sold": "Sold", "coming soon": "Coming Soon"}
        listing_status = status_options.get(raw_status, "Active")

        # listing-type option mapping
        prop_type = (listing.get("property_type") or "Single Family").strip()
        pt_lower = prop_type.lower()
        if "multi" in pt_lower or "multifamily" in pt_lower:
            listing_type_opt = "Multi-Family"
        elif "land" in pt_lower or "lot" in pt_lower or "acreage" in pt_lower:
            listing_type_opt = "Land"
        elif "commercial" in pt_lower or "retail" in pt_lower or "office" in pt_lower or "industrial" in pt_lower:
            listing_type_opt = "Commercial"
        elif "luxury" in pt_lower:
            listing_type_opt = "Luxury"
        else:
            listing_type_opt = "Single Family"

        data = {
            "name": listing.get("address_full") or listing.get("address_street", ""),
            "slug": listing.get("slug", "listing"),
            "street-address": listing.get("address_street", ""),
            "city": listing.get("address_city", ""),
            "state": listing.get("address_state", ""),
            "zip-code": listing.get("address_zip", ""),
            "address": listing.get("address_full", ""),
            "zip": listing.get("address_zip", ""),
            "listing-price": listing.get("price_formatted", ""),
            "price-display": listing.get("price_formatted", ""),
            "square-footage": listing.get("sqft_formatted", ""),
            "lot-size": listing.get("lot_acres_display", ""),
            "year-built": str(listing.get("year_built", "") or ""),
            "zoning": listing.get("zoning", "") or "",
            "mls-number": listing.get("mls_number", "") or "",
            "description": description_html,
            "key-features": highlights_html,
            "features": features_html,
            "property-type": prop_type,
            "neighborhood": listing.get("subdivision", "") or listing.get("county", "") or "",
            "school-district": listing.get("school_district", "") or "",
            "hoa-fees": listing.get("hoa_display", "") or "",
            "meta-title": listing.get("page_title", "") or "",
            "meta-description": listing.get("seo_description", "") or "",
            "contact-name": agent.get("name", "Cameron Torabi"),
            "contact-phone": agent.get("phone", "(858) 500-0222"),
            "contact-email": agent.get("email", "info@masoncapitalgroup.com"),
            "listing-status": listing_status,
            "listing-type": listing_type_opt,
            "status": "OM Generated",
        }

        # Numeric fields — only set if we have a value
        price_float = listing.get("price_float") or 0
        if price_float:
            try:
                data["price"] = int(price_float)
            except (ValueError, TypeError):
                pass

        beds = listing.get("beds")
        if beds:
            try:
                data["bedrooms"] = int(beds)
            except (ValueError, TypeError):
                pass

        baths = listing.get("baths")
        if baths:
            try:
                data["bathrooms"] = int(float(baths))
            except (ValueError, TypeError):
                pass

        # Hero image
        if photos:
            data["hero-image"] = {"url": photos[0], "alt": listing.get("address_street", "")}

        # Gallery images 1–8
        for i in range(1, 9):
            if i <= len(photos):
                data[f"gallery-image-{i}"] = {
                    "url": photos[i - 1],
                    "alt": f"{listing.get('address_street', '')} — photo {i}",
                }

        # EXPERIENCE card images + headings (editable in Webflow Editor)
        for i in range(1, 5):
            if i <= len(cards):
                card = cards[i - 1]
                img_url = card.get("image_url", "")
                heading = card.get("heading", "")
                if img_url:
                    data[f"experience-{i}-image"] = {
                        "url": img_url,
                        "alt": heading,
                    }
                if heading:
                    data[f"experience-{i}-heading"] = heading

        # Optional extras
        garage = listing.get("garage_spaces")
        if garage:
            data["garage"] = f"{garage} Car Garage"

        vt = listing.get("virtual_tour_url")
        if vt:
            data["virtual-tour-url"] = vt

        return data

    def _find_cms_item_by_slug(self, collection_id: str, slug: str) -> Optional[dict]:
        try:
            data = self._get(
                f"/collections/{collection_id}/items",
                params={"slug": slug, "limit": 1},
            )
            items = data.get("items", [])
            return items[0] if items else None
        except Exception as e:
            logger.warning(f"CMS slug lookup failed: {e}")
            return None

    def _publish_site(self):
        """Trigger a site publish so changes go live."""
        try:
            self._post(f"/sites/{self.site_id}/publish", {
                "publishToWebflowSubdomain": True,
                "customDomains": ["masoncapitalgroup.com"],
            })
            logger.info("Site publish triggered")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Publish trigger failed (may need manual publish): {e}")

    # ── Legacy: static page approach (kept for reference) ──────────────────

    def create_listing_page(self, listing: dict, full_html: str) -> dict:
        """
        Legacy: injects full HTML as custom code into a static Webflow page.
        Not editable via Webflow Editor — use push_listing_to_cms() instead.
        """
        slug = _make_slug(listing)
        title = listing.get("page_title") or _fallback_title(listing)
        seo_desc = listing.get("seo_description") or ""

        existing = self._find_page_by_slug(slug)
        if existing:
            return self._update_page(existing["id"], full_html, title, seo_desc, slug)

        page_data = {
            "title": title,
            "slug": slug,
            "parentId": "699cb0fbe650ce10414495f1",
            "draft": False,
            "seo": {"title": title, "description": seo_desc, "noIndex": False},
            "openGraph": {"title": title, "description": seo_desc},
        }
        page = self._post(f"/sites/{self.site_id}/pages", page_data)
        page_id = page["id"]
        self._inject_custom_code(page_id, full_html, listing)
        self._publish_site()
        return {
            "page_id": page_id,
            "url": f"https://masoncapitalgroup.com/property-listings/featured-listings/{slug}",
            "slug": slug,
            "title": title,
        }

    def _update_page(self, page_id, full_html, title, seo_desc, slug):
        self._patch(f"/pages/{page_id}", {"title": title, "seo": {"title": title, "description": seo_desc}})
        self._inject_custom_code(page_id, full_html, {})
        self._publish_site()
        return {
            "page_id": page_id,
            "url": f"https://masoncapitalgroup.com/property-listings/featured-listings/{slug}",
            "slug": slug,
        }

    def _inject_custom_code(self, page_id, full_html, listing):
        import re as _re
        head_match = _re.search(r"<head[^>]*>(.*?)</head>", full_html, _re.DOTALL)
        head_extras = head_match.group(1) if head_match else ""
        head_extras = _re.sub(r"<title[^>]*>.*?</title>", "", head_extras, flags=_re.DOTALL)
        head_extras = _re.sub(r"<meta\s+(?:name=\"(?:description|viewport)\"[^>]*)>", "", head_extras)
        head_extras = _re.sub(r"<meta\s+(?:charset[^>]*)>", "", head_extras)
        body_match = _re.search(r"<body[^>]*>(.*?)</body>", full_html, _re.DOTALL)
        body_content = body_match.group(1) if body_match else full_html
        payload = {
            "headCode": head_extras.strip(),
            "footerCode": f'<div id="mcg-listing-root">{body_content}</div>',
        }
        try:
            self._put(f"/pages/{page_id}/custom-code", payload)
        except Exception as e:
            logger.error(f"Custom code inject failed: {e}")
            raise

    def _find_page_by_slug(self, slug):
        try:
            pages = self.list_pages()
            for page in pages:
                if page.get("slug") == slug and page.get("parentId") == "699cb0fbe650ce10414495f1":
                    return page
        except Exception:
            pass
        return None


# ── Utilities ────────────────────────────────────────────────────────────────

def _make_slug(listing: dict) -> str:
    parts = [listing.get("address_street", ""), listing.get("address_city", ""), listing.get("address_state", "")]
    combined = "-".join(p for p in parts if p)
    slug = re.sub(r"[^a-z0-9]+", "-", combined.lower()).strip("-")
    return slug[:80]


def _fallback_title(listing: dict) -> str:
    street = listing.get("address_street", "")
    city = listing.get("address_city", "")
    state = listing.get("address_state", "AR")
    return f"{street} | {city}, {state} | Mason Capital Group"
