/* Loaded synchronously in <head> of every Studio page. Each section is a full page load whose modules
   rebuild the page (side menu, its indent, section layout) after the first paint, so switching sections
   flashed the raw page and jumped. Keep the content hidden until the modules have run (DOMContentLoaded
   comes after them) and until what they asked to wait for (studioBoot.hold(promise): the first data
   render) is on screen, then fade it in; the side menu is not faded. Never longer than 1.5 s.
   <html data-boot-hold>: the page's main module registers its hold after DOMContentLoaded (a module with
   top-level await does not delay that event), so wait for at least one hold.

   The side menu does not wait for its module: the last menu studio-rail.mjs drew is kept in localStorage
   and put into <aside class="studio-rail"> the moment the parser creates it, with this page's item marked,
   so it is on screen in the first frame, in the same place and state (collapsed or not) as on the page
   before. With cross-document view transitions (Chromium) the browser keeps the menu still and only the
   content changes; the cover is then not needed. */
(function () {
  var root = document.documentElement;
  var expectHold = root.hasAttribute('data-boot-hold');
  var parsed = false;
  var shown = false;
  var holds = [];
  function show() {
    if (shown) return;
    shown = true;
    root.classList.remove('k-boot');
    if (typeof performance !== 'undefined') window.studioBoot.shownAt = Math.round(performance.now());   // ms from navigation start
  }
  function settle() {
    if (shown || !parsed || (expectHold && !holds.length)) return;
    var count = holds.length;
    Promise.all(holds).then(function () {
      if (holds.length !== count) return settle();  // another hold arrived meanwhile
      // Two frames let the built page paint under the cover; a hidden tab gets no frames, so a short timer too.
      requestAnimationFrame(function () { requestAnimationFrame(show); });
      setTimeout(show, 60);
    });
  }
  window.studioBoot = {
    hold: function (promise) {
      if (shown) return;
      holds.push(Promise.resolve(promise).catch(function () {}));
      settle();
    },
  };
  root.classList.add('k-boot');
  document.addEventListener('DOMContentLoaded', function () { parsed = true; settle(); });
  setTimeout(show, 1500);

  var saved = null;
  try { saved = JSON.parse(localStorage.getItem('studio-rail') || 'null'); } catch (e) { /* storage blocked */ }
  root.classList.remove('rail-collapsed');

  // A view transition already hides the swap: show the new page at once instead of fading from the cover.
  if (typeof addEventListener === 'function') {
    addEventListener('pagereveal', function (event) { if (event.viewTransition) show(); });
  }

  function mark(rail) {
    var here = location.pathname + location.search;
    var links = rail.querySelectorAll('a[data-nav], a[data-sub]');
    var hit = null;
    for (var i = 0; i < links.length; i++) {
      var link = links[i];
      link.classList.remove('rail-active', 'rail-sub-active');
      var url = new URL(link.getAttribute('href'), location.href);
      if (!hit && url.pathname + url.search === here) hit = link;
    }
    var box = hit && hit.closest('.rail-subs');
    var parent = hit ? hit.dataset.nav || (box && box.dataset.subsOf) : null;
    var boxes = rail.querySelectorAll('.rail-subs');
    for (var j = 0; j < boxes.length; j++) boxes[j].hidden = boxes[j].dataset.subsOf !== parent;
    if (!hit) return;
    var item = rail.querySelector('a[data-nav="' + parent + '"]');
    if (item) item.classList.add('rail-active');
    if (hit.dataset.sub) hit.classList.add('rail-sub-active');
  }
  function fill(rail) {
    if (rail.dataset.cached || rail.children.length || !saved || !saved.html) return;
    rail.innerHTML = saved.html;
    var settings = rail.querySelector('.rail-settings');
    if (settings && window.innerWidth > 900) settings.open = true;
    rail.dataset.cached = '1';
    try { mark(rail); } catch (e) { /* the module marks it anyway */ }
    if (document.body) document.body.classList.add('has-studio-rail');
  }
  if (typeof MutationObserver === 'function' && saved && saved.html) {
    var watch = new MutationObserver(function () {
      var rail = document.querySelector('aside.studio-rail');
      if (rail && document.body) { fill(rail); watch.disconnect(); }
    });
    watch.observe(root, { childList: true, subtree: true });
    document.addEventListener('DOMContentLoaded', function () { watch.disconnect(); });
  }
})();
