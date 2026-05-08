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
])

GENERATED_DIR = Path(__file__).parent / "generated"
GENERATED_DIR.mkdir(exist_ok=True)

SETTINGS_FILE = Path(__file__).parent / ".dashboard_settings.json"

# ── Background scrape jobs ────────────────────────────────────────────────────
# Keyed by job_id: {"status": "working"|"done"|"error", "listing": {...}, "error": "..."}
_scrape_jobs: dict = {}
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
    Accept a normalized listing dict, render the full listing page HTML.
    Saves the HTML to the generated/ folder and returns it.
    """
    body = request.get_json(force=True) or {}
    listing = body.get("listing")
    if not listing:
        return jsonify({"error": "listing data is required"}), 400

    try:
        # Always inject current AGENT_DEFAULTS so agent photo/info stays fresh
        # regardless of what was stored in the scraped listing dict
        listing["agent"] = listing_generator.AGENT_DEFAULTS

        html = generate_html(listing)

        # Save to disk for serving previews and downloads
        slug = listing.get("slug", "listing")
        out_path = GENERATED_DIR / f"{slug}.html"
        out_path.write_text(html, encoding="utf-8")

        logger.info(f"Generated: {out_path}")
        return jsonify({
            "html": html,
            "slug": slug,
            "file": str(out_path),
        })

    except Exception as e:
        logger.error(f"Generate error: {e}", exc_info=True)
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
