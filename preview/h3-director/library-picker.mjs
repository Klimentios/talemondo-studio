// One Studio library window: cards with thumbnails, sections, search, upload. Used by Motion Control and by
// select-library.mjs (any <select> of assets opens this instead of a text list).
//   const value = await openLibraryPicker({ kind: 'image' | 'video', title, current, jobs: true });
//   → an asset_id, 'job:<id>' for a finished H3 video (when jobs: true), or null when closed.
import { COLLECTIONS, loadFranchiseMemberships } from './library-collections.mjs?v=20261030-library-roles-1';

const IMAGE_SECTIONS = [
  ['recent', 'Мои последние', () => true], ['heroes', 'Герои и лица', (a) => ['Лицо героя', 'Персонаж целиком'].includes(a.role)],
  ['outfit', 'Одежда', (a) => a.role === 'Костюм'], ['props', 'Предметы', (a) => a.role === 'Предмет'],
  ['lighting', 'Свет', (a) => a.role === 'Свет'], ['effects', 'Эффекты и стиль', (a) => a.role === 'Стиль'],
  ['places', 'Локации', (a) => ['Окружение', 'Кадр сцены', 'Композиция'].includes(a.role)], ['all', 'Все', () => true],
];
const VIDEO_SECTIONS = [
  ['recent', 'Мои последние', () => true], ['results', 'Готовые ролики', null],
  ['edits', 'Готовые правки', null],
  ['motion', 'Движение', (a) => /Движение|Исходное|Актёрская|Камера/.test(a.role || '')], ['all', 'Все', () => true],
];
const STYLE_ID = 'studio-library-picker-style';
const CSS = `
.klp{width:min(1100px,94vw);max-height:88vh;padding:0;border:1px solid #2a3036;border-radius:18px;background:#14181c;color:#eef1f2;font-family:Inter,system-ui,sans-serif}
.klp::backdrop{background:rgba(0,0,0,.6)}
.klp form{display:grid;grid-template-rows:auto auto 1fr auto;max-height:88vh;margin:0}
.klp header{display:flex;align-items:center;justify-content:space-between;padding:14px 18px 6px}
.klp header h2{margin:0;font-size:18px}
.klp header button{border:0;background:none;color:#8f989d;font-size:26px;cursor:pointer}
.klp-tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:6px 18px 12px}
.klp-chips{display:flex;flex-wrap:wrap;gap:6px}
.klp-chips button{padding:7px 12px;border:1px solid #2a3036;border-radius:999px;background:transparent;color:#8f989d;font:inherit;font-size:13px;cursor:pointer}
.klp-chips button[aria-selected=true]{border-color:#d9ff39;color:#eef1f2}
.klp-tools input[type=search]{flex:1 1 180px;padding:8px 10px;border:1px solid #2a3036;border-radius:10px;background:#12161a;color:#eef1f2;font:inherit}
.klp-tools select{padding:8px 10px;border:1px solid #2a3036;border-radius:10px;background:#12161a;color:#eef1f2;font:inherit}
.klp-upload{padding:8px 12px;border-radius:10px;background:#d9ff39;color:#141a05;font-size:13px;font-weight:700;cursor:pointer}
.klp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;padding:0 18px 14px;overflow-y:auto}
.klp-tile{display:grid;gap:5px;padding:6px;border:1px solid #2a3036;border-radius:12px;background:#1b2025;color:#eef1f2;font:inherit;text-align:left;cursor:pointer}
.klp-tile[aria-pressed=true]{border-color:#d9ff39}
.klp-tile img,.klp-fallback{width:100%;aspect-ratio:1;border-radius:8px;object-fit:cover;background:#0f1215}
.klp-fallback{display:grid;place-items:center;color:#8f989d;font-size:26px}
.klp-tile small{overflow:hidden;color:#8f989d;font-size:11px;text-overflow:ellipsis;white-space:nowrap}
.klp-empty{color:#8f989d;font-size:13px}
.klp-more{margin:0 18px 16px;padding:9px;border:1px solid #2a3036;border-radius:10px;background:transparent;color:#eef1f2;font:inherit;cursor:pointer}`;

async function json(url, options) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Ошибка ${response.status}`);
  return body;
}

const thumb = (item) => item.job ? `/api/jobs/${encodeURIComponent(item.job.job_id)}/poster` : `/api/assets/${encodeURIComponent(item.asset_id)}/thumbnail`;

export function libraryItems(kind, assets, jobs, section, query, edits = []) {
  const sections = kind === 'video' ? VIDEO_SECTIONS : IMAGE_SECTIONS;
  const [, , match] = sections.find(([id]) => id === section) || sections[0];
  const q = String(query || '').trim().toLowerCase();
  let items;
  if (section === 'edits') {
    items = edits.filter(j => j.state === 'completed').map(j => ({value:`edit:${j.edit_id}`, edit:j,
      name:`Правка ${j.edit_id.slice(0,8)} · ${String(j.created_at || '').slice(5,16)}`}));
  } else if (section === 'results') {
    items = jobs.filter((job) => job.state === 'completed').map((job) => ({ value: `job:${job.job_id}`, job,
      name: `${String(job.original_prompt || '').replace(/\s+/g, ' ').slice(0, 60)} · ${String(job.created_at || '').slice(5, 16)}` }));
  } else {
    items = assets.filter((a) => a.kind === kind && match(a)).map((a) => ({ value: a.asset_id, asset_id: a.asset_id, name: a.original_name }));
  }
  if (q) items = items.filter((item) => item.name.toLowerCase().includes(q));
  return section === 'recent' ? items.slice(0, 24) : items;
}

export async function openLibraryPicker({ kind = 'image', title, current = '', jobs: withJobs = false, edits: withEdits = false, role, section: initialSection } = {}) {
  if (!document.getElementById(STYLE_ID)) document.head.append(Object.assign(document.createElement('style'), { id: STYLE_ID, textContent: CSS }));
  const dialog = document.createElement('dialog');
  dialog.className = 'klp';
  dialog.innerHTML = `<form method="dialog"><header><h2></h2><button value="" aria-label="Закрыть">×</button></header>
    <div class="klp-tools"><div class="klp-chips" role="tablist" aria-label="Разделы"></div>
    <select aria-label="Франшиза"><option value="">Выбери франшизу</option></select>
    <input type="search" placeholder="Поиск по имени" aria-label="Поиск по имени" /></div>
    <div class="klp-grid"></div><button type="button" class="klp-more" hidden>Показать ещё</button></form>`;
  document.body.append(dialog);
  const [heading, chips, franchise, search, grid, more] = ['h2', '.klp-chips', 'select', 'input[type=search]', '.klp-grid', '.klp-more']
    .map((selector) => dialog.querySelector(selector));
  heading.textContent = title || (kind === 'video' ? 'Видео' : 'Картинки');
  for (const [id, label] of COLLECTIONS.filter(([id]) => id !== 'review')) franchise.add(new Option(label, id));
  franchise.value = sessionStorage.getItem('studio.library.franchise') || '';
  let assets = [], jobList = [], edits = [];
  let memberships = new Map();
  let section = initialSection || (withEdits && String(current).startsWith('edit:') ? 'edits' : withJobs && kind === 'video' && String(current).startsWith('job:') ? 'results' : 'recent'), limit = 60;
  const sections = (kind === 'video' ? VIDEO_SECTIONS : IMAGE_SECTIONS).filter(([id]) => (id !== 'results' || withJobs) && (id !== 'edits' || withEdits));

  const render = () => {
    chips.replaceChildren(...sections.map(([id, label]) => {
      const chip = Object.assign(document.createElement('button'), { type: 'button', textContent: label });
      chip.setAttribute('role', 'tab');
      chip.setAttribute('aria-selected', String(id === section));
      chip.onclick = () => { section = id; limit = 60; render(); };
      return chip;
    }));
    const selected = COLLECTIONS.find(([id]) => id === franchise.value)?.[1];
    const scoped = selected ? assets.filter(asset => {
      const names = new Set((memberships.get(asset.asset_id) || []).map(link => link.franchise));
      return names.size === 1 && names.has(selected);
    }) : [];
    const items = libraryItems(kind, scoped, [], section, search.value, []);
    grid.replaceChildren(...items.slice(0, limit).map((item) => {
      const tile = Object.assign(document.createElement('button'), { type: 'button', className: 'klp-tile', title: item.name });
      tile.setAttribute('aria-pressed', String(item.value === current));
      const img = item.edit ? Object.assign(document.createElement('video'), { muted:true, preload:'metadata', src:`/api/video_edit/jobs/${encodeURIComponent(item.edit.edit_id)}/video` }) : Object.assign(document.createElement('img'), { loading: 'lazy', alt: '', src: thumb(item) });
      if(item.edit){img.setAttribute('aria-label','Просмотр готовой правки');img.style.cssText='width:100%;aspect-ratio:1;object-fit:contain;background:#0f1215;border-radius:8px';}
      img.onerror = () => img.replaceWith(Object.assign(document.createElement('span'), { className: 'klp-fallback', textContent: kind === 'video' ? '▶' : '▣' }));
      tile.append(img, Object.assign(document.createElement('small'), { textContent: item.name }));
      tile.onclick = () => dialog.close(item.value);
      return tile;
    }));
    if (!items.length) grid.append(Object.assign(document.createElement('p'), { className: 'klp-empty', textContent: selected ? 'В этой франшизе нет подходящих привязанных файлов. Добавь файл в карточку франшизы.' : 'Сначала выбери франшизу.' }));
    more.hidden = items.length <= limit;
  };
  search.oninput = () => { limit = 60; render(); };
  more.onclick = () => { limit += 60; render(); };
  franchise.onchange = () => { sessionStorage.setItem('studio.library.franchise', franchise.value); limit = 60; render(); };
  const result = new Promise((resolve) => dialog.addEventListener('close', () => { resolve(dialog.returnValue || null); dialog.remove(); }, { once: true }));
  dialog.showModal();
  render();
  try {
    [assets, jobList, edits] = await Promise.all([json('/api/assets'), withJobs ? json('/api/jobs') : [], withEdits ? json('/api/video_edit/jobs') : []]);
    memberships = await loadFranchiseMemberships(json, new Map(assets.map(asset => [asset.asset_id, asset])));
    assets.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    if (dialog.open) render();
  } catch (error) { heading.textContent = `Медиатека недоступна: ${error.message}`; }
  return result;
}
