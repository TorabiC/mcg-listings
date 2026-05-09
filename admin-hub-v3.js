/**
 * MCG Marketing Hub — iframe loader
 * Replaces the Webflow admin-hub page with a full-viewport iframe
 * pointing to the self-contained Marketing Hub on GitHub Pages.
 */
(function () {
  // Inject override styles immediately
  var s = document.createElement('style');
  s.textContent = 'html,body{margin:0!important;padding:0!important;overflow:hidden!important;height:100%!important}body>*{display:none!important}#mcg-hub-frame{position:fixed!important;top:0!important;left:0!important;width:100vw!important;height:100vh!important;border:none!important;z-index:2147483647!important;background:#f5f0eb}';
  document.head.appendChild(s);

  // Create iframe — cache-busted so GitHub Pages always serves fresh
  var iframe = document.createElement('iframe');
  iframe.id = 'mcg-hub-frame';
  iframe.src = 'https://torabic.github.io/mcg-listings/mcg-marketing-hub.html?v=' + Date.now();
  iframe.allow = 'clipboard-write';
  document.body.appendChild(iframe);
})();
