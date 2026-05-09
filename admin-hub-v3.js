/**
 * MCG Admin Hub v3 — Railway-backed script
 * Hosted at: torabic/mcg-listings (GitHub Pages) as admin-hub-v2.js
 * Loaded by the existing mcgAdminHubLoader Webflow registered script.
 *
 * After Railway deploy: update window.MCG_API or the fallback URL below.
 */

(function () {
  // ── Config ──────────────────────────────────────────────────────────────────
  const API_URL = 'https://mcg-dashboard-production.up.railway.app';
  const TOKEN_KEY = 'mcg_admin_token';
  const TOKEN_TS_KEY = 'mcg_admin_token_ts';
  const TOKEN_TTL = 23 * 60 * 60 * 1000; // 23 h in ms (server token is 24 h)

  // ── Token helpers ────────────────────────────────────────────────────────────
  function getToken() {
    const token = localStorage.getItem(TOKEN_KEY);
    const ts = parseInt(localStorage.getItem(TOKEN_TS_KEY) || '0', 10);
    if (token && Date.now() - ts < TOKEN_TTL) return token;
    return null;
  }

  function storeToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(TOKEN_TS_KEY, Date.now().toString());
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_TS_KEY);
  }

  // ── Inject login overlay if not present in the Webflow page HTML ─────────────
  if (!document.getElementById('mcg-login')) {
    const overlay = document.createElement('div');
    overlay.id = 'mcg-login';
    overlay.style.cssText = [
      'position:fixed','inset:0','z-index:999999',
      'background:#16162a','display:flex','align-items:center',
      'justify-content:center','font-family:Lato,sans-serif'
    ].join(';');
    overlay.innerHTML = `
      <div class="login-box" style="background:#fff;border-radius:12px;padding:40px 36px;width:340px;max-width:90vw;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.4)">
        <img src="https://cdn.prod.website-files.com/699cb0b733f309dd4bda1b56/69a1adfa32ad89b96dade636_NEW%20LOGO%20COLOR%20copy.png"
             style="height:44px;width:auto;display:block;margin:0 auto 18px" alt="Mason Capital Group">
        <h2 style="font-size:20px;font-weight:900;color:#16162a;margin:0 0 6px">Admin Hub</h2>
        <div style="font-size:13px;color:#6b7280;margin-bottom:24px">Sign in to access the marketing dashboard</div>
        <input id="mcg-user" type="text" placeholder="Username" autocomplete="off"
          style="width:100%;padding:11px 14px;border:1.5px solid #e5e7eb;border-radius:6px;font-size:14px;margin-bottom:10px;box-sizing:border-box;font-family:inherit;outline:none">
        <input id="mcg-pass" type="password" placeholder="Password"
          style="width:100%;padding:11px 14px;border:1.5px solid #e5e7eb;border-radius:6px;font-size:14px;margin-bottom:14px;box-sizing:border-box;font-family:inherit;outline:none">
        <button id="mcg-login-btn"
          style="width:100%;padding:12px;background:#ab012e;color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit">
          Sign In
        </button>
        <div id="mcg-login-error" style="color:#ab012e;font-size:13px;margin-top:10px;min-height:18px"></div>
        <div style="font-size:11px;color:#9ca3af;margin-top:16px">Mason Capital Group • Authorized Access Only</div>
      </div>`;
    document.body.appendChild(overlay);
  }

  // ── DOM refs ─────────────────────────────────────────────────────────────────
  const loginWrap   = document.getElementById('mcg-login');
  const userInput   = document.getElementById('mcg-user');
  const passInput   = document.getElementById('mcg-pass');
  const loginBtn    = document.getElementById('mcg-login-btn');
  const loginError  = document.getElementById('mcg-login-error');
  const goBtn       = document.querySelector('.dash-go-btn');
  const urlPlaceholder = document.querySelector('.dash-url-placeholder');
  const cards       = document.querySelectorAll('.dash-card');

  // ── Inject status badge CSS ──────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    .dash-card-status.dash-working { background:#e0f2fe; color:#0369a1; }
    .dash-card-status.dash-done    { background:#dcfce7; color:#15803d; }
    .dash-card-status.dash-error   { background:#fee2e2; color:#dc2626; }
    #mcg-url-input {
      flex:1; background:transparent; border:none; outline:none;
      font-size:inherit; color:inherit; font-family:inherit; padding:0; width:100%;
    }
    #mcg-url-input::placeholder { opacity:0.55; }
    .dash-go-btn.is-loading { pointer-events:none; opacity:0.55; }
  `;
  document.head.appendChild(style);

  // ── Replace static placeholder div with a real input ────────────────────────
  let urlInput = null;
  if (urlPlaceholder) {
    urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.id = 'mcg-url-input';
    urlInput.placeholder = 'Paste listing URL (homes.com, realtor.com, IDX page)';
    urlInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); handleGenerate(); }
    });
    urlPlaceholder.replaceWith(urlInput);
  }

  // ── Login overlay helpers ────────────────────────────────────────────────────
  function showLogin() {
    if (loginWrap) loginWrap.style.display = 'flex';
  }

  function hideLogin() {
    if (loginWrap) loginWrap.style.display = 'none';
  }

  function setLoginError(msg) {
    if (loginError) loginError.textContent = msg || '';
  }

  // ── Login handler ────────────────────────────────────────────────────────────
  async function handleLogin() {
    const username = (userInput ? userInput.value : '').trim();
    const password = (passInput ? passInput.value : '').trim();
    if (!username || !password) {
      setLoginError('Enter your username and password.');
      return;
    }
    loginBtn.textContent = 'Signing in…';
    loginBtn.disabled = true;
    setLoginError('');

    try {
      const res = await fetch(API_URL + '/api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'bypass-tunnel-reminder': '1' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (res.ok && data.token) {
        storeToken(data.token);
        hideLogin();
        if (passInput) passInput.value = '';
        if (userInput) userInput.value = '';
      } else {
        setLoginError(data.error || 'Invalid credentials.');
      }
    } catch (err) {
      setLoginError('Cannot reach the dashboard server. Check your connection.');
    } finally {
      loginBtn.textContent = 'Sign In';
      loginBtn.disabled = false;
    }
  }

  // ── Card status helpers ──────────────────────────────────────────────────────
  function findCard(name) {
    for (const c of cards) {
      const h = c.querySelector('.dash-card-name');
      if (h && h.textContent.trim() === name) return c;
    }
    return null;
  }

  function setCardStatus(name, state, msg, link) {
    const card = findCard(name);
    if (!card) return;
    const el = card.querySelector('.dash-card-status');
    if (!el) return;
    el.className = 'dash-card-status'; // reset
    el.innerHTML = '';

    switch (state) {
      case 'working':
        el.classList.add('dash-working');
        el.textContent = msg || 'Working…';
        break;
      case 'done':
        el.classList.add('dash-done');
        if (link) {
          const a = document.createElement('a');
          a.href = link; a.target = '_blank';
          a.style.cssText = 'color:inherit;text-decoration:underline;';
          a.textContent = msg || 'View ↗';
          el.appendChild(a);
        } else {
          el.textContent = msg || 'Done';
        }
        break;
      case 'error':
        el.classList.add('dash-error');
        el.textContent = msg || 'Error';
        break;
      case 'ready':
        el.classList.add('dash-ready');
        el.textContent = msg || 'Ready';
        break;
      default:
        el.classList.add('dash-coming');
        el.textContent = msg || 'Coming Soon';
    }
  }

  // ── Generate pipeline ────────────────────────────────────────────────────────
  async function handleGenerate() {
    const token = getToken();
    if (!token) { clearToken(); showLogin(); return; }

    const url = (urlInput ? urlInput.value : '').trim();
    if (!url || !/^https?:\/\//.test(url)) {
      alert('Paste a valid listing URL starting with https://');
      return;
    }

    // Lock UI
    if (goBtn) { goBtn.classList.add('is-loading'); goBtn.textContent = 'Working…'; }
    setCardStatus('Listing Page', 'working', 'Scraping listing…');
    setCardStatus('OM Flip Book', 'working', 'Waiting…');

    const headers = {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + token,
      'bypass-tunnel-reminder': '1',
    };

    try {
      // ── Step 1: Start scrape job ──────────────────────────────────────────
      const startRes = await fetch(API_URL + '/api/scrape', {
        method: 'POST', headers,
        body: JSON.stringify({ url }),
      });
      if (startRes.status === 401) { clearToken(); showLogin(); return; }
      const startData = await startRes.json();
      if (startData.error) throw new Error('Scrape: ' + startData.error);

      const jobId = startData.job_id;

      // ── Step 2: Poll until done ───────────────────────────────────────────
      let listing = null;
      let elapsed = 0;
      while (elapsed < 300000) { // max 5 min
        await new Promise(r => setTimeout(r, 3000));
        elapsed += 3000;

        const dots = '.'.repeat(1 + (elapsed / 3000) % 3);
        setCardStatus('Listing Page', 'working', 'Scraping listing' + dots);

        const pollRes = await fetch(API_URL + '/api/scrape/' + jobId, { headers });
        if (pollRes.status === 401) { clearToken(); showLogin(); return; }
        const poll = await pollRes.json();

        if (poll.status === 'error') throw new Error('Scrape: ' + poll.error);
        if (poll.status === 'done') { listing = poll.listing; break; }
      }
      if (!listing) throw new Error('Scrape timed out after 3 minutes.');

      setCardStatus('Listing Page', 'working', 'Generating page HTML…');

      // ── Step 2: Generate HTML ─────────────────────────────────────────────
      const genRes = await fetch(API_URL + '/api/generate', {
        method: 'POST', headers,
        body: JSON.stringify({ listing }),
      });
      if (genRes.status === 401) { clearToken(); showLogin(); return; }
      const genData = await genRes.json();
      if (genData.error) throw new Error('Generate: ' + genData.error);

      const slug = genData.slug;
      const webflowUrl = genData.webflow_url;
      const previewUrl = webflowUrl || (API_URL + '/preview/' + slug);
      const doneLabel = webflowUrl ? '✓ View on Webflow ↗' : '✓ View Listing Page';

      setCardStatus('Listing Page', 'done', doneLabel, previewUrl);
      setCardStatus('OM Flip Book', 'ready', 'Ready — coming soon');

    } catch (err) {
      setCardStatus('Listing Page', 'error', err.message.substring(0, 80));
      setCardStatus('OM Flip Book', 'ready', 'Ready');
    } finally {
      if (goBtn) { goBtn.classList.remove('is-loading'); goBtn.textContent = 'Generate All'; }
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────────
  function init() {
    if (loginBtn) loginBtn.addEventListener('click', handleLogin);
    if (passInput) passInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') handleLogin();
    });
    if (goBtn) goBtn.addEventListener('click', function (e) {
      e.preventDefault(); handleGenerate();
    });

    // Show login overlay unless a valid token is already stored
    if (getToken()) {
      hideLogin();
    } else {
      showLogin();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
