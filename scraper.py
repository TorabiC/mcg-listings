"""
MCG Listing Scraper
Extracts property data from MLS URLs (Zillow, Realtor.com, Redfin, generic IDX).
Falls back to Claude AI for any platform it can't parse directly.
"""

import re
import json
import time
import logging
from urllib.parse import urlparse, urlencode
from typing import Optional

import requests
from bs4 import BeautifulSoup
import anthropic

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

TIMEOUT = 20


def scrape_listing(url: str, api_key: str) -> dict:
    """
    Main entry point. Returns a normalized listing dict from any supported MLS URL.
    Uses Playwright for JS-rendered pages (MLS Matrix, etc.) to capture photos.
    """
    domain = urlparse(url).netloc.lower()

    try:
        if "zillow.com" in domain:
            data = _scrape_zillow(url)
        elif "realtor.com" in domain:
            data = _scrape_realtor(url)
        elif "redfin.com" in domain:
            data = _scrape_redfin(url)
        else:
            # Use Playwright to get fully rendered HTML + photos for JS-rendered MLS pages
            rendered_html, photos = _fetch_rendered(url)
            data = _extract_with_claude(rendered_html, url, api_key)
            # Playwright photos take priority over anything Claude may have extracted
            if photos:
                data["photos"] = photos
                logger.info(f"Playwright extracted {len(photos)} photos")

        # Always enhance/fill gaps with Claude
        data = _enhance_with_claude(data, url, api_key)
        return data

    except Exception as e:
        logger.error(f"Scrape failed for {url}: {e}", exc_info=True)
        try:
            html = _fetch_html(url)
            data = _extract_with_claude(html, url, api_key)
            return _enhance_with_claude(data, url, api_key)
        except Exception as e2:
            logger.error(f"Claude fallback also failed: {e2}")
            return _empty_listing()


def _fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _fetch_rendered(url: str) -> tuple:
    """
    Load URL in a headless Chromium browser via Playwright.
    Returns (rendered_html, photo_urls).
    Falls back to requests if Playwright fails.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — falling back to requests")
        return _fetch_html(url), []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 900},
            )
            page.goto(url, wait_until="networkidle", timeout=30000)

            # ── Phase 1: scroll to trigger all lazy-loaded gallery images ─────
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.3)")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)

            # ── Phase 2: extract full-size URLs from HTML source ──────────────
            # MLS Matrix encodes all GetMedia URLs in the page source.
            # Size=3 = full-size; Size=1 = thumbnail. Prefer Size=3.
            html_content = page.content()

            import re as _re

            def _clean_url(u):
                return u.strip().rstrip("\\").rstrip('"').rstrip("'").replace("&amp;", "&")

            # Full-size photos (Size=3) — extract from HTML source
            large_urls = _re.findall(
                r'https?://[^"\'>\s\\]*GetMedia\.ashx[^"\'>\s\\]*Type=1[^"\'>\s\\]*Size=3[^"\'>\s\\]*',
                html_content
            )
            seen_urls = set()
            photo_candidates = []
            for u in large_urls:
                u = _clean_url(u)
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    photo_candidates.append(u)

            # Also scan data-src / data-lazy for lazy-loaded galleries
            lazy_srcs = page.eval_on_selector_all(
                "[data-src],[data-lazy],[data-original],[data-img]",
                "els => els.map(e => e.dataset.src || e.dataset.lazy || e.dataset.original || e.dataset.img || '')"
            )
            for s in lazy_srcs:
                s = _clean_url(s)
                if s and "GetMedia" in s and "Type=1" in s and s not in seen_urls:
                    seen_urls.add(s)
                    photo_candidates.append(s)

            # ── Phase 3: fallback — rendered img srcs (catches non-MLS sites) ─
            all_imgs = page.eval_on_selector_all(
                "img",
                "els => els.map(e => ({ src: e.src, natural_w: e.naturalWidth }))"
            )
            for img in all_imgs:
                src = _clean_url(img.get("src", ""))
                if not src or src in seen_urls:
                    continue
                if img.get("natural_w", 0) < 400:
                    continue
                if any(x in src for x in ["icon", "logo", "svg", "avatar", ".gif", "agent", "Type=8", "Type=2", "Size=1", "Size=2"]):
                    continue
                seen_urls.add(src)
                photo_candidates.append(src)

            photos = photo_candidates
            rendered_html = page.content()
            browser.close()

            logger.info(f"Playwright rendered {url}: {len(photos)} photos found")
            return rendered_html, photos

    except Exception as e:
        logger.warning(f"Playwright failed ({e}), falling back to requests")
        return _fetch_html(url), []


# ─── Platform-specific scrapers ────────────────────────────────────────────────

def _scrape_zillow(url: str) -> dict:
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # Zillow embeds all listing data in __NEXT_DATA__ JSON
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        # Try older REDUX_STATE pattern
        script = soup.find("script", string=re.compile(r"__REDUX_STATE__"))

    if script and script.string:
        try:
            raw = json.loads(script.string)
            props = _dig(raw, "props", "pageProps", "componentProps", "gdpClientCache")
            if props:
                # gdpClientCache is a JSON string inside the JSON
                cache = json.loads(props) if isinstance(props, str) else props
                zpid_data = next(iter(cache.values()), {})
                listing = zpid_data.get("property", zpid_data)
                return _normalize_zillow(listing)
        except Exception as e:
            logger.warning(f"Zillow JSON parse failed: {e}")

    # Fallback: parse HTML meta tags
    return _parse_meta_fallback(soup, url)


def _normalize_zillow(z: dict) -> dict:
    address = z.get("address", {})
    price = z.get("price", z.get("listingSubType", {}).get("price", 0))
    return {
        "address_street": address.get("streetAddress", ""),
        "address_city": address.get("city", ""),
        "address_state": address.get("state", ""),
        "address_zip": address.get("zipcode", ""),
        "price": _to_int(price),
        "status": z.get("homeStatus", "Active").replace("_", " ").title(),
        "beds": z.get("bedrooms", 0),
        "baths": z.get("bathrooms", 0),
        "sqft": _to_int(z.get("livingArea", 0)),
        "lot_sqft": _to_int(z.get("lotSize", 0)),
        "year_built": z.get("yearBuilt"),
        "property_type": z.get("homeType", "Single Family"),
        "description": z.get("description", ""),
        "photos": [p.get("url", p) if isinstance(p, dict) else p
                   for p in z.get("photos", [])],
        "hoa_fee": z.get("monthlyHoaFee", 0),
        "mls_number": z.get("mlsid", z.get("zpid", "")),
        "days_on_market": z.get("daysOnZillow", 0),
        "tax_annual": z.get("taxAnnualAmount", 0),
        "virtual_tour_url": z.get("virtualTour", {}).get("tourUrl") if z.get("virtualTour") else None,
        "source": "zillow",
    }


def _scrape_realtor(url: str) -> dict:
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # Realtor.com uses JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") == "SingleFamilyResidence" or "price" in data:
                return _normalize_realtor(data, soup)
        except Exception:
            continue

    # Fallback: try __APP_STATE__
    for script in soup.find_all("script"):
        text = script.string or ""
        if "window.__APP_STATE__" in text:
            try:
                match = re.search(r"window\.__APP_STATE__\s*=\s*(\{.+?\});", text, re.DOTALL)
                if match:
                    state = json.loads(match.group(1))
                    listing = _dig(state, "property", "data")
                    if listing:
                        return _normalize_realtor(listing, soup)
            except Exception:
                pass

    return _parse_meta_fallback(soup, url)


def _normalize_realtor(r: dict, soup: BeautifulSoup) -> dict:
    addr = r.get("address", {})
    return {
        "address_street": addr.get("streetAddress", r.get("name", "")),
        "address_city": addr.get("addressLocality", ""),
        "address_state": addr.get("addressRegion", ""),
        "address_zip": addr.get("postalCode", ""),
        "price": _to_int(r.get("price", r.get("offers", {}).get("price", 0))),
        "status": "Active",
        "beds": _to_int(r.get("numberOfRooms", r.get("bedrooms", 0))),
        "baths": _to_float(r.get("numberOfBathroomsTotal", r.get("bathrooms", 0))),
        "sqft": _to_int(r.get("floorSize", {}).get("value", 0)),
        "lot_sqft": 0,
        "year_built": r.get("yearBuilt"),
        "property_type": r.get("@type", "Single Family"),
        "description": r.get("description", ""),
        "photos": _extract_og_images(soup),
        "hoa_fee": 0,
        "mls_number": "",
        "days_on_market": 0,
        "tax_annual": 0,
        "virtual_tour_url": None,
        "source": "realtor",
    }


def _scrape_redfin(url: str) -> dict:
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    for script in soup.find_all("script"):
        text = script.string or ""
        if "window.__reactServerState" in text or "reactServerState" in text:
            try:
                match = re.search(r"reactServerState\s*=\s*(\{.+)", text, re.DOTALL)
                if match:
                    # Clean and parse
                    raw = match.group(1).rstrip(";")
                    data = json.loads(raw)
                    listing = _dig(data, "ReduxState", "pageData", "property")
                    if listing:
                        return _normalize_redfin(listing, soup)
            except Exception:
                pass

    return _parse_meta_fallback(soup, url)


def _normalize_redfin(r: dict, soup: BeautifulSoup) -> dict:
    basic = r.get("basicInfo", r)
    addr = basic.get("address", {})
    return {
        "address_street": addr.get("streetNumber", "") + " " + addr.get("streetName", ""),
        "address_city": addr.get("city", ""),
        "address_state": addr.get("stateOrProvince", ""),
        "address_zip": addr.get("zip", ""),
        "price": _to_int(basic.get("price", basic.get("listingPrice", {}).get("amount", 0))),
        "status": "Active",
        "beds": basic.get("beds", 0),
        "baths": basic.get("baths", 0),
        "sqft": _to_int(basic.get("sqFt", {}).get("value", 0)),
        "lot_sqft": _to_int(basic.get("lotSqFt", {}).get("value", 0)),
        "year_built": basic.get("yearBuilt"),
        "property_type": "Single Family",
        "description": r.get("remarks", ""),
        "photos": _extract_og_images(soup),
        "hoa_fee": 0,
        "mls_number": basic.get("mlsId", {}).get("value", ""),
        "days_on_market": basic.get("daysOnMarket", 0),
        "tax_annual": 0,
        "virtual_tour_url": None,
        "source": "redfin",
    }


# ─── HTML meta tag fallback ─────────────────────────────────────────────────

def _parse_meta_fallback(soup: BeautifulSoup, url: str) -> dict:
    def meta(name):
        tag = soup.find("meta", property=name) or soup.find("meta", attrs={"name": name})
        return tag.get("content", "").strip() if tag else ""

    title = soup.title.string if soup.title else ""
    price_match = re.search(r'\$[\d,]+', title or "")

    return {
        "address_street": meta("og:title").split("|")[0].strip(),
        "address_city": "",
        "address_state": "",
        "address_zip": "",
        "price": _to_int((price_match.group(0) if price_match else "0").replace("$", "").replace(",", "")),
        "status": "Active",
        "beds": 0,
        "baths": 0,
        "sqft": 0,
        "lot_sqft": 0,
        "year_built": None,
        "property_type": "Single Family",
        "description": meta("og:description") or meta("description"),
        "photos": [meta("og:image")] if meta("og:image") else [],
        "hoa_fee": 0,
        "mls_number": "",
        "days_on_market": 0,
        "tax_annual": 0,
        "virtual_tour_url": None,
        "source": "meta_fallback",
    }


# ─── JSON helpers ──────────────────────────────────────────────────────────

def _safe_json_parse(raw: str) -> dict:
    """Parse JSON, repairing truncated responses by closing open structures."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Trim to last complete key-value pair by walking back from end
    # Close any open arrays/objects to make it valid
    depth_map = {'{': '}', '[': ']'}
    close_map = {'}': '{', ']': '['}
    stack = []
    in_string = False
    escape_next = False

    for i, ch in enumerate(raw):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in depth_map:
            stack.append(depth_map[ch])
        elif ch in close_map and stack and stack[-1] == ch:
            stack.pop()

    # Append missing closing chars
    closing = "".join(reversed(stack))
    repaired = raw.rstrip().rstrip(",") + closing
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        logger.warning("JSON repair failed, returning empty dict")
        return {}


# ─── Claude AI extraction ──────────────────────────────────────────────────

def _extract_with_claude(html: str, url: str, api_key: str) -> dict:
    """Extract listing data from page HTML using Claude."""
    client = anthropic.Anthropic(api_key=api_key)

    # Clean to readable text — far more token-efficient and accurate than raw HTML
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    page_text = soup.get_text(separator="\n", strip=True)
    # Remove blank lines and cap at 15k chars (plenty for any listing page)
    lines = [l for l in page_text.splitlines() if l.strip()]
    page_text = "\n".join(lines)[:15000]

    prompt = f"""Extract ALL property listing data from this MLS page. The listing may be any type: residential, land, commercial, rental, investment, or development — extract what applies.

URL: {url}

Return a JSON object. Use null for fields that don't apply or aren't present:
{{
  "address_street": "street address only e.g. '146 Northfleet Ln' or 'Whithorn Lane'",
  "address_city": "city name",
  "address_state": "2-letter state code",
  "address_zip": "zip code",
  "price": 590000,
  "status": "Active | Pending | Sold | For Lease | Coming Soon",
  "property_type": "Single Family | Condo | Townhome | Multi-Family | Land | Lots | Office | Retail | Industrial | Commercial",
  "listing_type": "residential | land | commercial | rental | investment | development",

  "beds": 3,
  "baths": 2.5,
  "sqft": 2135,
  "garage_spaces": 2,
  "stories": 1,
  "year_built": 2020,
  "new_construction": false,

  "lot_sqft": 13068,
  "lot_acres": 0.30,
  "total_acres": 0.34,
  "price_per_acre": 52941,

  "units": null,
  "gross_rent_annual": null,
  "noi": null,
  "cap_rate": null,
  "lease_type": null,
  "occupancy_rate": null,

  "zoning": null,
  "zoning_description": null,
  "max_units": null,
  "far": null,
  "topography": null,
  "road_frontage": null,
  "utilities_available": [],
  "entitlements": [],

  "description": "full property description text",
  "highlights": [],
  "features_general": [],
  "features_interior": [],
  "features_kitchen": [],
  "features_exterior": [],
  "features_utilities": [],
  "features_location": [],

  "photos": [],
  "virtual_tour_url": null,

  "hoa_fee": null,
  "mls_number": "1234567",
  "days_on_market": 5,
  "tax_annual": null,
  "listing_date": null,

  "school_elementary": null,
  "school_middle": null,
  "school_high": null,
  "school_district": null,
  "county": null,
  "subdivision": null,
  "apn": null
}}

PAGE TEXT:
{page_text}

Return ONLY valid JSON. No explanation."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return _safe_json_parse(raw)


def _enhance_with_claude(data: dict, url: str, api_key: str) -> dict:
    """
    Fill in missing fields and generate compelling MCG-quality content.
    Generates: highlights, feature lists, description paragraphs, schools, comparables,
    tax estimates, loan payment breakdown, location content, environmental scores.
    """
    client = anthropic.Anthropic(api_key=api_key)

    addr = f"{data.get('address_street', '')}, {data.get('address_city', '')}, {data.get('address_state', '')} {data.get('address_zip', '')}"
    price = data.get("price") or 0
    price_int = int(price) if price else 0
    beds = data.get("beds") or 0
    baths = data.get("baths") or 0
    sqft = data.get("sqft") or 0
    lot_acres = data.get("total_acres") or data.get("lot_acres") or (
        data.get("lot_sqft", 0) / 43560 if data.get("lot_sqft") else None
    )
    year_built = data.get("year_built")
    city = data.get("address_city", "")
    state = data.get("address_state", "")
    listing_type = data.get("listing_type", "residential")
    prop_type = data.get("property_type", "Single Family")

    # Build type-specific stats summary
    if listing_type in ("land", "development"):
        stats_summary = f"Acres: {f'{lot_acres:.2f}' if lot_acres else 'unknown'} | Zoning: {data.get('zoning') or 'unknown'} | Topography: {data.get('topography') or 'unknown'}"
    elif listing_type in ("commercial", "investment", "rental"):
        stats_summary = f"Sqft: {sqft:,} | Units: {data.get('units') or 'N/A'} | Cap Rate: {data.get('cap_rate') or 'unknown'}"
    else:
        stats_summary = f"Beds: {beds} | Baths: {baths} | Sqft: {sqft:,} | Lot: {f'{lot_acres:.2f} acres' if lot_acres else 'unknown'}"

    # Build type-specific JSON template sections
    if listing_type in ("land", "development"):
        comps_template = '[{{"address": "nearby land parcel", "status": "Active", "price": "$XX,XXX", "acres": "X.XX", "ppa": "$XX,XXX/ac"}}]'
        extra_fields = f'"topography": "{data.get("topography") or "unknown"}", "road_frontage": "{data.get("road_frontage") or "unknown"}", "utilities_available": {data.get("utilities_available") or []},'
        loan_section = '"loan_estimate": {{"down_payment_pct": 25, "loan_term": 15, "interest_rate": 7.5}},'
        schools_section = ""
    elif listing_type in ("commercial", "investment", "rental"):
        comps_template = '[{{"address": "nearby property", "status": "Active", "price": "$X,XXX,XXX", "units": X, "cap_rate": "X.X%", "noi": "$XX,XXX"}}]'
        extra_fields = f'"cap_rate": {data.get("cap_rate") or 0}, "noi": {data.get("noi") or 0}, "lease_type": "{data.get("lease_type") or ""}",'
        loan_section = ""
        schools_section = ""
    else:
        comps_template = '[{{"address": "nearby address", "status": "Active", "price": "$XXX,XXX", "beds": X, "baths": X, "sqft": "X,XXX", "ppsf": "$XXX"}}]'
        extra_fields = f'"garage_spaces": {data.get("garage_spaces") or 2}, "stories_label": "Single Story | Two Story",'
        loan_section = f'"loan_estimate": {{"down_payment_pct": 20, "loan_term": 30, "interest_rate": 6.89}},'
        schools_section = '''"schools": [
    {{"name": "Elementary School", "grades": "PK-4", "type": "Public", "distance": "0.8 mi", "rating": 8, "level": "E"}},
    {{"name": "Middle School", "grades": "5-8", "type": "Public", "distance": "1.2 mi", "rating": 8, "level": "M"}},
    {{"name": "High School", "grades": "9-12", "type": "Public", "distance": "1.8 mi", "rating": 9, "level": "H"}}
  ],'''

    prompt = f"""You are a top-tier real estate copywriter and data analyst for Mason Capital Group, a luxury investment-focused brokerage in Northwest Arkansas. Generate comprehensive, investor-grade listing page content for this property.

PROPERTY DATA:
Address: {addr}
Price: ${price_int:,}
Type: {prop_type} ({listing_type})
{stats_summary}
Year Built: {year_built or 'N/A'}
HOA: {data.get('hoa_fee') or 0}/month
MLS#: {data.get('mls_number', '')}
Existing description: {(data.get('description') or '')[:500]}
Existing highlights: {data.get('highlights', [])}

Generate a JSON object with ALL of the following (use real location data for {city}, {state}):

{{
  "highlights": ["up to 9 concise bullet highlights relevant to this listing type"],
  "description_paragraphs": ["paragraph 1 (~80 words, property overview)", "paragraph 2 (~70 words, details/features)", "paragraph 3 (~70 words, location/investment angle)"],
  "features_general": ["MLS# ...", "{prop_type}", "County: ...", "DOM: X", ...4-6 items],
  "features_interior": [...relevant interior features or [] for land],
  "features_kitchen": [...relevant or [] for land/commercial],
  "features_exterior": [...relevant exterior or site features],
  "features_utilities": [...utilities available],
  "features_location": ["School District: ...", "Near ...", ...5 items],
  {schools_section}
  "tax_history": [
    {{"year": 2025, "assessed": "$XX,XXX", "amount": "$XXX", "change": "—"}},
    {{"year": 2024, "assessed": "$XX,XXX", "amount": "$XXX", "change": "+X.X%"}},
    {{"year": 2023, "assessed": "$XX,XXX", "amount": "$XXX", "change": "+X.X%"}}
  ],
  "comparable_homes": {comps_template},
  "property_history": [
    {{"date": "2025", "event": "Listed for Sale", "price": "${price_int:,}", "change": "—", "source": "NABOR MLS"}}
  ],
  {loan_section}
  "location_tagline": "The {city} Advantage",
  "location_description": "~60 word description of why this location is compelling for buyers/investors",
  "location_cards": [
    {{
      "tagline": "Recreation",
      "heading": "Specific heading about this property's nearby recreation",
      "body": "~50 words specific to this property's actual recreational assets (trails, parks, lakes, etc.)",
      "cta": "Explore →",
      "cta_url": "https://masoncapitalgroup.com",
      "image_url": "A real, publicly accessible image URL showing the SPECIFIC recreational asset named in this card (e.g. the actual lake, trail, or park). Use Wikimedia Commons (upload.wikimedia.org/wikipedia/commons/...) or a known public CDN. The URL must be a direct image link ending in .jpg, .jpeg, or .png. Must be VISUALLY DIFFERENT from other cards."
    }},
    {{
      "tagline": "Community",
      "heading": "Specific heading about this property's community/neighborhood",
      "body": "~50 words specific to the actual neighborhood, downtown, restaurants, culture",
      "cta": "Learn More →",
      "cta_url": "https://masoncapitalgroup.com",
      "image_url": "A real, publicly accessible image URL showing the SPECIFIC downtown, neighborhood, or community landmark named in this card. Use Wikimedia Commons or a known public CDN. Direct image link ending in .jpg, .jpeg, or .png. Must be VISUALLY DIFFERENT from other cards."
    }},
    {{
      "tagline": "Investment",
      "heading": "Specific heading about the investment/growth case for this market",
      "body": "~50 words with real data points about population growth, vacancy, development pipeline, economic drivers",
      "cta": "Market Insights →",
      "cta_url": "https://masoncapitalgroup.com",
      "image_url": "A real, publicly accessible image URL showing economic growth, a major employer, or development in this market. Use Wikimedia Commons or a known public CDN. Direct image link ending in .jpg, .jpeg, or .png. Must be VISUALLY DIFFERENT from other cards."
    }},
    {{
      "tagline": "Lifestyle",
      "heading": "Specific heading about the lifestyle this location enables",
      "body": "~50 words about the live/work/play environment — dining, arts, university, walkability, etc.",
      "cta": "Explore →",
      "cta_url": "https://masoncapitalgroup.com",
      "image_url": "A real, publicly accessible image URL showing the lifestyle amenity, arts venue, dining district, or university named in this card. Use Wikimedia Commons or a known public CDN. Direct image link ending in .jpg, .jpeg, or .png. Must be VISUALLY DIFFERENT from other cards."
    }}
  ],
  "environmental": {{
    "flood_score": 2, "flood_label": "Minimal",
    "fire_score": 3, "fire_label": "Moderate",
    "heat_score": 4, "heat_label": "Moderate",
    "wind_score": 2, "wind_label": "Minor",
    "air_score": 2, "air_label": "Minor"
  }},
  "walk_score": {{"score": 15, "label": "Car-Dependent"}},
  "transit_score": {{"score": 0, "label": "Minimal Transit"}},
  "bike_score": {{"score": 30, "label": "Bikeable"}},
  {extra_fields}
  "county": "{data.get('county') or ''}",
  "subdivision": "{data.get('subdivision') or ''}",
  "apn": "{data.get('apn') or ''}",
  "school_district": "{data.get('school_district') or ''}"
}}

IMPORTANT:
- Use real data for {city}, {state}. Research actual schools, parks, trails, employers, and location facts.
- Location card content MUST be specific to this exact property and community — name actual trails, streets, employers, universities, developments, etc.
- image_url rules: Provide a REAL, working direct image URL for each card. Prefer Wikimedia Commons (https://upload.wikimedia.org/wikipedia/commons/...) — search your knowledge for the exact filename of a well-known photo of this specific place, landmark, or asset. Each card's image must be VISUALLY DISTINCT (recreation ≠ community ≠ investment ≠ lifestyle). If you are not confident a specific Wikimedia URL exists, use a known public tourism CDN or leave image_url as "" — do NOT guess or fabricate URLs.
- Tax history: estimate based on typical {city}, {state} rates.
- Comparables: realistic nearby properties with plausible data for this listing type.
- Brand voice: authoritative, investment-focused, direct. Not salesy.
- Return ONLY valid JSON."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    enhanced = _safe_json_parse(raw)

    # Merge enhanced data into original data (enhanced fields take priority for missing data)
    for key, val in enhanced.items():
        if key not in data or not data[key]:
            data[key] = val
        elif key in ("highlights", "description_paragraphs", "schools", "tax_history",
                     "comparable_homes", "property_history", "location_cards",
                     "loan_estimate", "environmental", "features_general",
                     "features_interior", "features_kitchen", "features_exterior",
                     "features_utilities", "features_location", "area_stats",
                     "nearby_parks", "walk_score", "transit_score", "bike_score"):
            # Always take AI-generated versions for these structured fields
            data[key] = val

    return data


# ─── Utility helpers ────────────────────────────────────────────────────────

def _extract_og_images(soup: BeautifulSoup) -> list:
    imgs = []
    for tag in soup.find_all("meta", property="og:image"):
        if tag.get("content"):
            imgs.append(tag["content"])
    return imgs


def _dig(obj, *keys):
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _to_int(val) -> int:
    try:
        return int(str(val).replace(",", "").replace("$", "").split(".")[0])
    except Exception:
        return 0


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "").replace("$", ""))
    except Exception:
        return 0.0


def _empty_listing() -> dict:
    return {
        "address_street": "",
        "address_city": "",
        "address_state": "",
        "address_zip": "",
        "price": 0,
        "status": "Active",
        "beds": 0,
        "baths": 0,
        "sqft": 0,
        "lot_sqft": 0,
        "year_built": None,
        "property_type": "Single Family",
        "description": "",
        "photos": [],
        "hoa_fee": 0,
        "mls_number": "",
        "days_on_market": 0,
        "tax_annual": 0,
        "virtual_tour_url": None,
        "source": "empty",
    }
