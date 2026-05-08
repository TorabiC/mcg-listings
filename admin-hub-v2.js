/**
 * MCG Admin Hub v3 — Railway-backed script
 * Hosted at: torabic/mcg-listings (GitHub Pages) as admin-hub-v2.js
 * Loaded by the existing mcgAdminHubLoader Webflow registered script.
 *
 * After Railway deploy: update window.MCG_API or the fallback URL below.
 */

(function () {
  // ── Config ──────────────────────────────────────────────────────────────────
  // Set window.MCG_API from a Webflow inline script, or update the fallback below
  const API_URL = (window.MCG_API || 'https://YOUR-RAILWAY-APP.railway.app').replace(/\/$/, '');
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
        headers: { 'Content-Type': 'application/json' },
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
    };

    try {
      // ── Step 1: Scrape ────────────────────────────────────────────────────
      const scrapeRes = await fetch(API_URL + '/api/scrape', {
        method: 'POST', headers,
        body: JSON.stringify({ url }),
      });
      if (scrapeRes.status === 401) { clearToken(); showLogin(); return; }
      const scrapeData = await scrapeRes.json();
      if (scrapeData.error) throw new Error('Scrape: ' + scrapeData.error);

      const listing = scrapeData.listing;
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
      const previewUrl = API_URL + '/preview/' + slug;

      setCardStatus('Listing Page', 'done', '✓ View Listing Page', previewUrl);
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
