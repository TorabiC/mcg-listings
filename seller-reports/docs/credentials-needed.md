# Credentials Needed — checklist for Cameron

Each source in `config/sources.json` is `"enabled": false` until its
credential is in place. Nothing is called, nothing can crash — `collect.py`
just writes zeros with `data_quality: "missing"` for a disabled/uncredentialed
source. Flip `enabled` to `true` once you've dropped the credential in.

---

## 1. IDX Broker

**What we need:** an API access key ("accesskey").

**Where to find it:**
1. Log in to the IDX Broker account at https://middleware.idxbroker.com (or
   the standard idxbroker.com dashboard if that's the front door you use).
2. Go to **My Account → API Access** (Platinum-tier accounts only — confirm
   the MCG plan includes API access; if not, this needs an upgrade call
   with IDX Broker support first).
3. Generate/copy the **accesskey** shown there.

**Where it goes:** environment variable `IDXBROKER_ACCESSKEY`, or update
`config/sources.json → idx_broker.credential_env_or_path` if you'd rather
point at a file. Never commit the key value itself.

**Also confirm:** the "Listing Stats" add-on is active on the plan — that's
what exposes per-listing view counts via the API; the base plan may not
include it.

---

## 2. Constant Contact

**What we need:** an OAuth access token + refresh token pair (API v3 has no
static API key — it's OAuth2 only).

**Where to find it:**
1. Register an app at https://developer.constantcontact.com (My Applications
   → New Application) if one doesn't already exist for MCG.
2. Note the **Client ID** and **Client Secret** from the app page.
3. Run the OAuth authorization-code flow once (there are walkthroughs on the
   CC developer site) — this requires a one-time browser login as the MCG
   Constant Contact account owner and produces a **refresh token**.
4. `collect.py`'s adapter will exchange the refresh token for short-lived
   access tokens automatically from then on.

**Where it goes:** environment variable `CONSTANTCONTACT_OAUTH_TOKEN`
holding the JSON blob `{"access_token": "...", "refresh_token": "...",
"expires_at": "..."}`, or a file path in
`config/sources.json → constant_contact.credential_env_or_path`.

**Note:** this system only ever *reads* campaign stats. Nothing here sends
campaigns — CC sends stay a manual/draft action in Constant Contact itself.

---

## 3. Google Analytics 4 (GA4)

**What we need:** the GA4 **property ID** (numeric, e.g. `123456789` — not
the `G-XXXXXXX` measurement ID) and a **service account JSON key** with
Viewer access on that property.

**Where to find the property ID:**
1. In GA4, go to **Admin → Property Settings**.
2. Copy the **Property ID** field (numeric).

**Where to get the service account key:**
1. In Google Cloud Console, pick (or create) the project tied to the MCG
   Google account → **IAM & Admin → Service Accounts**.
2. Create a service account (or reuse one), then **Keys → Add Key → Create
   new key → JSON**. Download the file.
3. Back in GA4, go to **Admin → Property Access Management → Add users**,
   and add the service account's email address (looks like
   `name@project.iam.gserviceaccount.com`) as a **Viewer**.

**Where it goes:** save the JSON key file at the path in
`config/sources.json → ga4.credential_env_or_path` (default
`config/ga4-service-account.json` — do not commit this file), and set
`ga4.property_id` in the same config to the numeric property ID.

---

## 4. tawk.to

**What we need:** a REST API key for the relevant Property/Widget.

**Where to find it:**
1. Log in to the tawk.to dashboard.
2. Go to **Administration → Property Settings** (or the specific widget's
   settings) **→ REST API**.
3. Copy the API key shown there.

**Where it goes:** environment variable `TAWK_API_KEY`, or a file path in
`config/sources.json → tawk.credential_env_or_path`.

---

## 5. Portals (homes.com, Crexi, LoopNet)

No credentials needed — these have no usable public API for us. This stays
manual intake: drop numbers into `intake/<slug>/` per
`intake/README.md`. Nothing to obtain here.

---

## Quick status check

Run `python3 bin/collect.py --period weekly --slug all` (no `--sample`)
after adding any of the above — the `data_quality` block in each
`data/<slug>/<period_id>/metrics.json` will flip from `"missing"` to
`"live"` for whichever source now has working credentials.
