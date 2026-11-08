// Story canvas («Сюжет»): the scene as a full-screen node canvas like a long-video board —
// materials on the left feed every shot, shots run left to right (a solid link = continue from
// the previous last frame), the scene film at the end. Floating toolbars; selecting a node opens
// the floating panel on the right.
//
// Rules that make it usable:
// - the DOM is rebuilt only when the structure changes (shots, order, links, materials, format);
//   job progress, takes and text are patched in place, a focused field is never touched;
// - one poll of /api/jobs for the whole canvas, paused while the tab is hidden;
// - a text field writes only to its own shot (editCuts by id), never through the Create composer.
// Data lives in window.h3Studio; shots are run through the storyboard's own actions
// (start frames, colour samples, queue) so this module adds no second generation path.
import {
  MATERIAL_W, FILM_W, shotWidth, mergeLayout, canvasMaterials, structureKey, selectedJobId, takeList,
  nodeState, STATUS_TEXT, shotLabel, isTechnicalPrompt, filmPlan, plural, bounds, autoNote, shotMaterials, materialGroup,
} from './story-canvas-model.mjs?v=20261101-keyframe-1';
import { loadScenePlaces, sceneFranchiseId, scenePlaces } from './scene-places.mjs?v=20261101-place-1';
import { placeNote } from './shot-references.mjs?v=20261101-breakdown-1';
import { shotPlan, storyboardState, acceptedShot, MIN_SECONDS, MAX_SECONDS } from './storyboard-model.mjs?v=20261102-take-admission-1';
import { isFantasy } from './story-canvas-worlds.mjs?v=20261103-codex-world-1';
import { installBoard } from './story-canvas-board.mjs?v=20261101-staging-1';
import { installChatImages } from './chat-images.mjs?v=20261101-agent-brief-2';
import { installSceneOverview } from './scene-overview.mjs?v=20261102-talemondo-1';

const sceneView = new URLSearchParams(location.search).get('view') === 'scenes';
const POLL_MS = 4000;
const esc = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const videoUrl = (jobId) => `/api/jobs/${encodeURIComponent(jobId)}/video`;
const assetFile = (id) => `/api/assets/${encodeURIComponent(id)}/file`;
const pad = (n) => String(n).padStart(2, '0');
// Small JPEG for cards; the full file only if the thumbnail is unavailable.
// thumbnail → full file → a few retries (Studio may be restarting; a broken <img> never retries itself).
const thumbImg = (id, extra = '') => `<img ${extra} src="/api/assets/${encodeURIComponent(id)}/thumbnail" onerror="window.h3RetryThumb(this,'${encodeURIComponent(id)}')" alt="" loading="lazy" draggable="false">`;
window.h3RetryThumb = (img, id) => {
  const attempt = Number(img.dataset.retry || 0) + 1;
  img.dataset.retry = String(attempt);
  if (attempt === 1) { img.src = `/api/assets/${id}/file`; return; }
  if (attempt > 6) { img.onerror = null; return; }
  setTimeout(() => { img.src = `/api/assets/${id}/thumbnail?retry=${attempt}`; }, 3000);
};
const PORT_Y = 0.5; // ports sit at the middle of the media

const state = {
  shell: null, viewport: null, world: null, edges: null, panel: null, toast: null,
  jobs: new Map(), jobsLoaded: false, key: '', positions: {}, view: { x: 40, y: 40, zoom: 0.8 },
  pinned: {}, selected: null, busy: new Set(), lastFilm: null, filmBusy: false, inspectorHome: null, panelKey: '',
};

const studio = () => window.h3Studio;
function scene() {
  const s = studio();
  let info = {};
  try { info = s.sceneState(); } catch { /* draft still loading */ }
  const scenes = info.scenes || [];
  const active = scenes.find((item) => item.id === info.activeSceneId) || scenes[0] || {};
  return { scenes, active, cuts: s.cuts() || [], references: s.references() || [], places: places() };
}
// The franchise passport places arrive once per franchise; the canvas redraws its links when they do.
function places() {
  const id = sceneFranchiseId(studio().project?.());
  const known = scenePlaces(id);
  if (id && !known.length && !placesAsked.has(id)) {
    placesAsked.add(id);
    void loadScenePlaces(id).then((list) => { if (list.length) render(); });
  }
  return known;
}
const placesAsked = new Set();
const sceneId = () => scene().active.id || 'scene';
const cutIndex = (id) => scene().cuts.findIndex((cut) => cut.id === id);
const aspect = () => studio().project()?.aspect || document.getElementById('aspect')?.value || '16:9';

// --- persistence of the view -----------------------------------------------------------------

const storeKey = () => `studio.story-canvas.v3.${sceneId()}`;
function loadSaved() { try { return JSON.parse(localStorage.getItem(storeKey()) || '{}'); } catch { return {}; } }
function saveView() {
  // Only nodes the user dragged are remembered; everything else follows the shot order.
  try { localStorage.setItem(storeKey(), JSON.stringify({ pinned: state.pinned, view: state.view })); } catch { /* private mode */ }
}

// --- jobs ------------------------------------------------------------------------------------

async function refreshJobs() {
  try {
    const response = await fetch('/api/jobs');
    if (!response.ok) throw new Error(String(response.status));
    state.jobs = new Map((await response.json()).map((job) => [job.job_id, job]));
    state.jobsLoaded = true;
  } catch { state.jobsLoaded = false; }
}

// --- shell -----------------------------------------------------------------------------------

function buildShell(host) {
  const shell = document.createElement('section');
  shell.className = 'kc-shell';
  shell.setAttribute('aria-label', 'Холст сцены');
  shell.innerHTML = `
    <div class="kc-viewport" tabindex="-1">
      <div class="kc-world"><svg class="kc-edges" aria-hidden="true"></svg></div>
    </div>
    <div class="kc-float kc-top-left">
      <select data-kc-field="scene" aria-label="Сцена"></select>
      <button type="button" data-kc-act="rename-scene" title="Переименовать сцену" aria-label="Переименовать сцену">✎</button>
      <button type="button" data-kc-act="reset-scene" title="Сбросить сцену: убрать все шоты, материалы и настройки" aria-label="Сбросить сцену">↺</button>
      <button type="button" data-kc-act="delete-scene" title="Удалить сцену" aria-label="Удалить сцену">🗑</button>
      <button type="button" data-kc-act="new-scene" title="Новая сцена">＋ Сцена</button>
      <button type="button" data-kc-act="scene-settings" title="Материалы сцены: роли, кому принадлежат, загрузка; seed, стиль, цвет между шотами">⚙ Материалы и настройки</button>
      <span class="kc-summary" data-kc="summary"></span>
    </div>
    <div class="kc-float kc-top-right">
      <button type="button" class="k-btn k-btn--sm" data-kc-act="overview" aria-expanded="false">▦ Обзор сцены</button>
      <button type="button" data-kc-act="director" class="kc-director-btn">✦ AI Director</button>
      <button type="button" data-kc-act="whole-film" title="Монтаж всех сцен проекта: переходы, музыка, затемнения и сборка MP4">▣ Весь фильм</button>
    </div>
    <div class="kc-popover" data-kc="director-pop" hidden></div>
    <div class="kc-float kc-dock">
      <button type="button" data-kc-act="add-shot" title="Новый шот в конец сцены"><span>＋</span>Шот</button>
      <button type="button" data-kc-act="add-material" title="Герой, локация, костюм, голос — из библиотеки или с диска"><span>◩</span>Материал</button>
      <button type="button" data-chat-images="open" title="Подготовить картинку с контекстом сцены для обычного чата ChatGPT через MCP"><span>🎨</span>Картинка · ChatGPT</button>
      <span class="kc-sep"></span>
      <button type="button" data-kc-act="chain" class="kc-accent" title="Создать все шоты по порядку; продолжения ждут предыдущий"><span>▶▶</span>Собрать сцену</button>
    </div>
    <div class="kc-float kc-zoom">
      <button type="button" data-kc-act="zoom-out" aria-label="Уменьшить">−</button>
      <output data-kc="zoom">80%</output>
      <button type="button" data-kc-act="zoom-in" aria-label="Увеличить">＋</button>
      <button type="button" data-kc-act="fit" title="Показать всю сцену" aria-label="Показать всю сцену">⤢</button>
      <button type="button" data-kc-act="relayout" title="Разложить шоты в одну ленту" aria-label="Разложить">≡</button>
    </div>
    <aside class="kc-panel" hidden></aside>
    <div class="kc-toast" role="status" aria-live="polite" hidden></div>`;
  host.prepend(shell);
  Object.assign(state, {
    shell, viewport: shell.querySelector('.kc-viewport'), world: shell.querySelector('.kc-world'),
    edges: shell.querySelector('.kc-edges'), panel: shell.querySelector('.kc-panel'), toast: shell.querySelector('.kc-toast'),
  });
  shell.addEventListener('click', onClick);
  shell.addEventListener('input', onInput);
  shell.addEventListener('change', onChange);
  state.overview = installSceneOverview({ shell, snapshot: () => {
    const { active, cuts, references } = scene();
    return { title: active.title, cuts, references, jobs: state.jobs, board: storyboardState(studio().project()) };
  }, selectShot: (id) => select(`s:${id}`) });
  installChatImages({ shell, studio, toast, sync, selectedShot: () => {
    const cuts = scene().cuts;
    const id = state.selected?.startsWith('s:') ? state.selected.slice(2) : null;
    return cuts.find((cut) => cut.id === id) || (cuts.length === 1 ? cuts[0] : null);
  } });
  installPanZoom();
  installBoard({
    shell, viewport: state.viewport, world: state.world, edges: () => state.edges,
    scene, studio, select, sync, toast, act, pin, reveal, resetLayout: () => act('relayout'),
    selected: () => state.selected, jobs: () => state.jobs, positions: () => state.positions,
    getView: () => state.view, setView: (view) => { state.view = view; applyView(); saveView(); },
  });
  fitToWindow();
  // Measure actual layout changes instead of moving the canvas again at arbitrary delays.
  const geometry = new ResizeObserver(fitToWindow);
  for (const node of document.querySelectorAll('.studio-rail, .site-header')) geometry.observe(node);
}

// The canvas takes the whole work area: right of the side rail, below the top bar.
function fitToWindow() {
  if (!state.shell) return;
  const rail = document.querySelector('.studio-rail');
  const header = document.querySelector('.site-header');
  const railRect = rail && getComputedStyle(rail).display !== 'none' ? rail.getBoundingClientRect() : null;
  const verticalRail = railRect && railRect.width < innerWidth / 2 && railRect.height > innerHeight / 2;
  const left = verticalRail ? Math.max(0, railRect.right) : 0;
  const top = Math.max(header?.getBoundingClientRect().bottom || 0, !verticalRail && railRect?.bottom || 0);
  Object.assign(state.shell.style, { left: `${left}px`, top: `${top}px` });
}

function toast(text, ms = 6000) {
  state.toast.textContent = text;
  state.toast.hidden = !text;
  clearTimeout(state.toastTimer);
  if (text) state.toastTimer = setTimeout(() => { state.toast.hidden = true; }, ms);
}

// --- full render (structure changed) ---------------------------------------------------------

function render({ fit = false } = {}) {
  const { cuts, references } = scene();
  const saved = loadSaved();
  state.pinned = saved.pinned || {};
  state.positions = mergeLayout(state.pinned, cuts, references, { aspect: aspect() });
  if (saved.view && !fit) state.view = saved.view;
  state.world.querySelectorAll('.kc-node, .kc-link').forEach((node) => node.remove());
  const frag = document.createDocumentFragment();
  const materials = canvasMaterials(references);
  if (materials.length) materials.forEach((ref) => frag.append(materialNode(ref)));
  else frag.append(addMaterialNode());
  cuts.forEach((cut, index) => frag.append(shotNode(cut, index)));
  frag.append(filmNode());
  state.world.append(frag);
  placeAll();
  renderScenePicker();
  patch();
  if (fit) requestAnimationFrame(fitView);
  else if (!saved.view) requestAnimationFrame(startView);
  else applyView();
  if (state.selected && !nodeExists(state.selected)) state.selected = null;
  renderPanel();
}

const nodeExists = (key) => Boolean(state.world.querySelector(`[data-key="${CSS.escape(key)}"]`));

function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; }

function renderScenePicker() {
  const select = state.shell.querySelector('[data-kc-field="scene"]');
  const { scenes, active } = scene();
  const options = scenes.map((item, index) => `<option value="${esc(item.id)}" ${item.id === active.id ? 'selected' : ''}>${esc(item.title || `Сцена ${pad(index + 1)}`)}</option>`).join('');
  if (select.innerHTML !== options) select.innerHTML = options;
}

// The face/whole-character picture (or any picture) sets the mood of empty shot cards.
function moodPicture() {
  const pictures = scene().references.filter((ref) => ref.kind === 'image');
  return pictures.find((ref) => ['Лицо героя', 'Персонаж целиком'].includes(ref.role)) || pictures[0] || null;
}

function materialNode(ref) {
  const media = ref.kind === 'image'
    ? thumbImg(ref.id)
    : ref.kind === 'video' ? `<video src="${esc(studio().referenceUrl(ref.id))}#t=0.1" preload="metadata" muted playsinline></video><span class="kc-kind">▶</span>`
      : '<span class="kc-kind kc-kind-audio">♪</span>';
  return el(`<article class="kc-node kc-material" data-key="m:${esc(ref.id)}" style="width:${MATERIAL_W}px">
    <div class="kc-label"><span>${esc(ref.role || 'Материал')}</span>${ref.owner ? `<b>${esc(ref.owner)}</b>` : ''}</div>
    <div class="kc-card kc-material-card">${media}<span class="kc-port kc-out"></span></div>
  </article>`);
}

function addMaterialNode() {
  return el(`<article class="kc-node kc-material kc-material-add" data-key="m:+" style="width:${MATERIAL_W}px">
    <div class="kc-label"><span>Материалы сцены</span></div>
    <button type="button" class="kc-card kc-add-card" data-kc-act="add-material">
      <span class="kc-add-plus">＋</span><span>Герой, локация,<br>костюм или голос</span></button>
  </article>`);
}

function shotNode(cut, index) {
  const width = shotWidth(aspect());
  return el(`<article class="kc-node kc-shot" data-key="s:${esc(cut.id)}" data-cut="${esc(cut.id)}" style="width:${width}px">
    <div class="kc-label"><span class="kc-num">${pad(index + 1)}</span><span>Шот ${index + 1}</span><button type="button" class="kc-notes-chip" data-kc="notes" hidden></button><span class="kc-badge" data-kc="badge"></span></div>
    <div class="kc-card kc-media" data-aspect="${esc(aspect())}">
      <div class="kc-media-inner" data-kc="preview"></div>
      <div class="kc-chips"><span data-kc="dur"></span><span>${esc(aspect())}</span><span data-kc="link"></span><span data-kc="refs"></span><span data-kc="fantasy" class="is-fantasy" title="Шот в фантазии: получает мир фантазии, а не реальное место">✦ фантазия</span></div>
      <button type="button" class="kc-play" data-kc-act="run" data-cut="${esc(cut.id)}"></button>
      <span class="kc-port kc-in"></span><span class="kc-port kc-out"></span>
    </div>
    <div class="kc-takes" data-kc="takes"></div>
    <div class="kc-note" data-kc="text"></div>
    <div class="kc-auto" data-kc="auto" hidden></div>
  </article>`);
}

function filmNode() {
  return el(`<article class="kc-node kc-film" data-key="film" style="width:${FILM_W}px">
    <div class="kc-label"><span class="kc-num kc-num-film">▣</span><span>Фильм сцены</span></div>
    <div class="kc-card kc-media" data-aspect="${esc(aspect())}">
      <div class="kc-media-inner" data-kc="film-preview"></div>
      <span class="kc-port kc-in"></span>
    </div>
    <p class="kc-film-info" data-kc="film-info"></p>
    <button type="button" class="kc-accent kc-wide" data-kc-act="glue">Склеить</button>
  </article>`);
}

// --- in-place patch (every poll) ------------------------------------------------------------

function patch() {
  const { cuts } = scene();
  const plan = filmPlan(cuts, state.jobs);
  const total = cuts.reduce((sum, cut) => sum + (Number(cut.duration) || 0), 0);
  const summary = `${plural(cuts.length, 'шот', 'шота', 'шотов')} · ${total} с · готово ${plan.ready.length}/${cuts.length}${state.jobsLoaded ? '' : ' · очередь Mac недоступна'}`;
  setText(state.shell.querySelector('[data-kc="summary"]'), summary);
  const sceneSelect = state.shell.querySelector('.kc-top-left select');
  if (sceneSelect && sceneSelect.title !== summary) sceneSelect.title = summary;
  cuts.forEach((cut, index) => patchShot(cut, index, cuts));
  patchFilm(plan, cuts);
  state.overview?.render();
  drawEdges();
}

function setText(node, text) { if (node && node.textContent !== text) node.textContent = text; }

function patchShot(cut, index, cuts) {
  const node = state.world.querySelector(`[data-key="s:${CSS.escape(cut.id)}"]`);
  if (!node) return;
  const info = nodeState(cuts, index, state.jobs);
  node.dataset.status = info.status;
  node.classList.toggle('is-selected', state.selected === `s:${cut.id}`);
  setText(node.querySelector('[data-kc="badge"]'), info.status === 'running' && info.progress !== null ? `${info.progress}%` : STATUS_TEXT[info.status]);
  setText(node.querySelector('[data-kc="dur"]'), `${cut.duration}с`);
  const { references, places } = scene();
  const materials = canvasMaterials(references);
  const gets = shotMaterials(cut, materials, places);
  const refsChip = node.querySelector('[data-kc="refs"]');
  setText(refsChip, gets.error ? '⚠ материалы' : materials.length ? `◫ ${gets.ids.size}/${materials.length}` : '');
  refsChip.hidden = !refsChip.textContent;
  refsChip.classList.toggle('is-warn', Boolean(gets.error));
  if (refsChip.title !== (gets.error || 'Сколько материалов сцены уйдёт в этот шот')) refsChip.title = gets.error || 'Сколько материалов сцены уйдёт в этот шот';
  setText(node.querySelector('[data-kc="link"]'), cut.startFrame?.source === 'previous' ? '↳ продолжение'
    : cut.startFrame?.source === 'keyframe' ? '✨ ключевой кадр' : cut.startFrame?.asset_id ? '▣ свой кадр' : '');
  node.querySelector('[data-kc="link"]').hidden = !node.querySelector('[data-kc="link"]').textContent;
  node.querySelector('[data-kc="fantasy"]').hidden = !isFantasy(cut);
  const notesChip = node.querySelector('[data-kc="notes"]');
  const noteCount = (cut.notes || []).length;
  setText(notesChip, noteCount ? `💬 ${noteCount}` : '');
  notesChip.hidden = !noteCount;
  notesChip.title = noteCount ? cut.notes.at(-1).text : '';
  const note = node.querySelector('[data-kc="text"]');
  setText(note, cut.prompt?.trim() ? shotLabel(cut) : 'Опиши, что происходит в шоте…');
  note.classList.toggle('is-empty', !cut.prompt?.trim());
  const auto = node.querySelector('[data-kc="auto"]');
  setText(auto, autoNote(cut));
  auto.hidden = !auto.textContent;
  patchPreview(node.querySelector('[data-kc="preview"]'), cut, info);
  patchTakes(node.querySelector('[data-kc="takes"]'), cut);
  const play = node.querySelector('.kc-play');
  const busy = state.busy.has(cut.id) || info.status === 'queued' || info.status === 'running';
  play.disabled = busy;
  play.dataset.mode = busy ? 'busy' : info.status === 'idle' || info.status === 'failed' ? 'create' : 'again';
  setText(play, busy ? '' : info.status === 'idle' || info.status === 'failed' ? '▶  Создать' : '↻');
  play.title = info.status === 'idle' ? 'Создать первый дубль' : 'Создать ещё один дубль';
}

function patchPreview(box, cut, info) {
  const job = selectedJobId(cut);
  const done = info.status === 'done' || info.status === 'stale';
  const mood = moodPicture();
  const key = done ? `v:${job}` : info.status === 'running' ? `p:${info.jobId}` : cut.startFrame?.asset_id ? `f:${cut.startFrame.asset_id}` : `e:${info.status}:${mood?.id || ''}`;
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    if (done) box.innerHTML = `<video src="${esc(videoUrl(job))}#t=0.1" poster="/api/jobs/${encodeURIComponent(job)}/poster" preload="metadata" muted playsinline loop></video>`;
    else if (info.status === 'running') box.innerHTML = `${mood ? thumbImg(mood.id, 'class="kc-dim"') : ''}<img class="kc-live" alt="" draggable="false"><div class="kc-progress"><i></i></div>`;
    else if (cut.startFrame?.asset_id) box.innerHTML = `<img src="${esc(assetFile(cut.startFrame.asset_id))}" alt="" draggable="false">`;
    // No start frame yet: say so. A dimmed reference photo here read as a ready composition.
    else box.innerHTML = `<div class="kc-blank"></div>${info.status === 'idle' ? `<span class="kc-wait">${cut.startFrame?.source === 'keyframe' ? 'Опорного кадра нет' : 'Кадра нет'}</span>` : ''}${info.status === 'failed' ? '<span class="kc-fail">✕ Ошибка генерации</span>' : info.status === 'queued' ? '<span class="kc-wait">В очереди…</span>' : ''}`;
  }
  if (info.status === 'running') {
    const live = box.querySelector('.kc-live');
    live.onerror = () => { live.removeAttribute('src'); };
    live.src = `/api/jobs/${encodeURIComponent(info.jobId)}/preview?t=${Date.now()}`;
    box.querySelector('.kc-progress i').style.width = `${info.progress || 2}%`;
  }
}

function patchTakes(box, cut) {
  const takes = takeList(cut, state.jobs);
  const current = selectedJobId(cut);
  const key = JSON.stringify([takes.map((take) => [take.jobId, take.status]), current]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = takes.length > 0 ? takes.map((take, index) => `<button type="button" class="kc-take is-${take.status}${take.jobId === current ? ' is-current' : ''}"
    data-kc-act="take" data-cut="${esc(cut.id)}" data-job="${esc(take.jobId)}" title="Дубль ${index + 1}: ${STATUS_TEXT[take.status]}">${take.status === 'done' ? `<img src="/api/jobs/${encodeURIComponent(take.jobId)}/poster" alt="">` : take.status === 'failed' ? '✕' : '…'}</button>`).join('') : '';
}

function patchFilm(plan, cuts) {
  const node = state.world.querySelector('[data-key="film"]');
  if (!node) return;
  node.classList.toggle('is-selected', state.selected === 'film');
  setText(node.querySelector('[data-kc="film-info"]'), plan.ready.length
    ? `Готово ${plan.ready.length} из ${cuts.length} · ${plan.seconds} с`
    : 'Появится, когда будет готов хотя бы один шот');
  const button = node.querySelector('[data-kc-act="glue"]');
  button.disabled = state.filmBusy || !plan.ready.length;
  setText(button, state.filmBusy ? 'Склеиваю…' : plan.missing.length && plan.ready.length ? `Склеить готовые (${plan.ready.length})` : 'Склеить фильм');
  const film = filmResult();
  const box = node.querySelector('[data-kc="film-preview"]');
  const key = film?.url || '';
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = film ? `<video src="${esc(film.url)}#t=0.1" preload="metadata" muted playsinline></video>` : '<div class="kc-blank kc-film-blank"><span>▣</span></div>';
  }
}

function filmResult() {
  // lastFilm is the glue just made on this page; after a switch to another scene it belonged to the previous one
  // (one scene showed another scene's film). The scene's own storyboard holds its film.
  const own = state.lastFilm?.sceneId === sceneId() ? state.lastFilm : null;
  const film = own;
  return film?.url ? film : null;
}

// --- geometry --------------------------------------------------------------------------------

function placeAll() {
  state.world.querySelectorAll('.kc-node').forEach((node) => {
    const p = state.positions[node.dataset.key];
    if (p) node.style.transform = `translate(${p.x}px, ${p.y}px)`;
  });
}

function applyView() {
  const { x, y, zoom } = state.view;
  state.world.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
  setText(state.shell.querySelector('[data-kc="zoom"]'), `${Math.round(zoom * 100)}%`);
}

function sizes() {
  const out = {};
  state.world.querySelectorAll('.kc-node').forEach((node) => { out[node.dataset.key] = { w: node.offsetWidth, h: node.offsetHeight }; });
  return out;
}

// Room left for the floating bars and the open panel.
function usable() {
  const vw = state.viewport.clientWidth - (state.panel.hidden ? 0 : state.panel.offsetWidth + 24);
  const top = innerWidth <= 760 ? 116 : 70;
  return { x: 0, y: top, w: Math.max(200, vw), h: Math.max(200, state.viewport.clientHeight - top - 90) };
}

function fitView() {
  const box = bounds(state.positions, sizes());
  const area = usable();
  const zoom = Math.max(0.15, Math.min(1, (area.w - 80) / box.w, (area.h - 40) / box.h));
  state.view = { zoom, x: area.x + (area.w - box.w * zoom) / 2 - box.x * zoom, y: area.y + (area.h - box.h * zoom) / 2 - box.y * zoom };
  applyView(); saveView();
}

// First open: readable cards, the strip starts at the left, vertically centred in the free area.
function startView() {
  const box = bounds(state.positions, sizes());
  const area = usable();
  const zoom = Math.max(0.3, Math.min(0.85, (area.h - 40) / box.h));
  state.view = { zoom, x: 60 - box.x * zoom, y: area.y + (area.h - box.h * zoom) / 2 - box.y * zoom };
  applyView(); saveView();
}

function zoomAt(factor, cx, cy) {
  const { x, y, zoom } = state.view;
  const next = Math.max(0.15, Math.min(1.6, zoom * factor));
  state.view = { zoom: next, x: cx - ((cx - x) * next) / zoom, y: cy - ((cy - y) * next) / zoom };
  applyView(); saveView();
}

function portPoint(key, side) {
  if (!key) return null;
  const node = state.world.querySelector(`[data-key="${CSS.escape(key)}"]`);
  const card = node?.querySelector('.kc-card');
  const p = state.positions[key];
  if (!node || !card || !p) return null;
  return { x: p.x + (side === 'out' ? node.offsetWidth : 0), y: p.y + card.offsetTop + card.offsetHeight * PORT_Y };
}

function curve(a, b) {
  const dx = Math.max(60, Math.abs(b.x - a.x) / 2);
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

function drawEdges() {
  const { cuts, references, places } = scene();
  const paths = [];
  const shots = cuts.map((cut) => `s:${cut.id}`);
  // A link only where the material really goes into that shot (the same pass as the render), coloured by role.
  const materials = canvasMaterials(references);
  cuts.forEach((cut, index) => {
    const b = portPoint(shots[index], 'in');
    const gets = shotMaterials(cut, materials, places).ids;
    for (const ref of materials) {
      const a = portPoint(`m:${ref.id}`, 'out');
      if (a && b && gets.has(ref.id)) paths.push(`<path class="kc-edge-ref is-${materialGroup(ref)}" data-cut="${esc(cut.id)}" data-ref="${esc(ref.id)}" d="${curve(a, b)}"/>`);
    }
  });
  const chips = [];
  cuts.forEach((cut, index) => {
    if (!index) return;
    const a = portPoint(shots[index - 1], 'out');
    const b = portPoint(shots[index], 'in');
    if (!a || !b) return;
    const continues = cut.startFrame?.source === 'previous';
    paths.push(`<path class="kc-edge-shot${continues ? ' is-continue' : ''}" d="${curve(a, b)}"/>`);
    chips.push({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, cut: cut.id, continues });
  });
  const last = portPoint(shots.at(-1), 'out'); const film = portPoint('film', 'in');
  if (last && film) paths.push(`<path class="kc-edge-film" d="${curve(last, film)}"/>`);
  const box = bounds(state.positions, sizes());
  const svg = state.edges;
  svg.setAttribute('width', box.x + box.w + 400); svg.setAttribute('height', box.y + box.h + 400);
  const markup = paths.join('');
  if (svg.innerHTML !== markup) svg.innerHTML = markup;
  const chipKey = JSON.stringify(chips);
  if (state.chipKey === chipKey) return;
  state.chipKey = chipKey;
  state.world.querySelectorAll('.kc-link').forEach((chip) => chip.remove());
  for (const chip of chips) {
    state.world.append(el(`<button type="button" class="kc-link${chip.continues ? ' is-continue' : ''}" data-kc-act="toggle-link" data-cut="${esc(chip.cut)}"
      title="${chip.continues ? 'Продолжение: шот начнётся с последнего кадра предыдущего. Нажми — сделать новым планом' : 'Новый план (склейка). Нажми — продолжить с последнего кадра предыдущего'}"
      style="transform:translate(${chip.x - 16}px, ${chip.y - 16}px)">${chip.continues ? '↳' : '✂'}</button>`));
  }
}

// --- pan / zoom / drag -----------------------------------------------------------------------

function installPanZoom() {
  const vp = state.viewport;
  vp.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = vp.getBoundingClientRect();
    // Mouse wheel and pinch zoom (like the reference canvas); a trackpad two-finger swipe pans.
    const trackpadPan = !event.ctrlKey && Math.abs(event.deltaX) > 0 && event.deltaMode === 0 && Math.abs(event.deltaY) < 40;
    if (trackpadPan) { state.view.x -= event.deltaX; state.view.y -= event.deltaY; applyView(); saveView(); return; }
    zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0018)), event.clientX - rect.left, event.clientY - rect.top);
  }, { passive: false });
  vp.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('button, textarea, input, select')) return;
    const node = event.target.closest('.kc-node');
    const start = { x: event.clientX, y: event.clientY };
    let moved = false;
    if (node) {
      const key = node.dataset.key; const origin = { ...state.positions[key] };
      vp.setPointerCapture(event.pointerId);
      const move = (e) => {
        const dx = (e.clientX - start.x) / state.view.zoom; const dy = (e.clientY - start.y) / state.view.zoom;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        if (!moved) return;
        state.positions[key] = { x: Math.round(origin.x + dx), y: Math.round(origin.y + dy) };
        node.style.transform = `translate(${state.positions[key].x}px, ${state.positions[key].y}px)`;
        drawEdges();
      };
      const up = () => { vp.removeEventListener('pointermove', move); vp.removeEventListener('pointerup', up); if (moved) { state.pinned[key] = state.positions[key]; saveView(); } else select(key); };
      vp.addEventListener('pointermove', move); vp.addEventListener('pointerup', up);
      return;
    }
    const origin = { ...state.view };
    vp.setPointerCapture(event.pointerId);
    vp.classList.add('is-panning');
    const move = (e) => { state.view = { ...origin, x: origin.x + e.clientX - start.x, y: origin.y + e.clientY - start.y }; moved = true; applyView(); };
    const up = () => { vp.classList.remove('is-panning'); vp.removeEventListener('pointermove', move); vp.removeEventListener('pointerup', up); if (moved) saveView(); else select(null); };
    vp.addEventListener('pointermove', move); vp.addEventListener('pointerup', up);
  });
  // Hovering a finished clip plays it, like the reference board.
  vp.addEventListener('pointerover', (event) => { const v = event.target.closest('.kc-media video'); if (v) void v.play().catch(() => {}); });
  vp.addEventListener('pointerout', (event) => { const v = event.target.closest('.kc-media video'); if (v) v.pause(); });
}

// A node placed by hand (double-click on the board) stays where it was put, like a dragged one.
function pin(key, point) {
  state.pinned[key] = point; state.positions[key] = point;
  saveView(); placeAll(); drawEdges();
}

// Brings a node into view without changing the zoom (keyboard ← →).
function reveal(key) {
  const p = state.positions[key]; const node = state.world.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (!p || !node) return;
  const { zoom } = state.view; const area = usable();
  const left = state.view.x + p.x * zoom; const right = left + node.offsetWidth * zoom;
  if (left >= 20 && right <= area.w - 20) return;
  state.view = { ...state.view, x: area.w / 2 - (p.x + node.offsetWidth / 2) * zoom };
  applyView(); saveView();
}

// Runs one of the canvas's own actions (onClick) from the board tools: one path for every change.
function act(name, data = {}) {
  const button = document.createElement('button');
  button.type = 'button'; button.hidden = true; button.dataset.kcAct = name;
  Object.assign(button.dataset, data);
  state.shell.append(button); button.click(); button.remove();
}

// --- selection + panel -----------------------------------------------------------------------

function select(key) {
  if (key) toggleDirector(false);
  if (key === 'm:+') key = 'scene';
  state.selected = key;
  if (key?.startsWith('s:')) {
    const id = key.slice(2);
    studio().selectCut(id);
    storyboardAction('select', id); // keeps the director inspector on this shot
  }
  state.world.querySelectorAll('.kc-node').forEach((node) => node.classList.toggle('is-selected', node.dataset.key === key));
  renderPanel();
}

function renderPanel() {
  const panel = state.panel;
  const key = state.selected;
  parkInspector();
  if (!key) { panel.hidden = true; panel.replaceChildren(); return; }
  panel.hidden = false;
  if (key === 'film') filmPanel(panel);
  else if (key === 'edit') editPanel(panel);
  else if (key === 'scene') scenePanel(panel);
  else if (key.startsWith('m:')) materialPanel(panel, key.slice(2));
  else shotPanel(panel, key.slice(2));
}

const closeButton = '<button type="button" class="kc-close" data-kc-act="close" aria-label="Закрыть">×</button>';

const LIBRARY_ADD = [['characters', 'Персонаж'], ['locations', 'Локация'], ['clothes', 'Одежда'], ['props', 'Предмет'], ['motion', 'Движение'], ['styles', 'Стиль, свет'], ['other', 'Звук, другое']];

// Director presets of the active shot, read from the Create strip (same data, same dialog).
function directorChips() {
  return [...document.querySelectorAll('#directorStrip [data-category]')].map((button) => {
    const [title, rest = ''] = (button.getAttribute('aria-label') || '').split(':');
    return { key: button.dataset.category, title: title.trim(), value: rest.replace(/\.\s*Открыть библиотеку\.?$/, '').trim(), image: button.querySelector('img')?.getAttribute('src') || '' };
  });
}

function startFrameMode(cut, index) {
  const source = cut.startFrame?.source;
  if (source === 'previous' && index) return 'previous';
  if (source === 'library' || source === 'keyframe' || cut.startFrame?.asset_id) return 'frame';
  return 'none';
}

function shotPanel(panel, id) {
  const { cuts, references, places } = scene();
  const index = cutIndex(id);
  const cut = cuts[index];
  if (!cut) { state.selected = null; panel.hidden = true; return; }
  const info = nodeState(cuts, index, state.jobs);
  const job = selectedJobId(cut);
  const done = info.status === 'done' || info.status === 'stale';
  const mode = startFrameMode(cut, index);
  const busy = state.busy.has(id) || info.status === 'queued' || info.status === 'running';
  const roles = studio().referenceRoles();
  const linkPrevious = storyboardState(studio().project()).linkPrevious;
  const linkNote = !index ? ''
    : mode === 'previous' ? `Начнётся ровно с последнего кадра шота ${index} — шов не виден, композиция и окружение те же.`
    : linkPrevious ? `Новый ракурс, но локация, свет и реквизит возьмутся из последнего кадра шота ${index} (референс «Окружение»).`
    : 'Шот не связан с предыдущим: возможен разнобой локации и света.';
  const materials = canvasMaterials(references);
  const gets = shotMaterials(cut, materials, places);
  const placeWarning = placeNote(cut.prompt || '', cut.sceneShot?.location, places);
  // Per shot: on / switched off by the user / left out automatically (a hero who is not in this shot).
  const refUse = (ref) => {
    if (gets.off.has(ref.id)) return `<button type="button" class="kc-ref-use is-off" data-kc-act="ref-shot-on" data-cut="${esc(id)}" data-ref="${esc(ref.id)}" title="Выключен для этого шота. Нажми — вернуть">не в шоте</button>`;
    if (gets.place?.has(ref.id)) return '<span class="kc-ref-use is-auto" title="Картинка другого места паспорта: шот происходит не там, она не уйдёт в H3">другое место</span>';
    if (!gets.ids.has(ref.id)) return '<span class="kc-ref-use is-auto" title="Героя нет в тексте и составе этого шота — его фото не уйдёт в H3">нет в тексте</span>';
    return `<button type="button" class="kc-ref-use" data-kc-act="ref-shot-off" data-cut="${esc(id)}" data-ref="${esc(ref.id)}" title="Уходит в этот шот. Нажми — выключить только для него">в шоте</button>`;
  };
  const refRow = (ref) => `<li class="kc-ref${gets.ids.has(ref.id) ? '' : ' is-out'}">
      <span class="kc-ref-thumb">${ref.kind === 'image' ? thumbImg(ref.id) : ref.kind === 'video' ? '▶' : '♪'}</span>
      <span class="kc-ref-fields">
        <select data-kc-field="role" data-ref="${esc(ref.id)}" aria-label="Роль">${(roles[ref.kind] || []).map((role) => `<option ${role === ref.role ? 'selected' : ''}>${esc(role)}</option>`).join('')}</select>
        <input data-kc-field="owner" data-ref="${esc(ref.id)}" value="${esc(ref.owner || '')}" maxlength="80" placeholder="кто это — например HERO" aria-label="Кому принадлежит">
      </span>
      ${refUse(ref)}
      <button type="button" class="kc-icon" data-kc-act="ref-remove" data-ref="${esc(ref.id)}" title="Убрать из сцены" aria-label="Убрать из сцены">×</button></li>`;
  // A generated keyframe is a proposal: the user accepts it (composition, space, faces, contacts) before video.
  const accepted = window.h3Storyboard?.frameAccepted?.(id);
  const frameReview = cut.startFrame?.source === 'keyframe' && cut.startFrame?.asset_id
    ? `<p class="kc-note-text">${accepted ? '✓ Кадр принят — видео шота снимается от него.'
      : 'Проверь композицию, пространство, лица, анатомию и контакты. Видео шота снимается только от принятого кадра.'}</p>
      <button type="button" class="${accepted ? '' : 'kc-accent'}" data-kc-act="${accepted ? 'frame-unaccept' : 'frame-accept'}" data-cut="${esc(id)}">${accepted ? 'Снять принятие' : '✓ Принять кадр'}</button>`
    : '';
  const staging = cut.productionSpec ? `Постановка и поведение · v${esc(cut.productionSpec.shot?.revision ?? '')}` : 'Создать постановку';
  panel.innerHTML = `${closeButton}
    <header class="kc-panel-head"><span class="kc-num">${pad(index + 1)}</span><h3>Шот ${index + 1}</h3><span class="kc-badge" data-status="${info.status}">${STATUS_TEXT[info.status]}</span></header>
    <div class="kc-panel-video" data-aspect="${esc(aspect())}">${done ? `<video src="${esc(videoUrl(job))}" poster="/api/jobs/${encodeURIComponent(job)}/poster" controls playsinline preload="metadata"></video>` : cut.startFrame?.asset_id ? `<img src="${esc(assetFile(cut.startFrame.asset_id))}" alt="">` : '<span class="kc-empty">Видео появится после генерации</span>'}</div>
    ${info.status === 'failed' && info.error ? `<p class="kc-error">${esc(info.error)}</p>` : ''}
    <div class="kc-takes kc-takes-panel" data-kc="takes"></div>
    <div class="kc-prompt-box">
      <textarea data-kc-field="prompt" data-cut="${esc(id)}" rows="${isTechnicalPrompt(cut.prompt) ? 10 : 5}" placeholder="Что происходит в шоте? Например: герой входит в комнату, замечает чашку на полу и замирает">${esc(cut.prompt || '')}</textarea>
      <div class="kc-prompt-tools">
        <label class="kc-pill">⏱ <input type="number" min="${MIN_SECONDS}" max="${MAX_SECONDS}" step="1" value="${Number(cut.duration) || 5}" data-kc-field="duration" data-cut="${esc(id)}" aria-label="Длительность, секунд"> с</label>
        <span class="kc-pill">${esc(aspect())}</span>
        <span class="kc-pill">MiniMax H3</span>
      </div>
    </div>
    <p class="kc-msg" data-kc="msg"></p>
    <button type="button" class="kc-accent kc-big" data-kc-act="run" data-cut="${esc(id)}" ${busy ? 'disabled' : ''}>${busy ? 'Генерируется…' : info.status === 'idle' ? '▶ Создать дубль' : '↻ Ещё дубль'}</button>

    <section class="kc-sec"><h4>Начало шота</h4>
      <div class="kc-seg kc-seg-3" role="group" aria-label="Начало шота">
        <button type="button" class="${mode === 'none' ? 'is-on' : ''}" data-kc-act="start-none" data-cut="${esc(id)}">✂ Новый план</button>
        <button type="button" class="${mode === 'previous' ? 'is-on' : ''}" data-kc-act="link-on" data-cut="${esc(id)}" ${index ? '' : 'disabled title="У первого шота нет предыдущего"'}>↳ С конца предыдущего</button>
        <button type="button" class="${mode === 'frame' ? 'is-on' : ''}" data-kc-act="start-frame" data-cut="${esc(id)}">▣ Свой кадр</button>
      </div>
      ${mode === 'frame' ? `<div class="kc-frame">
        <span class="kc-frame-thumb">${cut.startFrame?.asset_id ? `<img src="${esc(assetFile(cut.startFrame.asset_id))}" alt="">` : 'нет кадра'}</span>
        <div class="kc-frame-actions">
          <button type="button" data-kc-act="frame-upload" data-cut="${esc(id)}">Загрузить</button>
          <button type="button" data-kc-act="frame-library" data-cut="${esc(id)}">Из медиатеки</button>
          <button type="button" data-kc-act="frame-keyframe" data-cut="${esc(id)}" title="Нарисовать стартовый кадр по описанию шота и материалам (нужен GPU)">✨ Ключевой кадр</button>
        </div></div>${frameReview}` : ''}
      ${linkNote ? `<p class="kc-note-text">${esc(linkNote)}</p>` : ''}
      ${index && mode !== 'previous' ? `<label class="kc-note-text"><input type="checkbox" data-kc-act="link-env" ${linkPrevious ? 'checked' : ''}> Склейки берут окружение из предыдущего шота (для всей сцены)</label>` : ''}
    </section>

    <section class="kc-sec"><h4>Постановка</h4>
      <p class="kc-note-text">Начальный кадр, карта пространства, поведение героев и визуальный профиль — по ним рисуется опорный кадр.</p>
      <button type="button" class="kc-wide" data-kc-act="staging" data-cut="${esc(id)}">${staging}</button>
    </section>

    <section class="kc-sec"><h4>Превиз · Blockout</h4>
      <p class="kc-note-text">Поставь шот в Blockout: герои в настоящем масштабе, метки, объектив, камера. Кадр станет «Композицией» только этого шота — серым наброском, по которому H3 и опорный кадр расставят фигуры; движущаяся камера — видео «Камера» на всю длину шота.</p>
      <button type="button" class="kc-wide" data-kc-act="previs-frame" data-cut="${esc(id)}">◫ Взять кадр из Blockout</button>
      <button type="button" class="kc-wide" data-kc-act="previs-motion" data-cut="${esc(id)}" title="Камера шота в Blockout движется (2+ метки камеры): её путь станет видео «Камера» — H3 возьмёт только кадрирование, движение и глубину">▶ Видео камеры из Blockout</button>
    </section>

    <section class="kc-sec"><h4>Режиссура шота</h4>
      <div class="kc-dir-grid">${directorChips().map((chip) => `<button type="button" class="kc-dir" data-kc-act="director-pick" data-cut="${esc(id)}" data-category="${esc(chip.key)}">
        ${chip.image ? `<img src="${esc(chip.image)}" alt="">` : '<i></i>'}<span><small>${esc(chip.title)}</small>${esc(chip.value || 'Авто')}</span></button>`).join('')}</div>
    </section>

    <section class="kc-sec"><h4>Материалы шота <small>${gets.ids.size} из ${materials.length} уходят в H3</small></h4>
      ${placeWarning ? `<p class="kc-note-text kc-warn">${esc(placeWarning)}</p>` : ''}
      ${gets.error ? `<p class="kc-note-text kc-warn">⚠ ${esc(gets.error)}</p>` : ''}
      <ul class="kc-refs">${references.map(refRow).join('') || '<li class="kc-note-text">Пока нет — добавь героя, локацию, одежду, предметы…</li>'}</ul>
      <div class="kc-add-row">${LIBRARY_ADD.map(([type, label]) => `<button type="button" data-kc-act="library-type" data-type="${type}">＋ ${esc(label)}</button>`).join('')}</div>
    </section>

    ${done ? `<section class="kc-sec"><h4>AI-проверка</h4><p class="kc-note-text">${cut.shotQc ? esc(cut.shotQc.decision === 'accept' ? 'Проверка пройдена' : cut.shotQc.finding || '') : 'Сверит готовый ролик с описанием шота.'}</p>
      <button type="button" class="kc-wide" data-kc-act="qc" data-cut="${esc(id)}">◇ Проверить шот</button>
      ${acceptedShot(cut) ? '<p class="kc-note-text">Дубль принят для сборки.</p>' : `<button type="button" class="kc-wide" data-kc-act="qc-accept" data-cut="${esc(id)}">Принять после просмотра</button>`}
      <button type="button" class="kc-wide" data-kc-act="qc-reject" data-cut="${esc(id)}">Отклонить дубль</button></section>` : ''}

    <div class="kc-row-small">
      <button type="button" data-kc-act="move-left" data-cut="${esc(id)}" ${index ? '' : 'disabled'}>← Раньше</button>
      <button type="button" data-kc-act="move-right" data-cut="${esc(id)}" ${index < cuts.length - 1 ? '' : 'disabled'}>Позже →</button>
      <button type="button" class="kc-danger" data-kc-act="remove" data-cut="${esc(id)}" ${cuts.length > 1 ? '' : 'disabled'}>Удалить</button>
    </div>`;
  patchTakes(panel.querySelector('[data-kc="takes"]'), cut);
}

// Existing storyboard blocks (shot inspector, scene materials, colour settings) are moved into the
// panel while it is open — the same elements with all their functions, never copies — and put
// back on close. The panel lives inside #storyboard, so the storyboard's delegated listeners still fire.
const borrowed = new Map();
function borrow(node, slot) {
  if (!node || !slot) return;
  if (!borrowed.has(node)) borrowed.set(node, { parent: node.parentNode, next: node.nextSibling });
  slot.append(node);
}
function parkInspector() {
  for (const [node, home] of borrowed) {
    if (!state.panel.contains(node)) continue;
    home.parent.insertBefore(node, home.next && home.next.parentNode === home.parent ? home.next : null);
  }
}

function scenePanel(panel) {
  const { active } = scene();
  panel.innerHTML = `${closeButton}
    <header class="kc-panel-head"><span class="kc-num">⚙</span><h3>${esc(active.title || 'Сцена')}: материалы и настройки</h3></header>
    <p class="kc-note-text">Материалы получает каждый шот. Укажи роль и кому принадлежит (герой, костюм, голос) — так H3 не перепутает, кто есть кто.</p>
    <div class="kc-borrow" data-kc="setup"></div>
    <div class="kc-borrow" data-kc="colour"></div>`;
  borrow(document.getElementById('scene-setup'), panel.querySelector('[data-kc="setup"]'));
  borrow(document.querySelector('#scene-assembly .scene-export-settings'), panel.querySelector('[data-kc="colour"]'));
  window.h3Storyboard?.refresh();
}

function materialPanel(panel, id) {
  const ref = scene().references.find((item) => item.id === id);
  if (!ref) { state.selected = null; panel.hidden = true; return; }
  const roles = studio().referenceRoles()[ref.kind] || [];
  panel.innerHTML = `${closeButton}
    <header class="kc-panel-head"><h3>Материал сцены</h3></header>
    <div class="kc-panel-video kc-panel-material">${ref.kind === 'image' ? `<img src="${esc(studio().referenceUrl(ref.id))}" alt="">`
      : ref.kind === 'video' ? `<video src="${esc(studio().referenceUrl(ref.id))}" controls playsinline preload="metadata"></video>`
        : `<audio src="${esc(studio().referenceUrl(ref.id))}" controls></audio>`}</div>
    <p class="kc-note-text">${esc(ref.name || '')}</p>
    <label class="kc-field">Роль в сцене
      <select data-kc-field="role" data-ref="${esc(id)}">${roles.map((role) => `<option ${role === ref.role ? 'selected' : ''}>${esc(role)}</option>`).join('')}</select></label>
    <label class="kc-field">Кто это (имя героя)
      <input data-kc-field="owner" data-ref="${esc(id)}" value="${esc(ref.owner || '')}" maxlength="80" placeholder="Например: HERO"></label>
    <p class="kc-note-text">Материал получает каждый шот сцены — это линии на холсте.</p>
    <button type="button" class="kc-wide" data-kc-act="scene-settings">Все материалы сцены →</button>
    <div class="kc-row-small">
      <button type="button" data-kc-act="ref-up" data-ref="${esc(id)}">↑ Выше</button>
      <button type="button" data-kc-act="ref-down" data-ref="${esc(id)}">↓ Ниже</button>
      <button type="button" class="kc-danger" data-kc-act="ref-remove" data-ref="${esc(id)}">Убрать</button>
    </div>`;
}

function filmPanel(panel) {
  const { cuts } = scene();
  const plan = filmPlan(cuts, state.jobs);
  const film = filmResult();
  panel.innerHTML = `${closeButton}
    <header class="kc-panel-head"><span class="kc-num kc-num-film">▣</span><h3>Фильм сцены</h3></header>
    <div class="kc-panel-video" data-aspect="${esc(aspect())}">${film ? `<video src="${esc(film.url)}" controls playsinline preload="metadata"></video>` : '<span class="kc-empty">Ещё не склеено</span>'}</div>
    ${film ? `<p class="kc-note-text">${Number(film.duration || 0).toFixed(1)} с · ${film.mode === 'copy' ? 'без перекодирования' : 'с перекодированием'} · <a href="${esc(film.url)}?download=true">Скачать MP4</a></p>` : ''}
    <ol class="kc-film-list">${cuts.map((cut, index) => {
      const ok = plan.ready.some((item) => item.index === index);
      return `<li class="${ok ? 'is-ok' : ''}">Шот ${index + 1} — ${ok ? `готов, ${cut.duration} с` : STATUS_TEXT[nodeState(cuts, index, state.jobs).status].toLowerCase()}</li>`;
    }).join('')}</ol>
    <p class="kc-msg" data-kc="msg"></p>
    <button type="button" class="kc-accent kc-big" data-kc-act="glue" ${plan.ready.length ? '' : 'disabled'}>${plan.missing.length && plan.ready.length ? `Склеить готовые (${plan.ready.length} из ${cuts.length})` : 'Склеить фильм'}</button>`;
}

// «Весь фильм»: the edit sheet (edit-sheet.mjs, lives in #film-assembly) is borrowed into the panel.
function editPanel(panel) {
  panel.innerHTML = `${closeButton}
    <header class="kc-panel-head"><span class="kc-num kc-num-film">▣</span><h3>Монтаж всего фильма</h3></header>
    <p class="kc-note-text">Все сцены проекта по порядку, выбранные дубли. Выбери переход на каждом стыке, музыку и затемнения, проверь монтаж и собери MP4.</p>
    <div class="kc-borrow" data-kc="edit-sheet"></div>
    <button type="button" class="kc-wide" data-kc-act="whole-film-quick">Быстро склеить без переходов</button>`;
  borrow(document.querySelector('#film-assembly .edit-sheet'), panel.querySelector('[data-kc="edit-sheet"]'));
  window.h3Storyboard?.refresh();
}

function panelMessage(text) {
  const msg = state.panel.querySelector('[data-kc="msg"]');
  if (msg && !state.panel.hidden) msg.textContent = text;
  toast(text);
}

// --- AI Director popover (the existing scene-director block, moved, not copied) -------------

function toggleDirector(force) {
  const pop = state.shell.querySelector('[data-kc="director-pop"]');
  const open = force ?? pop.hidden;
  const block = document.getElementById('scene-idea');
  if (open) select(null);
  if (open && block && block.parentNode !== pop) { state.directorHome ||= block.parentNode; pop.append(block); }
  if (!open && block && state.directorHome && block.parentNode === pop) state.directorHome.prepend(block);
  pop.hidden = !open;
  state.shell.querySelector('[data-kc-act="director"]').classList.toggle('is-on', open);
  if (open) block?.querySelector('input, textarea')?.focus();
}

// --- actions ---------------------------------------------------------------------------------

// Storyboard actions run through its own click delegation (start frames, colour, queue, confirms).
function storyboardAction(action, id) {
  const root = document.getElementById('storyboard');
  if (!root) return false;
  const button = document.createElement('button');
  button.type = 'button'; button.hidden = true; button.dataset.action = action; button.dataset.id = id;
  root.append(button); button.click(); button.remove();
  return true;
}

function editCut(id, change) {
  studio().editCuts((list) => list.map((cut) => (cut.id === id ? change(cut) : cut)));
}

// «Превиз» (local_studio/previs.py): the frame staged in Blockout becomes this shot's «Композиция», the moving camera
// its «Камера» video; every other shot has it switched off (offRefs), and this shot's earlier previs of that role leaves it.
const PREVIS = {
  frame: { url: '/api/previs/snapshot', role: 'Композиция', done: 'Кадр превиза стал «Композицией»' },
  motion: { url: '/api/previs/motion', role: 'Камера', done: 'Видео камеры из превиза стало «Камерой»' },
};
async function previsMaterial(id, kind) {
  const s = studio();
  const index = cutIndex(id);
  const { url, role, done } = PREVIS[kind];
  state.busy.add(id); patch();
  try {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `${scene().active.title || 'scene'} shot ${index + 1}`.slice(0, 80) }) });
    const asset = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof asset.detail === 'string' ? asset.detail : `HTTP ${response.status}`);
    const earlier = scene().references.filter((ref) => ref.role === role && String(ref.name || '').startsWith('previs-'))
      .map((ref) => ref.id);
    s.addReferenceAsset(asset, role);
    s.editCuts((list) => list.map((cut) => ({ ...cut, offRefs: [...new Set([...(cut.offRefs || []),
      ...(cut.id === id ? earlier : [asset.asset_id])])].filter((ref) => cut.id !== id || ref !== asset.asset_id) })));
    void s.saveDraft(); sync(); renderPanel();
    toast(`${done} шота ${index + 1}`);
  } catch (error) { toast(`Превиз: ${error.message}`); }
  finally { state.busy.delete(id); patch(); }
}

async function runShot(id) {
  if (state.busy.has(id)) return;
  await refreshJobs();
  const { cuts } = scene();
  const index = cutIndex(id);
  const plan = shotPlan(cuts, index, state.jobs, storyboardState(studio().project()));
  if (!plan.ok) { select(`s:${id}`); panelMessage(plan.message); state.panel.querySelector('textarea')?.focus(); return; }
  const before = cuts[index].shotJobId;
  state.busy.add(id); patch();
  storyboardAction(nodeState(cuts, index, state.jobs).status === 'idle' ? 'run' : 'rerun', id);
  // The storyboard answers asynchronously; free the button once the new job shows up (or after 20 s).
  const started = Date.now();
  const wait = setInterval(() => {
    const now = scene().cuts.find((cut) => cut.id === id);
    if (now?.shotJobId !== before || Date.now() - started > 20000) {
      clearInterval(wait); state.busy.delete(id);
      if (now?.shotJobId === before) {
        const status = document.querySelector('#storyboard .storyboard-status')?.textContent || '';
        toast(status ? `Шот ${index + 1}: ${status}` : `Шот ${index + 1} не отправлен`);
      } else toast(`Шот ${index + 1} в очереди`);
      void refreshJobs().then(() => { patch(); if (state.selected === `s:${id}`) renderPanel(); });
    }
  }, 500);
}

async function glue() {
  if (state.filmBusy) return;
  await refreshJobs();
  const { cuts } = scene();
  const plan = filmPlan(cuts, state.jobs);
  if (plan.missing.length || !plan.ids.length) {
    panelMessage(`Склейка остановлена: проверь и прими каждый шот (${plan.missing.map((i) => i + 1).join(', ')}).`);
    return;
  }
  state.filmBusy = true; patch(); panelMessage('Склеиваю…');
  try {
    const response = await fetch('/api/storyboard/concat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_ids: plan.ids }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join('; ') : `Ошибка ${response.status}`);
    state.lastFilm = { ...body, sceneId: sceneId() };
    const project = studio().project();
    project.storyboard = { ...(project.storyboard || {}), lastConcat: body };
    await studio().saveDraft().catch(() => {});
    toast(`Склеено: ${Number(body.duration).toFixed(1)} с`);
    state.selected = 'film';
  } catch (error) {
    panelMessage(`Не удалось склеить: ${error.message}`);
  } finally { state.filmBusy = false; patch(); renderPanel(); }
}

// «Весь фильм»: the existing film assembly (all scenes, selected takes); its status is shown here.
function wholeFilm() {
  const button = document.querySelector('#film-assembly .storyboard-primary');
  if (!button) { toast('Сборка фильма недоступна'); return; }
  button.click();
  const started = Date.now();
  const watch = setInterval(() => {
    const status = document.querySelector('#film-assembly .film-status')?.textContent?.trim() || '';
    const issues = document.querySelector('#film-assembly .film-issues')?.textContent?.trim() || '';
    const link = document.querySelector('#film-assembly .film-final video, #film-assembly .film-final a');
    if ((status && !/Собираем/.test(status)) || Date.now() - started > 120000) {
      clearInterval(watch);
      toast(status || issues || 'Фильм собран', 12000);
      if (link) window.open(link.src || link.href, '_blank');
    }
  }, 700);
}

function onClick(event) {
  const button = event.target.closest('[data-kc-act]');
  if (!button) return;
  event.stopPropagation();
  const { kcAct: act, cut: id, ref, job } = button.dataset;
  const s = studio();
  switch (act) {
    case 'overview': state.overview?.open(); return;
    case 'add-shot': { const cut = s.addShot(); sync(); select(`s:${cut.id}`); state.panel.querySelector('textarea')?.focus(); return; }
    case 'add-material': select('scene'); return;
    case 'scene-settings': select(state.selected === 'scene' ? null : 'scene'); return;
    case 'chain': document.querySelector('#scene-assembly .storyboard-primary')?.click(); toast('Сборка сцены запущена — шоты пойдут по порядку'); return;
    case 'zoom-in': zoomAt(1.2, state.viewport.clientWidth / 2, state.viewport.clientHeight / 2); return;
    case 'zoom-out': zoomAt(1 / 1.2, state.viewport.clientWidth / 2, state.viewport.clientHeight / 2); return;
    case 'fit': fitView(); return;
    case 'relayout': try { localStorage.removeItem(storeKey()); } catch { /* ignore */ } state.key = ''; render({ fit: true }); return;
    case 'close': select(null); return;
    case 'director': toggleDirector(); return;
    case 'whole-film': select(state.selected === 'edit' ? null : 'edit'); return;
    case 'whole-film-quick': wholeFilm(); return;
    case 'new-scene': s.createScene(); state.selected = null; state.key = ''; sync(); return;
    case 'reset-scene': {
      const { active } = scene();
      if (!window.confirm(`Сбросить «${active.title || 'сцену'}» полностью? Останется один пустой шот: шоты, материалы, стиль, раскадровка и план режиссёра будут убраны. Готовые ролики останутся в очереди и библиотеке.`)) return;
      if (s.resetScene()) { state.selected = null; state.key = ''; sync(); toast('Сцена сброшена'); }
      return;
    }
    case 'delete-scene': {
      const { active, scenes } = scene();
      if (scenes.length < 2) { // the only scene cannot go away: clearing it is what the user wants
        if (window.confirm('Это единственная сцена — удалить её нельзя. Сбросить её полностью?') && s.resetScene()) {
          state.selected = null; state.key = ''; sync(); toast('Сцена сброшена');
        }
        return;
      }
      if (!window.confirm(`Удалить «${active.title || 'сцену'}» со всеми шотами? Готовые ролики останутся в очереди и библиотеке.`)) return;
      if (s.deleteScene(active.id)) { state.selected = null; state.key = ''; sync(); toast('Сцена удалена'); }
      return;
    }
    case 'rename-scene': {
      const { active } = scene();
      const title = window.prompt('Название сцены', active.title || '');
      if (title?.trim()) { s.renameScene(active.id, title.trim()); renderScenePicker(); }
      return;
    }
    case 'previs-frame': void previsMaterial(id, 'frame'); return;
    case 'previs-motion': void previsMaterial(id, 'motion'); return;
    case 'run': void runShot(id); return;
    case 'take':
      editCut(id, (cut) => ({ ...cut, shotJobId: job, selectedTakeId: job, shotQc: null, shotQcPending: false, shotQcError: '' }));
      void s.saveDraft(); patch(); if (state.selected === `s:${id}`) renderPanel(); return;
    case 'ref-shot-off': case 'ref-shot-on': {
      editCut(id, (cut) => {
        const off = new Set(cut.offRefs || []);
        if (act === 'ref-shot-off') off.add(ref); else off.delete(ref);
        return { ...cut, offRefs: [...off] };
      });
      void s.saveDraft(); sync(); if (state.selected === `s:${id}`) renderPanel(); return;
    }
    case 'toggle-link': case 'link-on': case 'link-off': {
      const on = act === 'link-on' || (act === 'toggle-link' && scene().cuts[cutIndex(id)]?.startFrame?.source !== 'previous');
      if (on && cutIndex(id) === 0) return;
      editCut(id, (cut) => ({ ...cut, startFrame: on ? { source: 'previous', asset_id: null } : null }));
      void s.saveDraft(); sync(); if (state.selected === `s:${id}`) renderPanel(); return;
    }
    case 'move-left': case 'move-right': {
      s.editCuts((list) => {
        const i = list.findIndex((cut) => cut.id === id); const j = i + (act === 'move-left' ? -1 : 1);
        if (i < 0 || j < 0 || j >= list.length) return list;
        [list[i], list[j]] = [list[j], list[i]]; return list;
      });
      try { localStorage.removeItem(storeKey()); } catch { /* ignore */ }
      void s.saveDraft(); sync(); renderPanel(); return;
    }
    case 'remove':
      if (!window.confirm('Удалить этот шот? Готовые ролики останутся в очереди и библиотеке.')) return;
      s.editCuts((list) => list.filter((cut) => cut.id !== id)); state.selected = null; void s.saveDraft(); sync(); return;
    case 'ref-up': case 'ref-down': s.moveReference(ref, act === 'ref-up' ? -1 : 1); sync(); return;
    case 'ref-remove':
      if (!window.confirm('Убрать этот материал из сцены? Файл останется в библиотеке.')) return;
      s.removeReference(ref); state.selected = null; sync(); return;
    case 'glue': void glue(); return;
    case 'link-env': {
      const project = s.project();
      project.storyboard = { ...(project.storyboard || {}), linkPrevious: !storyboardState(project).linkPrevious };
      void s.saveDraft(); renderPanel(); return;
    }
    case 'start-none': editCut(id, (cut) => ({ ...cut, startFrame: null })); void s.saveDraft(); sync(); renderPanel(); return;
    case 'start-frame':
      editCut(id, (cut) => ({ ...cut, startFrame: { source: 'library', asset_id: cut.startFrame?.source !== 'previous' ? cut.startFrame?.asset_id || null : null } }));
      void s.saveDraft(); sync(); renderPanel(); return;
    case 'frame-upload': case 'frame-library':
      // The storyboard keeps the current link mode when it sets the picture: make it «own frame» first.
      if (scene().cuts[cutIndex(id)]?.startFrame?.source !== 'library') editCut(id, (cut) => ({ ...cut, startFrame: { source: 'library', asset_id: null } }));
      storyboardAction(act, id); setTimeout(() => { sync(); renderPanel(); }, 1500); return;
    case 'staging':
      window.h3Storyboard?.staging(id);
      [...document.querySelectorAll('dialog.production-spec-editor')].at(-1)
        ?.addEventListener('close', () => { sync(); renderPanel(); }, { once: true });
      return;
    case 'frame-accept': case 'frame-unaccept':
      void window.h3Storyboard?.acceptFrame(id, act === 'frame-accept').then(() => { sync(); renderPanel(); }); return;
    case 'frame-keyframe':
      editCut(id, (cut) => ({ ...cut, startFrame: { source: 'keyframe', asset_id: cut.startFrame?.asset_id || null } }));
      storyboardAction('keyframe', id);
      toast(document.querySelector('#storyboard .storyboard-status')?.textContent || 'Ключевой кадр готовится…'); return;
    case 'director-pick': {
      s.selectCut(id);
      const dialog = document.getElementById('directorDialog');
      dialog?.addEventListener('close', () => { if (state.selected === `s:${id}`) renderPanel(); }, { once: true });
      document.querySelector(`#directorStrip [data-category="${CSS.escape(button.dataset.category)}"]`)?.click();
      return;
    }
    case 'library-type': document.querySelector(`#referenceTypes [data-type="${CSS.escape(button.dataset.type)}"]`)?.click(); return;
    case 'qc-accept': case 'qc-reject':
      storyboardAction(act, id); setTimeout(() => { sync(); renderPanel(); }, 1000); return;
    case 'qc': storyboardAction('qc', id); toast('AI проверяет шот по кадрам…'); setTimeout(() => renderPanel(), 8000); return;
    default:
  }
}

let promptTimer;
function onInput(event) {
  const field = event.target.dataset.kcField;
  const id = event.target.dataset.cut;
  if (field === 'prompt' && id) {
    const value = event.target.value;
    clearTimeout(promptTimer);
    promptTimer = setTimeout(() => { editCut(id, (cut) => ({ ...cut, prompt: value })); patch(); }, 400);
  }
}

function onChange(event) {
  const { kcField: field, cut: id, ref } = event.target.dataset;
  const s = studio();
  if (field === 'scene') { s.selectScene(event.target.value); return; }
  if (field === 'prompt' && id) { clearTimeout(promptTimer); editCut(id, (cut) => ({ ...cut, prompt: event.target.value })); void s.saveDraft(); patch(); }
  if (field === 'duration' && id) {
    const seconds = Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(Number(event.target.value) || 5)));
    event.target.value = String(seconds);
    editCut(id, (cut) => ({ ...cut, duration: seconds })); void s.saveDraft(); patch();
  }
  if (field === 'role' && ref) { s.updateReference(ref, { role: event.target.value }); sync(); }
  if (field === 'owner' && ref) { s.updateReference(ref, { owner: event.target.value }); patch(); }
}

// --- lifecycle -------------------------------------------------------------------------------

function sync({ fit = false } = {}) {
  if (!state.shell) return;
  const { cuts, references } = scene();
  const key = structureKey(sceneId(), cuts, references, aspect());
  if (key !== state.key || fit) { state.key = key; state.chipKey = ''; render({ fit }); } else patch();
}

async function tick() {
  if (document.hidden || !state.shell) return;
  await refreshJobs();
  sync();
  // Keep the open panel's video/status fresh without touching a field being edited.
  if (state.selected?.startsWith('s:') && !state.panel.contains(document.activeElement)) {
    const { cuts } = scene(); const i = cutIndex(state.selected.slice(2));
    const panelKey = i < 0 ? '' : JSON.stringify([state.selected, nodeState(cuts, i, state.jobs).status, selectedJobId(cuts[i]), takeList(cuts[i], state.jobs).length]);
    if (panelKey !== state.panelKey) { state.panelKey = panelKey; renderPanel(); }
  }
}

// The page-boot cover waits for the canvas: switching kc-loading → kc-on moves the page 3 px.
let canvasShown = () => {};
if (sceneView) window.studioBoot?.hold(new Promise((resolve) => { canvasShown = resolve; }));

function install() {
  const board = document.getElementById('scene-board');
  if (!studio()?.ready?.() || !board || !document.getElementById('storyboard')) { setTimeout(install, 50); return; }
  if (state.shell) return;
  buildShell(board);
  document.body.classList.add('kc-on');
  // The old assembly handlers remain available to proxy actions, but must not take keyboard focus.
  for (const node of document.querySelectorAll('.scene-mode-bar, #scene-assembly, #film-assembly, #scene-timeline')) node.inert = true;
  // Build nodes and restore the saved view before revealing the workspace. Jobs patch statuses
  // afterwards; waiting for the full history must not expose the old board or an empty canvas.
  sync();
  // A hidden or background window gets no animation frames: a short timer reveals the canvas there too.
  let revealed = false;
  const reveal = () => {
    if (revealed) return;
    revealed = true;
    fitToWindow();
    document.body.classList.remove('kc-loading');
    canvasShown();
  };
  requestAnimationFrame(reveal);
  setTimeout(reveal, 120);
  void refreshJobs().then(() => sync());
  const resync = () => sync();
  window.addEventListener('h3:scenes-changed', () => { renderScenePicker(); resync(); });
  window.addEventListener('h3:scene-active-changed', () => { state.selected = null; state.key = ''; sync(); });
  window.addEventListener('h3:shot-added', resync);
  setInterval(tick, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void tick(); });
  window.addEventListener('resize', () => { fitToWindow(); applyView(); });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !event.target.closest('textarea, input, select, dialog')) { toggleDirector(false); select(null); }
  });
}

if (sceneView) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
}
