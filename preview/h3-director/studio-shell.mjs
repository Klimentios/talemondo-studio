import { getLocale } from './locale.mjs?v=20261108-i18n-1';


function updateRailCopy() {
  const english = getLocale() === 'en';
  // Rail labels are translated by studio-rail.mjs.
  const audioLink = document.querySelector('.top-nav a[href="/audio-studio.html"]');
  if (audioLink) audioLink.textContent = english ? 'Sound and voice' : 'Звук и речь';
}
addEventListener('studio:localechange', updateRailCopy);
updateRailCopy();

// Links from the other Studio pages should open the actual queue or library,
// not only land on a hash in the video workspace.
if (['#jobQueue', '#referenceLibrary'].includes(location.hash)) {
  const destination = location.hash;
  let attempts = 0;
  let opened = () => {};
  // The page-boot cover stays until the queue or library is open, not over the Create view first.
  window.studioBoot?.hold(new Promise((resolve) => { opened = resolve; }));
  const openWhenReady = () => {
    if (location.hash !== destination) return opened();
    if (!window.h3Studio?.ready?.()) {
      if (attempts++ < 600) setTimeout(openWhenReady, 25);
      else opened();
      return;
    }
    if (destination === '#jobQueue') document.getElementById('navQueue')?.click();
    else document.getElementById('openLibrary')?.click();
    opened();
  };
  openWhenReady();
}
