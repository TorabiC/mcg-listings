import os
path = '/Users/camerontorabi/mcg-marketing-dashboard/presentations/north-walton-blvd/index.html'
with open(path, 'r') as f:
    content = f.read()

old = """// Scroll reveal
var observer = new IntersectionObserver(function(entries) {
  entries.forEach(function(e) { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1 });
document.querySelectorAll('.animate').forEach(function(el) { observer.observe(el); });"""

new = """// Scroll reveal — when inside an iframe the outer page scrolls so
// IntersectionObserver never fires; make everything visible immediately.
if (window.self !== window.top) {
  document.querySelectorAll('.animate').forEach(function(el) {
    el.classList.add('visible');
  });
  // Report actual page height so parent iframe can resize correctly
  window.addEventListener('load', function() {
    var h = document.documentElement.scrollHeight;
    window.parent.postMessage({ type: 'listingHeight', height: h }, '*');
  });
} else {
  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) { if (e.isIntersecting) e.target.classList.add('visible'); });
  }, { threshold: 0.1 });
  document.querySelectorAll('.animate').forEach(function(el) { observer.observe(el); });
}"""

assert old in content, 'Pattern not found in file!'
content = content.replace(old, new)
with open(path, 'w') as f:
    f.write(content)
print('Patched. New line count:', content.count('\n'))
