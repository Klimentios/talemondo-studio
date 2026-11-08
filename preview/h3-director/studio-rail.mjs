// One side rail for every Studio page. Each page keeps an empty <aside class="studio-rail"> and loads this
// module first (before app.mjs and the page scripts), so all pages show the same sections in the same order.
import './studio-ui.mjs?v=20261008-audit-fixes-1';
import './glass-interactions.mjs?v=20261102-glass-studio-2';
import './dom-translate.mjs?v=20261108-i18n-1';
import { getLocale, setLocale } from './locale.mjs?v=20261108-i18n-1';
const ICONS = {
  profile: '<rect x="3.5" y="2.5" width="13" height="15" rx="2"/><circle cx="10" cy="8" r="2.4"/><path d="M6.5 14c.7-1.8 2-2.7 3.5-2.7s2.8.9 3.5 2.7"/>',
  agent: '<path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H9l-4 3v-3H4A1.5 1.5 0 0 1 2.5 13V6A1.5 1.5 0 0 1 4 4.5z"/><path d="M6.5 9.5h.01M10 9.5h.01M13.5 9.5h.01" stroke-width="2.2"/>',
  analytics: '<path d="M3 17V3M3 17h14M6 14V9M10 14V5M14 14V7"/>',
  franchises: '<rect x="2" y="3" width="6" height="5" rx="1"/><rect x="12" y="12" width="6" height="5" rx="1"/><path d="M8 5h6v7M5 8v6h7"/>',
  create: '<rect x="3" y="4" width="14" height="12" rx="2.5"/><path d="m8.5 7.6 4 2.4-4 2.4z" fill="currentColor" stroke="none"/>',
  story: '<rect x="2.5" y="6" width="5" height="8" rx="1.2"/><rect x="12.5" y="6" width="5" height="8" rx="1.2"/><path d="M7.5 10h5"/>',
  library: '<rect x="3" y="3" width="6" height="6" rx="1.4"/><rect x="11" y="3" width="6" height="6" rx="1.4"/><rect x="3" y="11" width="6" height="6" rx="1.4"/><rect x="11" y="11" width="6" height="6" rx="1.4"/>',
  editor: '<path d="M3 6h14M3 10h9M3 14h11"/><path d="M14.5 8v4" /><rect x="12" y="12.5" width="5" height="3" rx="1"/>',
  audio: '<path d="M3 10h1.5M6.5 7v6M9.5 4.5v11M12.5 7.5v5M15.5 9v2"/>',
  edit: '<path d="m4 16 8.5-8.5M11 5l1-2 1 2 2 1-2 1-1 2-1-2-2-1zM15 11l.6 1.3 1.4.7-1.4.7L15 15l-.6-1.3-1.4-.7 1.4-.7z"/>',
  motion: '<circle cx="6" cy="5" r="2"/><path d="M6 7v5l-2.5 5M6 12l3 5M3.5 9.5 6 8l3 1.5"/><path d="M12 6h5v8h-5z"/><path d="m14 8.5 2 1.5-2 1.5z" fill="currentColor" stroke="none"/>',
  refine: '<circle cx="10" cy="7" r="3"/><path d="M4.5 17c.8-3.2 2.9-5 5.5-5s4.7 1.8 5.5 5"/><path d="M3 3h3M3 3v3M17 3h-3M17 3v3" />',
  queue: '<path d="M4 5h12M4 10h12M4 15h7"/><circle cx="15" cy="15" r="2.4"/>',
  workflows: '<circle cx="5" cy="5" r="2"/><circle cx="15" cy="10" r="2"/><circle cx="5" cy="15" r="2"/><path d="M7 5h2.5a2 2 0 0 1 2 2v1.5M7 15h2.5a2 2 0 0 0 2-2v-1.5M11.5 10H13"/>',
  setup: '<path d="M10 2.5 16.5 6v8L10 17.5 3.5 14V6z"/><path d="m7 10 2 2 4-4"/>',
  compute: '<rect x="5" y="5" width="10" height="10" rx="2"/><rect x="8" y="8" width="4" height="4" rx=".6"/><path d="M8 2.5V5M12 2.5V5M8 15v2.5M12 15v2.5M2.5 8H5M2.5 12H5M15 8h2.5M15 12h2.5"/>',
};

// Related pages are one item with sub-links (shown while you are in that section): the franchise board and its
// passport, video generation and its queue/GPU views.
const GROUPS = [
  { ru: 'Франшизы', en: 'Franchises', items: [
    { id: 'franchises', href: '/franchise-board/', ru: 'Франшизы', en: 'Franchises', subs: [
      { id: 'franchises', href: '/franchise-board/', ru: 'Доска', en: 'Board' },
      { id: 'profile', href: '/franchise-profile.html', ru: 'Паспорт', en: 'Profile' },
    ] },
  ] },
  { ru: 'Создание', en: 'Create', items: [
    { id: 'create', href: '/index.html', ru: 'Генерация видео', en: 'Video Generation', subs: [
      { id: 'create', href: '/index.html', ru: 'Видео', en: 'Video' },
      { id: 'queue', href: '/#jobQueue', ru: 'Очередь', en: 'Queue' },
      { id: 'compute', href: '/?view=compute', ru: 'GPU и модели', en: 'GPU and models' },
    ] },
    { id: 'scenes', href: '/?view=scenes', ru: 'Видео студия', en: 'Video Studio', icon: 'story' },
    { id: 'assets', href: '/?view=assets', ru: 'Библиотека', en: 'Library', icon: 'library' },
  ] },
  { ru: 'Инструменты', en: 'Tools', items: [
    { id: 'editor', href: '/editor/', ru: 'Монтаж', en: 'Edit timeline', icon: 'editor' },
    { id: 'audio', href: '/audio-studio.html', ru: 'Звук и речь', en: 'Sound and voice' },
    { id: 'workflows', href: '/workflows.html', ru: 'ComfyUI', en: 'ComfyUI', subs: [
      { id: 'workflows', href: '/workflows.html', ru: 'Свои воркфлоу', en: 'My workflows' },
      { id: 'comfyui', href: '/comfyui.html', ru: 'Редактор графов', en: 'Graph editor' },
      { id: 'comfy-nodes', href: '/comfy-nodes.html', ru: 'Узлы и модели', en: 'Nodes and models' },
    ] },
    { id: 'setup', href: '/setup.html', ru: 'Первый запуск', en: 'First run' },
  ] },
];
// sub id → the item that holds it
const PARENT = Object.fromEntries(GROUPS.flatMap((group) => group.items.flatMap((item) =>
  (item.subs || [item]).map((sub) => [sub.id, item.id]))));

export function currentSection() {
  const path = location.pathname;
  if (path.startsWith('/analytics')) return 'analytics';
  if (path.startsWith('/franchise-board')) return 'franchises';
  if (path.endsWith('/franchise-profile.html')) return 'profile';
  if (path.endsWith('/agent-chat.html')) return 'agent';
  if (path.startsWith('/editor')) return 'editor';
  if (path.endsWith('/audio-studio.html')) return 'audio';
  if (path.endsWith('/workflows.html')) return 'workflows';
  if (path.endsWith('/comfyui.html')) return 'comfyui';
  if (path.endsWith('/comfy-nodes.html')) return 'comfy-nodes';
  if (path.endsWith('/setup.html')) return 'setup';
  const view = new URLSearchParams(location.search).get('view');
  if (['scenes', 'assets', 'compute'].includes(view)) return view;
  return location.hash === '#jobQueue' ? 'queue' : 'create';
}

const english = () => getLocale() === 'en';
const icon = (id) => `<svg class="rail-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[id] || ''}</svg>`;

function render(rail) {
  let en = false;
  try { en = english(); } catch { /* storage blocked: Russian */ }
  // The queue lives inside «Создать»: from that view it is a same-page switch, from anywhere else a link back.
  const onIndex = ['/', '/index.html'].includes(location.pathname) && !new URLSearchParams(location.search).get('view');
  const groups = GROUPS.map((group, index) => `
    <span class="rail-caption" data-ru="${group.ru}" data-en="${group.en}">${en ? group.en : group.ru}</span>
    <nav class="${index ? 'rail-secondary' : 'rail-primary'}" aria-label="${en ? group.en : group.ru}">
      ${group.items.map((item) => {
        const badge = item.badge ? `<em class="rail-badge">${item.badge}</em>` : '';
        const subs = (item.subs || []).map((sub) => {
          // On the main page the queue is a view of the same page; elsewhere it is a link back to it.
          const href = sub.id === 'queue' && onIndex ? '#jobQueue' : sub.href;
          return `<a class="rail-sub" href="${href}" title="${en ? sub.en : sub.ru}" data-sub="${sub.id}" data-ru="${sub.ru}" data-en="${sub.en}"><span class="rail-label">${en ? sub.en : sub.ru}</span></a>`;
        }).join('');
        return `<a href="${item.href}" title="${en ? item.en : item.ru}" data-nav="${item.id}" data-ru="${item.ru}" data-en="${item.en}">${icon(item.icon || item.id)}<span class="rail-label">${en ? item.en : item.ru}</span>${badge}</a>`
          + (subs ? `<div class="rail-subs" data-subs-of="${item.id}" hidden>${subs}</div>` : '');
      }).join('')}
    </nav>`).join('');
  rail.setAttribute('aria-label', en ? 'Studio sections' : 'Разделы Studio');
  rail.innerHTML = `
    <a class="rail-brand" href="/" aria-label="TaleMondo — главная"><span class="brand-mark" aria-hidden="true"><img src="/assets/talemondo-mark.svg" alt="" width="32" height="40"></span><span><strong>TaleMondo</strong><small>AI VIDEO STUDIO</small></span></a>
    <nav class="rail-primary" aria-label="Главная">
      <a href="/agent-chat.html" data-main="agent">${icon('agent')}<span class="rail-label">${en ? 'Chat' : 'Чат'}</span></a>
      <a href="/analytics/" data-main="analytics">${icon('analytics')}<span class="rail-label">${en ? 'Channel analytics' : 'Аналитика каналов'}</span></a>
    </nav>
    <details class="rail-settings"><summary title="${en ? 'Tools and settings' : 'Инструменты и настройки'}"><svg class="rail-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="10" cy="10" r="3"/><path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4m8.6 8.6 1.4 1.4m0-11.4-1.4 1.4m-8.6 8.6-1.4 1.4"/></svg><span class="rail-label">${en ? 'Tools and settings' : 'Инструменты и настройки'}</span></summary>
    ${groups}
    </details>
    <div class="rail-project"><span>${en ? 'Current project' : 'Текущий проект'}</span><a href="/?view=scenes"><strong id="railProjectName">Мой фильм</strong><small id="railProjectMeta">Сцены и шоты</small></a></div>
    <p class="rail-note"><strong>${en ? 'Stored on this Mac' : 'Локально на Mac'}</strong><span>${en ? 'References, queue and MP4' : 'Референсы, очередь и MP4'}</span></p>
    <div class="rail-foot" data-no-i18n>
      <button type="button" class="rail-lang" data-rail-lang aria-label="${en ? 'Переключить на русский' : 'Switch to English'}"><span data-on="${!en}">RU</span><span aria-hidden="true">/</span><span data-on="${en}">EN</span></button>
      <a class="rail-credit" href="https://polyformproject.org/licenses/noncommercial/1.0.0" target="_blank" rel="noopener" title="PolyForm Noncommercial 1.0.0">© 2026 Klimentios</a>
    </div>`;
  rail.querySelector('.rail-settings').open = window.innerWidth > 900;
}

export function setRailActive(id) {
  const parent = PARENT[id] || id;
  document.querySelectorAll('.studio-rail [data-main]').forEach((link) => { const active = link.dataset.main === id; link.classList.toggle('rail-active', active); if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  for (const link of document.querySelectorAll('.studio-rail nav a[data-nav]')) {
    const active = link.dataset.nav === parent;
    link.classList.toggle('rail-active', active);
    if (active && !link.nextElementSibling?.matches?.('.rail-subs')) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  // The sub-links of the current item open; the current page among them is marked.
  for (const box of document.querySelectorAll('.studio-rail .rail-subs')) box.hidden = box.dataset.subsOf !== parent;
  for (const sub of document.querySelectorAll('.studio-rail a[data-sub]')) {
    const current = sub.dataset.sub === id && sub.closest('.rail-subs')?.dataset.subsOf === parent;
    sub.classList.toggle('rail-sub-active', current);
    if (current) sub.setAttribute('aria-current', 'page'); else sub.removeAttribute('aria-current');
  }
}

function updateCopy() {
  let en = false;
  try { en = english(); } catch { /* Russian */ }
  for (const node of document.querySelectorAll('.studio-rail [data-ru][data-en]')) {
    const label = node.querySelector('.rail-label') || node;
    label.textContent = en ? node.dataset.en : node.dataset.ru;
  }
}

// page-boot.js puts the last drawn menu (kept here) on screen before this module runs, and restores the
// collapsed state before the first paint, so the menu stays put when the section changes.
const CACHE = 'studio-rail';
function remember() {
  try {
    const copy = rail.cloneNode(true);
    copy.querySelectorAll('.rail-active, .rail-sub-active').forEach((node) => node.classList.remove('rail-active', 'rail-sub-active'));
    copy.querySelectorAll('[aria-current]').forEach((node) => node.removeAttribute('aria-current'));
    localStorage.setItem(CACHE, JSON.stringify({ html: copy.innerHTML, collapsed: false }));
  } catch { /* storage blocked: the menu is drawn by this module as before */ }
}
document.documentElement.classList.remove('rail-collapsed');

let rail = document.querySelector('aside.studio-rail');
if (!rail) {
  rail = document.createElement('aside');
  rail.className = 'studio-rail';
  document.body.prepend(rail);
}
render(rail);
setRailActive(currentSection());
document.body.classList.add('has-studio-rail');
rail.addEventListener('click', (event) => { if (event.target.closest('[data-rail-lang]')) setLocale(getLocale() === 'en' ? 'ru' : 'en'); });
rail.addEventListener('click', (event) => { if (window.innerWidth > 900 && event.target.closest('.rail-settings > summary')) event.preventDefault(); });
addEventListener('resize', () => { if (innerWidth > 900) rail.querySelector('.rail-settings').open = true; });
remember();
addEventListener('pagehide', remember);   // the project name and counts filled in later go along
addEventListener('hashchange', () => setRailActive(currentSection()));
addEventListener('studio:localechange', updateCopy);
window.studioRail = { setActive: setRailActive, current: currentSection };
