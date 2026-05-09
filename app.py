"""
MCG Marketing Dashboard — Flask Backend
Run: python app.py
"""

import os
import json
import logging
import hmac
import hashlib
import time
import threading
import uuid
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps

from scraper import scrape_listing
import listing_generator
from listing_generator import normalize, generate_html
from webflow_client import WebflowClient

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mcg-dashboard-dev-key")
CORS(app, origins=[
    "https://www.masoncapitalgroup.com",
    "https://masoncapitalgroup.com",
    "https://torabic.github.io",
])

GENERATED_DIR = Path(__file__).parent / "generated"
GENERATED_DIR.mkdir(exist_ok=True)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

SETTINGS_FILE = Path(__file__).parent / ".dashboard_settings.json"

# ── Background scrape jobs ────────────────────────────────────────────────────
# Keyed by job_id: {"status": "working"|"done"|"error", "listing": {...}, "error": "..."}
_scrape_jobs: dict = {}
# Keyed by job_id: unified generate jobs (scrape + generate + publish)
_gen_jobs: dict = {}
_jobs_lock = threading.Lock()

ADMIN_USER = os.getenv("ADMIN_USERNAME", "mcgadmin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "")
TOKEN_SECRET = os.getenv("FLASK_SECRET_KEY", "mcg-dashboard-dev-key")
TOKEN_TTL = 86400  # 24 hours


def _make_token(username: str) -> str:
    """Create a signed token: base64(username:ts):hmac"""
    ts = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_token(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        username, ts, sig = parts
        payload = f"{username}:{ts}"
        expected = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        if time.time() - int(ts) > TOKEN_TTL:
            return False
        return True
    except Exception:
        return False


@app.after_request
def set_security_headers(response):
    # Allow embedding only from masoncapitalgroup.com
    response.headers["X-Frame-Options"] = "ALLOW-FROM https://www.masoncapitalgroup.com"
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://www.masoncapitalgroup.com https://masoncapitalgroup.com"
    )
    return response


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Accept Bearer token for API calls from Webflow
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            if _verify_token(token):
                return f(*args, **kwargs)
            return jsonify({"error": "Invalid or expired token"}), 401
        # Fall back to session auth for browser
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ── Settings helpers ─────────────────────────────────────────────────────────

def load_settings() -> dict:
    defaults = {
        "wf_token": os.getenv("WEBFLOW_API_TOKEN", ""),
        "wf_site": os.getenv("WEBFLOW_SITE_ID", "699cb0b733f309dd4bda1b56"),
        "ai_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "agent_name": os.getenv("AGENT_NAME", "Cameron Torabi"),
        "agent_phone": os.getenv("AGENT_PHONE", "(858) 500-0222"),
        "agent_license": os.getenv("AGENT_LICENSE", "AR RE License #PB00056565"),
        "agent_email": os.getenv("AGENT_EMAIL", "info@masoncapitalgroup.com"),
    }
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            defaults.update({k: v for k, v in saved.items() if v})
        except Exception:
            pass
    return defaults


def save_settings(data: dict):
    existing = load_settings()
    existing.update(data)
    SETTINGS_FILE.write_text(json.dumps(existing, indent=2))


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == ADMIN_USER and password == ADMIN_PASS and ADMIN_PASS:
            session["logged_in"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/auth", methods=["POST"])
def api_auth():
    """Exchange username/password for a bearer token (used by Webflow Admin Hub)."""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if username == ADMIN_USER and password == ADMIN_PASS and ADMIN_PASS:
        return jsonify({"token": _make_token(username)})
    return jsonify({"error": "Invalid credentials"}), 401


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("dashboard.html")


@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    s = load_settings()
    # Never expose secrets to the client
    return jsonify({
        "wf_site": s.get("wf_site", ""),
        "agent_name": s.get("agent_name", ""),
        "agent_phone": s.get("agent_phone", ""),
        "agent_license": s.get("agent_license", ""),
        "agent_email": s.get("agent_email", ""),
        "has_wf_token": bool(s.get("wf_token")),
        "has_ai_key": bool(s.get("ai_key")),
    })


@app.route("/api/settings", methods=["POST"])
@login_required
def post_settings():
    data = request.get_json(force=True) or {}
    save_settings(data)
    # Update env vars for the current process
    if data.get("ai_key"):
        os.environ["ANTHROPIC_API_KEY"] = data["ai_key"]
    if data.get("wf_token"):
        os.environ["WEBFLOW_API_TOKEN"] = data["wf_token"]
    if data.get("wf_site"):
        os.environ["WEBFLOW_SITE_ID"] = data["wf_site"]
    return jsonify({"ok": True})


@app.route("/api/scrape", methods=["POST"])
@login_required
def api_scrape():
    """
    Start a background scrape job. Returns {job_id} immediately.
    Poll GET /api/scrape/<job_id> for status and results.
    """
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    settings = load_settings()
    api_key = settings.get("ai_key") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. Add it in Settings."}), 400

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _scrape_jobs[job_id] = {"status": "working"}

    def _run(job_id, url, api_key):
        try:
            logger.info(f"[job {job_id}] Scraping: {url}")
            raw = scrape_listing(url, api_key)
            listing = normalize(raw)
            logger.info(f"[job {job_id}] Done: {listing.get('address_full')} @ {listing.get('price_formatted')}")
            with _jobs_lock:
                _scrape_jobs[job_id] = {"status": "done", "listing": listing, "source": raw.get("source", "unknown")}
        except Exception as e:
            logger.error(f"[job {job_id}] Scrape error: {e}", exc_info=True)
            with _jobs_lock:
                _scrape_jobs[job_id] = {"status": "error", "error": str(e)}

    threading.Thread(target=_run, args=(job_id, url, api_key), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "working"})


@app.route("/api/scrape/<job_id>", methods=["GET"])
@login_required
def api_scrape_poll(job_id):
    """Poll for scrape job status."""
    with _jobs_lock:
        job = _scrape_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    """
    Accept a normalized listing dict, render the full listing page HTML,
    publish it to Webflow as a static page (preserving the approved design),
    and store a CMS draft item so EXPERIENCE images are editable in the Editor.
    """
    body = request.get_json(force=True) or {}
    listing = body.get("listing")
    if not listing:
        return jsonify({"error": "listing data is required"}), 400

    try:
        # Always inject current AGENT_DEFAULTS so agent photo/info stays fresh
        listing["agent"] = listing_generator.AGENT_DEFAULTS

        html = generate_html(listing)

        # Save to disk (HTML for serving, JSON for Refresh endpoint)
        slug = listing.get("slug", "listing")
        out_path = GENERATED_DIR / f"{slug}.html"
        out_path.write_text(html, encoding="utf-8")
        (GENERATED_DIR / f"{slug}.json").write_text(
            json.dumps(listing, default=str, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(f"Generated: {out_path}")

        settings = load_settings()
        wf_token = settings.get("wf_token") or os.getenv("WEBFLOW_API_TOKEN", "")
        wf_site = settings.get("wf_site") or os.getenv("WEBFLOW_SITE_ID", "699cb0b733f309dd4bda1b56")

        webflow_url = None
        if wf_token:
            client = WebflowClient(wf_token, wf_site)

            # Publish listing as a live CMS page on masoncapitalgroup.com/listings/{slug}.
            # is_draft=False: publishes the item so it goes live immediately.
            # All fields (including EXPERIENCE images) remain editable in the Webflow Editor.
            try:
                cms_result = client.push_listing_to_cms(listing, is_draft=False)
                webflow_url = cms_result.get("url")
                logger.info(f"CMS page published: {webflow_url}")
            except Exception as cms_err:
                logger.warning(f"CMS publish failed (non-fatal): {cms_err}")

        return jsonify({
            "html": html,
            "slug": slug,
            "file": str(out_path),
            "webflow_url": webflow_url,
        })

    except Exception as e:
        logger.error(f"Generate error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-job", methods=["POST"])
@login_required
def api_generate_job():
    """
    Unified generate endpoint: accepts {url} or {listing}, runs scrape→generate→Webflow publish
    as a background job. Returns {jobId} immediately.
    The dashboard polls GET /api/status/<jobId> for progress and results.
    """
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    listing_input = body.get("listing")

    if not url and not listing_input:
        return jsonify({"error": "url or listing required"}), 400

    job_id = str(int(time.time() * 1000))
    with _jobs_lock:
        _gen_jobs[job_id] = {
            "status": "running",
            "step": "scrape" if url else "generate",
            "log": "",
            "slug": None,
            "files": [],
            "error": None,
            "webflow_url": None,
        }

    def _update(step=None, log_append=None, **kw):
        with _jobs_lock:
            j = _gen_jobs[job_id]
            if step:
                j["step"] = step
            if log_append:
                j["log"] += log_append + "\n"
            j.update(kw)

    def _run():
        try:
            settings = load_settings()
            api_key    = settings.get("ai_key")    or os.getenv("ANTHROPIC_API_KEY", "")
            wf_token   = settings.get("wf_token")  or os.getenv("WEBFLOW_API_TOKEN", "")
            wf_site    = settings.get("wf_site")   or os.getenv("WEBFLOW_SITE_ID", "699cb0b733f309dd4bda1b56")
            ixact_key  = settings.get("ixactKey")  or os.getenv("IXACT_API_KEY", "")
            gen_server = os.getenv("GEN_SERVER_URL", "https://mcg-marketing-hub-production.up.railway.app")

            listing = listing_input

            # ── Step 1: Scrape ────────────────────────────────────────────────
            if url:
                if not api_key:
                    raise ValueError("Anthropic API key not configured. Add it in Settings.")
                _update(step="scrape", log_append="Scraping listing...")
                raw = scrape_listing(url, api_key)
                listing = normalize(raw)
                _update(log_append=f"Scraped: {listing.get('address_full')}")

            listing["agent"] = listing_generator.AGENT_DEFAULTS
            slug = listing.get("slug", "listing")

            # ── Step 2: Full generation via Node.js server ───────────────────
            # Produces: flipbook, listing page, flyer HTML, flyer PDF, OM PDF, email campaign
            _update(step="generate", log_append="Generating OM, listing page, flyer + PDFs…")
            gen_payload = {
                "address":       listing.get("address_full", ""),
                "streetAddress": listing.get("address_street", ""),
                "city":          listing.get("address_city", ""),
                "state":         listing.get("address_state", "AR"),
                "zip":           listing.get("address_zip", ""),
                "price":         listing.get("price_formatted", ""),
                "beds":          listing.get("beds"),
                "baths":         listing.get("baths"),
                "sqft":          listing.get("sqft_formatted", ""),
                "acres":         listing.get("lot_acres_display", ""),
                "yearBuilt":     listing.get("year_built"),
                "description":   " ".join(listing.get("description_paragraphs") or []),
                "photos":        listing.get("photos") or [],
                "mls":           listing.get("mls_number", ""),
                "status":        listing.get("status", "Active"),
                "county":        listing.get("county", ""),
                "subdivision":   listing.get("subdivision", ""),
                "zoning":        listing.get("zoning", ""),
                "type":          listing.get("property_type", "Residential"),
                "lat":           listing.get("lat"),
                "lng":           listing.get("lng"),
                "listingUrl":    url or "",
            }

            gen_resp = requests.post(
                f"{gen_server}/api/generate-from-data",
                json=gen_payload,
                timeout=240,  # PDFs can take up to 3 min
            )
            gen_data = gen_resp.json() if gen_resp.ok else {}
            if not gen_resp.ok:
                _update(log_append=f"Generation server warning ({gen_resp.status_code}): {gen_resp.text[:200]}")

            urls = gen_data.get("urls", {})
            node_slug = gen_data.get("slug") or slug

            # Absolute URLs hosted on the Node.js server
            def abs_url(rel):
                if not rel:
                    return None
                return f"{gen_server}{rel}" if rel.startswith("/") else rel

            om_url       = abs_url(urls.get("flipbook"))
            listing_url  = abs_url(urls.get("listingPage"))
            flyer_url    = abs_url(urls.get("flyer"))
            flyer_pdf    = abs_url(urls.get("flyerPdf"))
            om_pdf       = abs_url(urls.get("omPdf"))
            email_url    = abs_url(urls.get("emailCampaign"))

            _update(log_append="Content generation complete.")

            # ── Step 3: Generate listing page HTML (Python) for Webflow ──────
            # We use our own HTML generator for the Webflow page so it matches
            # the approved design and embeds the flipbook via iframe.
            _update(step="listing", log_append="Building listing page for Webflow…")
            html = generate_html(listing)
            if om_url:
                # Inject OM flipbook iframe just before </body>
                embed = (
                    f'\n<section style="padding:40px 0;background:#f9f6f2">'
                    f'<div style="max-width:1200px;margin:0 auto;padding:0 20px">'
                    f'<h2 style="text-align:center;margin-bottom:20px">Offering Memorandum</h2>'
                    f'<iframe src="{om_url}" style="width:100%;height:750px;border:none;border-radius:8px" '
                    f'loading="lazy" title="Offering Memorandum"></iframe></div></section>'
                )
                html = html.replace("</body>", embed + "</body>")

            out_dir = OUTPUT_DIR / node_slug
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "listing-page.html").write_text(html, encoding="utf-8")
            (GENERATED_DIR / f"{node_slug}.html").write_text(html, encoding="utf-8")
            (GENERATED_DIR / f"{node_slug}.json").write_text(
                json.dumps(listing, default=str, ensure_ascii=False), encoding="utf-8"
            )

            # ── Step 4: Publish to Webflow ────────────────────────────────────
            webflow_url = None
            if wf_token:
                _update(step="publish", log_append="Publishing to Webflow (Featured Listings)…")
                client = WebflowClient(wf_token, wf_site)

                # Live CMS item
                try:
                    cms_result = client.push_listing_to_cms(listing, is_draft=False)
                    webflow_url = cms_result.get("url")
                    _update(log_append=f"CMS live: {webflow_url}")
                except Exception as e:
                    _update(log_append=f"CMS warning: {e}")

                # Static page under Featured Listings folder
                try:
                    page_result = client.create_listing_page(listing, html)
                    webflow_url = page_result.get("url") or webflow_url
                    _update(log_append=f"Featured Listings page: {page_result.get('url')}")
                except Exception as e:
                    _update(log_append=f"Static page warning: {e}")
            else:
                _update(log_append="Webflow token not set — skipping publish.")

            # ── Step 5: Push email campaign to IXACT ─────────────────────────
            ixact_result = None
            if ixact_key and email_url:
                _update(step="ixact", log_append="Pushing email campaign to IXACT…")
                try:
                    # Fetch the generated email HTML from Node.js server
                    email_html_resp = requests.get(email_url, timeout=30)
                    email_html = email_html_resp.text if email_html_resp.ok else ""

                    if not email_html and node_slug:
                        # Fallback: fetch via slug API
                        r2 = requests.get(f"{gen_server}/api/email-html/{node_slug}", timeout=15)
                        if r2.ok:
                            email_html = r2.json().get("html", "")

                    if email_html:
                        address = listing.get("address_full", "New Listing")
                        subject = f"New MCG Listing: {address}"
                        ixact_resp = requests.post(
                            "https://api.ixactcontact.com/v1/MassEmail",
                            headers={
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                                "IXACT-API-Key": ixact_key,
                            },
                            json={
                                "Subject": subject,
                                "From": "info@masoncapitalgroup.com",
                                "FromName": "Cameron Torabi, Mason Capital Group",
                                "ReplyTo": "info@masoncapitalgroup.com",
                                "Body": email_html,
                                "IsDraft": True,
                            },
                            timeout=30,
                        )
                        ixact_result = {"success": ixact_resp.ok, "status": ixact_resp.status_code}
                        _update(log_append=f"IXACT email campaign {'saved' if ixact_resp.ok else 'failed'} (HTTP {ixact_resp.status_code})")
                    else:
                        _update(log_append="IXACT: no email HTML found — skipping.")
                except Exception as e:
                    _update(log_append=f"IXACT warning: {e}")
                    ixact_result = {"success": False, "error": str(e)}

            # ── Finalize ──────────────────────────────────────────────────────
            with _jobs_lock:
                _gen_jobs[job_id].update({
                    "status":      "done",
                    "step":        "done",
                    "slug":        node_slug,
                    "files":       gen_data.get("files", []),
                    "webflow_url": webflow_url,
                    "om_url":      om_url,
                    "listing_url": listing_url or webflow_url,
                    "flyer_url":   flyer_url,
                    "flyer_pdf":   flyer_pdf,
                    "om_pdf":      om_pdf,
                    "email_url":   email_url,
                    "ixact":       ixact_result,
                    "log":         _gen_jobs[job_id]["log"] + "✓ All done.",
                })

        except Exception as e:
            logger.error(f"Generate job {job_id} error: {e}", exc_info=True)
            with _jobs_lock:
                _gen_jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"jobId": job_id, "status": "started"})


@app.route("/api/status/<job_id>", methods=["GET"])
@login_required
def api_status(job_id):
    """Poll status of a unified generate job."""
    with _jobs_lock:
        job = _gen_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/output/<slug>/<filename>")
@login_required
def serve_output(slug, filename):
    """Serve generated output files (listing-page.html, flipbook.html, etc.)."""
    file_path = OUTPUT_DIR / slug / filename
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(file_path))


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    """
    Re-generate and re-publish a listing page using any image overrides
    stored in the CMS draft item (edited via Webflow Editor).
    """
    body = request.get_json(force=True) or {}
    slug = (body.get("slug") or "").strip()
    if not slug:
        return jsonify({"error": "slug is required"}), 400

    # Load the saved listing from disk
    out_path = GENERATED_DIR / f"{slug}.html"
    listing_path = GENERATED_DIR / f"{slug}.json"
    if not listing_path.exists():
        return jsonify({"error": "Listing data not found. Re-run Generate first."}), 404

    settings = load_settings()
    wf_token = settings.get("wf_token") or os.getenv("WEBFLOW_API_TOKEN", "")
    wf_site = settings.get("wf_site") or os.getenv("WEBFLOW_SITE_ID", "699cb0b733f309dd4bda1b56")

    if not wf_token:
        return jsonify({"error": "Webflow API token not configured"}), 400

    try:
        listing = json.loads(listing_path.read_text(encoding="utf-8"))
        listing["agent"] = listing_generator.AGENT_DEFAULTS

        client = WebflowClient(wf_token, wf_site)

        # Pull any image overrides from the CMS draft item
        overrides = client.get_cms_image_overrides(slug)
        if overrides:
            cards = listing.get("location_cards") or []
            for i, card in enumerate(cards):
                key = f"experience-{i+1}-image"
                if key in overrides and overrides[key]:
                    card["image_url"] = overrides[key]
            listing["location_cards"] = cards
            logger.info(f"Applied {len(overrides)} CMS image overrides for {slug}")

        html = generate_html(listing)
        out_path.write_text(html, encoding="utf-8")

        page_result = client.create_listing_page(listing, html)
        webflow_url = page_result.get("url")
        logger.info(f"Refreshed static page: {webflow_url}")

        return jsonify({"ok": True, "slug": slug, "webflow_url": webflow_url})

    except Exception as e:
        logger.error(f"Refresh error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/publish", methods=["POST"])
@login_required
def api_publish():
    """
    Publish the generated listing page to Webflow.
    Creates a static page at /properties/<slug> on masoncapitalgroup.com.
    """
    body = request.get_json(force=True) or {}
    listing = body.get("listing")
    html = body.get("html") or ""

    if not listing:
        return jsonify({"error": "listing data is required"}), 400

    settings = load_settings()
    wf_token = settings.get("wf_token") or os.getenv("WEBFLOW_API_TOKEN", "")
    wf_site = settings.get("wf_site") or os.getenv("WEBFLOW_SITE_ID", "699cb0b733f309dd4bda1b56")

    if not wf_token:
        return jsonify({"error": "Webflow API token not configured. Add it in Settings."}), 400

    # If HTML not passed in, try to load from disk
    if not html:
        slug = listing.get("slug", "listing")
        path = GENERATED_DIR / f"{slug}.html"
        if path.exists():
            html = path.read_text(encoding="utf-8")
        else:
            return jsonify({"error": "No generated HTML found. Run /api/generate first."}), 400

    try:
        client = WebflowClient(wf_token, wf_site)
        result = client.create_listing_page(listing, html)
        logger.info(f"Published: {result['url']}")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Publish error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _push_to_ixact(data: dict):
    """Send lead to IXACT Contact via Zapier webhook."""
    webhook = os.getenv("IXACT_ZAPIER_WEBHOOK", "")
    if not webhook:
        return
    try:
        import requests as req
        payload = {
            "first_name": data.get("first_name", ""),
            "last_name": data.get("last_name", ""),
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "interest": data.get("interest", ""),
            "property": data.get("property", ""),
            "price": data.get("price", ""),
            "mls": data.get("mls", ""),
            "source": "MCG Listing Page",
        }
        req.post(webhook, json=payload, timeout=8)
        logger.info("Lead pushed to IXACT via Zapier")
    except Exception as e:
        logger.warning(f"IXACT webhook failed: {e}")


def _send_lead_email(data: dict):
    """Send lead notification email to MCG."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_addr = "info@masoncapitalgroup.com"

    if not smtp_user or not smtp_pass:
        logger.warning("SMTP not configured — skipping lead email notification")
        return

    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("name", "—")
    subject = f"New Lead: {data.get('property', 'MCG Listing')} — {name}"
    body = f"""\
New lead submitted via MCG listing page.

Property: {data.get('property', '—')}
Price:     {data.get('price', '—')}
MLS #:     {data.get('mls', '—')}

Name:      {name}
Email:     {data.get('email', '—')}
Phone:     {data.get('phone', '—')}
Interest:  {data.get('interest', '—')}
Type:      {data.get('type', '—')}

Message:
{data.get('message', '(none)')}
"""
    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_addr, msg.as_string())
        logger.info(f"Lead notification sent to {to_addr}")
    except Exception as e:
        logger.warning(f"Lead email failed: {e}")


@app.route("/api/lead", methods=["POST"])
@login_required
def api_lead():
    """
    Capture a lead submission from the listing page contact forms.
    Pushes to IXACT Contact via Zapier and sends email notification.
    """
    data = request.get_json(force=True) or {}
    logger.info(f"Lead: {json.dumps(data)}")

    _push_to_ixact(data)
    _send_lead_email(data)

    # Legacy MCG email endpoint fallback
    mcg_email_endpoint = os.getenv("MCG_EMAIL_ENDPOINT", "")
    if mcg_email_endpoint:
        try:
            import requests as req
            req.post(mcg_email_endpoint, json=data, timeout=5)
        except Exception as e:
            logger.warning(f"MCG email endpoint failed: {e}")

    return jsonify({"ok": True})


@app.route("/preview/<slug>")
@login_required
def preview(slug):
    """Serve a generated listing page for preview."""
    path = GENERATED_DIR / f"{slug}.html"
    if not path.exists():
        return "Page not found", 404
    return send_file(str(path))


@app.route("/api/listings", methods=["GET"])
@login_required
def api_listings():
    """List all generated listing pages."""
    files = sorted(GENERATED_DIR.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
    listings = []
    for f in files:
        listings.append({
            "slug": f.stem,
            "file": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "modified": f.stat().st_mtime,
        })
    return jsonify({"listings": listings})


@app.route("/api/webflow/test", methods=["GET"])
@login_required
def test_webflow():
    """Quick connectivity check for Webflow API."""
    settings = load_settings()
    wf_token = settings.get("wf_token") or os.getenv("WEBFLOW_API_TOKEN", "")
    wf_site = settings.get("wf_site") or os.getenv("WEBFLOW_SITE_ID", "")
    if not wf_token:
        return jsonify({"ok": False, "error": "No Webflow token configured"})
    try:
        client = WebflowClient(wf_token, wf_site)
        site = client.get_site()
        return jsonify({"ok": True, "site": site.get("displayName", ""), "id": wf_site})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Dev server ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"\n  MCG Marketing Dashboard")
    print(f"  ─────────────────────────────────────")
    print(f"  http://localhost:{port}")
    print(f"  Copy .env.example → .env and add your API keys\n")
    app.run(host="0.0.0.0", port=port, debug=True)
