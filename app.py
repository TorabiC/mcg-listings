"""
MCG Marketing Dashboard — Flask Backend
Run: python app.py
"""

import os
import json
import logging
import hmac
import hashlib
import re
import time
import threading
import uuid
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, Response
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps

from scraper import scrape_listing
import listing_generator
from listing_generator import normalize, generate_html
from webflow_client import WebflowClient
import reports_hub

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
REPORTS_STATE_FILE = Path(__file__).parent / "reports_state.json"

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


# ── Listing Reports state helpers ───────────────────────────────────────────
# Schema: {"<slug>": {
#   "seller": {"name", "email", "updated_at"},   # entered via the dashboard --
#                                                 # never written to the public
#                                                 # registry (privacy fix)
#   "<period_id>": {"status": "ready"|"approved"|"sent",
#     "approved_at", "sent_at", "note", "cc_campaign_id", "cc_activity_id",
#     "email_stats": {...}, "page_views": 0, "sent_html_sha256"}}}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_reports_state() -> dict:
    if REPORTS_STATE_FILE.exists():
        try:
            return json.loads(REPORTS_STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_reports_state(state: dict):
    REPORTS_STATE_FILE.write_text(json.dumps(state, indent=2))


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

        # Auto-provision this listing for the Listing Reports hub (never
        # blocks/fails the response -- see reports_hub.register_listing_for_reports).
        reports_registered = False
        try:
            reports_registered = reports_hub.register_listing_for_reports(listing, webflow_url)
        except Exception as reg_err:
            logger.warning(f"Reports auto-registration failed (non-fatal): {reg_err}")

        return jsonify({
            "html": html,
            "slug": slug,
            "file": str(out_path),
            "webflow_url": webflow_url,
            "reports_registered": reports_registered,
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
                # MLS Matrix shared URLs use a dedicated Node.js HTML parser that
                # works reliably with simple HTTP GET. Playwright+Claude (the Python
                # path) fails on these URLs due to bot-detection on the Railway host.
                # Delegate the entire scrape+generate pipeline to the Node server and
                # convert the result back into the Flask job format.
                from urllib.parse import urlparse as _up
                _domain = _up(url).netloc.lower()
                if "mlsmatrix" in _domain or "matrix" in _domain:
                    _update(step="scrape", log_append="MLS Matrix URL — delegating to Node.js generator…")
                    node_job_resp = requests.post(
                        f"{gen_server}/api/generate",
                        json={"url": url},
                        timeout=30,
                    )
                    if not node_job_resp.ok:
                        raise ValueError(f"Node generator error: {node_job_resp.text[:200]}")
                    node_job_id = node_job_resp.json().get("jobId")
                    if not node_job_id:
                        raise ValueError("Node generator did not return a jobId")
                    # Poll Node's /api/status until done
                    import time as _time
                    for _ in range(60):
                        _time.sleep(5)
                        st_resp = requests.get(f"{gen_server}/api/status/{node_job_id}", timeout=15)
                        st = st_resp.json() if st_resp.ok else {}
                        _update(log_append=st.get("log", "")[-200:] if st.get("log") else None)
                        if st.get("status") == "error":
                            raise ValueError(f"Node generation failed: {st.get('error','unknown')}")
                        if st.get("status") == "done":
                            node_slug = st.get("slug") or ""
                            node_files = st.get("files") or []
                            def _node_url(path):
                                return f"{gen_server}/output/{node_slug}/{path}" if node_slug else None
                            with _jobs_lock:
                                _gen_jobs[job_id].update({
                                    "status":      "done",
                                    "step":        "done",
                                    "slug":        node_slug,
                                    "files":       node_files,
                                    "webflow_url": None,
                                    "om_url":      _node_url("flipbook.html"),
                                    "listing_url": _node_url("listing-page.html"),
                                    "flyer_url":   _node_url("flyer.html"),
                                    "flyer_pdf":   _node_url("flyer.pdf"),
                                    "om_pdf":      _node_url("om.pdf"),
                                    "email_url":   _node_url("email-campaign.html"),
                                    "ixact":       None,
                                    "log":         _gen_jobs[job_id]["log"] + "✓ All done.",
                                })
                            return  # exit _run — Node handled everything
                    raise ValueError("Node generation timed out after 5 minutes")

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

            # Auto-provision this listing for the Listing Reports hub (never
            # blocks/fails the job -- see reports_hub.register_listing_for_reports).
            # NOTE: only covers this (non-MLS-Matrix) publish path -- the MLS
            # Matrix delegation branch above returns early before webflow_url
            # is ever set and is not yet hooked.
            reports_registered = False
            try:
                reports_registered = reports_hub.register_listing_for_reports(listing, webflow_url)
            except Exception as reg_err:
                _update(log_append=f"Reports auto-registration warning: {reg_err}")

            # ── Step 5: Confirm email campaign is ready ───────────────────────
            # IXACT Contact does not expose a REST API for mass email creation.
            # The generated email HTML is served from the Node.js server and
            # surfaced in the dashboard for copy-paste into IXACT.
            ixact_result = None
            _update(step="ixact", log_append="Email campaign ready — copy HTML from dashboard into IXACT.")
            if email_url:
                ixact_result = {"success": True, "ready": True, "email_url": email_url}
                logger.info(f"Email campaign HTML ready at: {email_url}")

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
                    "reports_registered": reports_registered,
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

        # Auto-provision this listing for the Listing Reports hub (never
        # blocks/fails the response -- see reports_hub.register_listing_for_reports).
        try:
            result["reports_registered"] = reports_hub.register_listing_for_reports(listing, result.get("url"))
        except Exception as reg_err:
            logger.warning(f"Reports auto-registration failed (non-fatal): {reg_err}")
            result["reports_registered"] = False

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


# ── Listing Reports (seller reports hub) ────────────────────────────────────

def _reports_payload() -> dict:
    """Registry + state + computed period/report URLs, for GET
    /api/reports/listings. Always computes BOTH weekly and monthly current
    periods so the dashboard can switch sub-tabs without a second round trip."""
    registry = reports_hub.get_registry()
    state = load_reports_state()

    period_defs = {
        ptype: {"period_type": ptype, "current_period_id": reports_hub.current_period_id(ptype)}
        for ptype in ("weekly", "monthly")
    }

    listings_out = []
    ready_count = 0
    for listing in registry:
        if listing.get("status") != "active":
            continue
        slug = listing["slug"]
        token = listing.get("report_token", "")
        listing_state = state.get(slug, {})

        # Seller contact resolves from hub state FIRST (entered via the
        # dashboard, never written to the public registry) -- the registry's
        # own seller.name/email (usually null) is only a fallback for
        # listings that predate this feature.
        hub_seller = listing_state.get("seller") or {}
        registry_seller = listing.get("seller") or {}
        seller_name = hub_seller.get("name") or registry_seller.get("name")
        seller_email = hub_seller.get("email") or registry_seller.get("email")

        reports_by_type = {}
        for ptype, pdef in period_defs.items():
            cur_id = pdef["current_period_id"]
            cur_entry = listing_state.get(cur_id, {})
            cur_status = cur_entry.get("status", "ready")
            if cur_status == "ready":
                ready_count += 1

            archive = []
            for pid in reports_hub.prior_period_ids(ptype, cur_id, n=8):
                pid_entry = listing_state.get(pid)
                archive.append({
                    "period_id": pid,
                    "url": reports_hub.report_url(slug, token, pid),
                    "sent": bool(pid_entry and pid_entry.get("status") == "sent"),
                    "sent_at": (pid_entry or {}).get("sent_at"),
                })

            reports_by_type[ptype] = {
                "current_period_id": cur_id,
                "report_url": reports_hub.report_url(slug, token, cur_id),
                "flyer_url": reports_hub.flyer_url(slug, token, cur_id),
                "status": cur_status,
                "approved_at": cur_entry.get("approved_at"),
                "sent_at": cur_entry.get("sent_at"),
                "note": cur_entry.get("note", ""),
                "email_stats": cur_entry.get("email_stats"),
                "page_views": cur_entry.get("page_views", 0),
                "cc_campaign_id": cur_entry.get("cc_campaign_id"),
                "cc_activity_id": cur_entry.get("cc_activity_id"),
                "archive": archive,
            }

        listings_out.append({
            "slug": slug,
            "address": listing.get("address", ""),
            "type": listing.get("type", ""),
            "report_token": token,
            # "email" is the full address (for pre-filling the edit field --
            # this is an authenticated admin API); "email_masked" is what the
            # collapsed list view should render (see reports_hub.mask_email).
            "seller": {
                "name": seller_name,
                "email": seller_email,
                "email_masked": reports_hub.mask_email(seller_email) if seller_email else None,
            },
            "reports": reports_by_type,
        })

    return {"listings": listings_out, "periods": period_defs, "ready_count": ready_count}


@app.route("/api/reports/listings", methods=["GET"])
@login_required
def api_reports_listings():
    try:
        return jsonify(_reports_payload())
    except Exception as e:
        logger.error(f"Reports listings error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/approve", methods=["POST"])
@login_required
def api_reports_approve():
    body = request.get_json(force=True) or {}
    slug = (body.get("slug") or "").strip()
    period_id = (body.get("period_id") or "").strip()
    note = body.get("note") or ""
    if not slug or not period_id:
        return jsonify({"error": "slug and period_id are required"}), 400

    state = load_reports_state()
    entry = state.setdefault(slug, {}).setdefault(period_id, {})
    entry["status"] = "approved"
    entry["approved_at"] = datetime.now(timezone.utc).isoformat()
    entry["note"] = note
    save_reports_state(state)
    return jsonify({"ok": True, "status": entry["status"], "approved_at": entry["approved_at"]})


@app.route("/api/reports/seller", methods=["POST"])
@login_required
def api_reports_seller():
    """Save a seller's name/e-mail against the hub's OWN state file --
    deliberately never written to the public seller-reports registry
    (privacy fix: seller PII must not live in the public GitHub repo)."""
    body = request.get_json(force=True) or {}
    slug = (body.get("slug") or "").strip()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    if not slug:
        return jsonify({"error": "slug is required"}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "That doesn't look like a valid e-mail address."}), 400

    state = load_reports_state()
    seller_entry = state.setdefault(slug, {}).setdefault("seller", {})
    seller_entry["name"] = name
    seller_entry["email"] = email
    seller_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_reports_state(state)
    return jsonify({
        "ok": True,
        "seller": {
            "name": name,
            "email": email,
            "email_masked": reports_hub.mask_email(email) if email else None,
        },
    })


@app.route("/api/reports/send", methods=["POST"])
@login_required
def api_reports_send():
    """The only path anywhere in this codebase that triggers a real send --
    requires @login_required (Cameron's click) and a prior Approve."""
    body = request.get_json(force=True) or {}
    slug = (body.get("slug") or "").strip()
    period_id = (body.get("period_id") or "").strip()
    if not slug or not period_id:
        return jsonify({"error": "slug and period_id are required"}), 400

    listing = reports_hub.get_listing(slug)
    if not listing:
        return jsonify({"error": f"listing '{slug}' not found in registry"}), 404

    state = load_reports_state()
    entry = state.setdefault(slug, {}).setdefault(period_id, {})
    if entry.get("status") != "approved":
        return jsonify({"error": "Report must be approved before it can be sent."}), 400

    # Seller resolves from hub state FIRST, registry only as a fallback --
    # see api_reports_seller / _reports_payload.
    hub_seller = state.get(slug, {}).get("seller") or {}
    registry_seller = listing.get("seller") or {}
    seller_name = hub_seller.get("name") or registry_seller.get("name")
    seller_email = hub_seller.get("email") or registry_seller.get("email")
    if not seller_email:
        return jsonify({"error": "No seller e-mail on file for this listing."}), 400
    listing = {**listing, "seller": {"name": seller_name, "email": seller_email}}

    try:
        result = reports_hub.send_report(listing, period_id, entry.get("note"))
    except reports_hub.CCError as e:
        logger.warning(f"Reports send failed for {slug}/{period_id}: {e}")
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        logger.error(f"Reports send error for {slug}/{period_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    entry["status"] = "sent"
    entry["sent_at"] = result["sent_at"]
    entry["cc_campaign_id"] = result["cc_campaign_id"]
    entry["cc_activity_id"] = result["cc_activity_id"]
    entry["sent_html_sha256"] = result["sent_html_sha256"]
    save_reports_state(state)
    return jsonify({"ok": True, "status": "sent", **result})


@app.route("/api/reports/stats/<slug>/<period_id>", methods=["GET"])
@login_required
def api_reports_stats(slug, period_id):
    state = load_reports_state()
    entry = state.get(slug, {}).get(period_id)
    if not entry or not entry.get("cc_activity_id"):
        return jsonify({"error": "No sent campaign for this period yet."}), 404
    try:
        stats = reports_hub.get_stats(entry["cc_activity_id"])
    except reports_hub.CCError as e:
        # A CC hiccup should never break the panel -- fall back to last-known stats.
        logger.warning(f"Reports stats error for {slug}/{period_id}: {e}")
        return jsonify({"ok": False, "error": str(e), "email_stats": entry.get("email_stats")})
    entry["email_stats"] = stats
    save_reports_state(state)
    return jsonify({"ok": True, "email_stats": stats, "page_views": entry.get("page_views", 0)})


@app.route("/api/reports/insights/<slug>/<period_id>", methods=["GET"])
@login_required
def api_reports_insights(slug, period_id):
    """Private talking points + pricing flag for Cameron's 'For your call'
    card. Fetches insights.json (published by bin/generate.py alongside the
    seller-facing report/flyer) -- never surfaced to the seller."""
    listing = reports_hub.get_listing(slug)
    if not listing:
        return jsonify({"available": False})
    insights = reports_hub.fetch_insights(slug, listing.get("report_token", ""), period_id)
    if not insights:
        return jsonify({"available": False})
    return jsonify({"available": True, **insights})


_BEACON_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04"
    b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
    b"\x01\x00;"
)


@app.route("/api/reports/beacon/<slug>/<token_gif>", methods=["GET"])
def api_reports_beacon(slug, token_gif):
    """1x1 tracking pixel embedded in the seller-facing report page.
    Deliberately NOT behind @login_required -- sellers view report.html
    unauthenticated. Validated against the listing's own report_token from
    the public registry instead. Never raises: any failure (bad token,
    unknown slug, state-file hiccup) still returns the gif."""
    try:
        token = token_gif[:-4] if token_gif.endswith(".gif") else token_gif
        listing = reports_hub.get_listing(slug)
        if listing and listing.get("report_token") == token:
            period_id = reports_hub.current_period_id("weekly")
            state = load_reports_state()
            entry = state.setdefault(slug, {}).setdefault(period_id, {})
            entry["page_views"] = int(entry.get("page_views", 0)) + 1
            save_reports_state(state)
    except Exception as e:
        logger.warning(f"Beacon error (non-fatal) for {slug}: {e}")

    resp = Response(_BEACON_GIF, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Dev server ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"\n  MCG Marketing Dashboard")
    print(f"  ─────────────────────────────────────")
    print(f"  http://localhost:{port}")
    print(f"  Copy .env.example → .env and add your API keys\n")
    app.run(host="0.0.0.0", port=port, debug=True)
