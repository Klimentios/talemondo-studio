// «Сцены» — one pipeline «из коротких шотов — один сюжет», three zones top to bottom:
//   «Библия сцены»  shared heroes / locations / props / voices, scene style, scene seed;
//   «Раскадровка»   one card per shot (cut) with its own start frame, prompt, duration, status;
//   «Сборка»        «Собрать сцену» (shot N+1 may start from the last frame of shot N),
//                   «Склеить в ролик».
// The chain runs in this page by polling /api/jobs; its state lives in the project draft
// (project.storyboard, cut.startFrame, cut.shotJobId), so a reload resumes it.
// Keyframe generation is a separate backend: «✨ Ключевой кадр» fires the window event
// 'h3:storyboard-keyframe' and the result comes back through window.h3Storyboard.setStartFrame.
import {
  LINK_LABELS, STATUS_LABELS, bibleVoices, chainWarnings, continuationWarning,
  nativeContinuationSource, enqueueOptions, finishedJobIds, jobProgress, linkMode, moveCut, pendingShots, resetFailed, sceneSummary,
  shotPlan, shotStatus, storyboardState, shotCardDescription, MIN_SECONDS, MAX_SECONDS,
  frameAccepted, frameAcceptance, runSources,
} from './storyboard-model.mjs?v=20261102-take-admission-1';
import { shotReferences } from './shot-references.mjs?v=20261101-breakdown-1';
import { openProductionSpecEditor } from './production-spec-editor.mjs?v=20261029-frames-1';
import { openMotionDirectorEditor } from './motion-director-editor.mjs?v=20261105-steps25';
import { START_FRAME_TYPES, startFrameChoices } from './storyboard-picker.mjs?v=20261007-1';
import { directorField, directorFieldOptions } from './director-config.mjs?v=20261007-camera-options-5';
import { arrangeShotInspector } from './scene-inspector.mjs?v=20261029-frames-1';
import { renderSceneTimelineLanes } from './scene-timeline-lanes.mjs?v=20261007-1';
import { planFilm } from './film-assembly.mjs?v=20261101-keyframe-1';
import { mountEditSheet } from './edit-sheet.mjs?v=20261106-aspects-1';
import { errorText, jobErrorText } from './api-error.mjs?v=20261008-1';
import { isFantasy } from './story-canvas-worlds.mjs?v=20261103-codex-world-1';

const studio = () => window.h3Studio;
const POLL_MS = 3000;
const OWNER_ROLES = new Set(['Лицо героя', 'Персонаж целиком', 'Костюм', 'Актёрская игра', 'Голос']);
let jobsById = new Map();
let jobsLoaded = false;

let filmBusy = false;
const signatures = { bible: '', board: '', assembly: '', film: '' };
let zones;
let picker;
let selectedShotId = null;
let selectedShotIds = new Set();
const openReferenceSettings = new Set();

function el(tag, { dataset = {}, ...attrs } = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('aria-') || key === 'role') node.setAttribute(key, value);
    else node[key] = value;
  }
  Object.assign(node.dataset, dataset);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false && child !== ''));
  return node;
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(payload, response.status));
  return payload;
}
const post = (url, body) => json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

const cuts = () => studio().cuts();
const project = () => studio().project();
const board = () => storyboardState(project());
function setBoard(patch) {
  project().storyboard = { ...(project().storyboard || {}), ...board(), ...patch };
  void studio().saveDraft().catch(() => {});
  renderAll(true);
}
function say(text) { if (zones) zones.status.textContent = text; }
function patchCut(id, change) { studio().editCuts((list) => list.map((cut) => (cut.id === id ? change(cut) : cut))); }
const assetUrl = (id) => `/api/assets/${encodeURIComponent(id)}/file`;

async function refreshJobs() {
  try {
    jobsById = new Map((await json('/api/jobs')).map((job) => [job.job_id, job]));
    jobsLoaded = true;
  } catch { jobsLoaded = false; }
}

async function uploadImage(file, role = 'Кадр сцены') {
  const data = new FormData();
  data.append('file', file, file.name || 'frame.png');
  const asset = await json(`/api/assets?role=${encodeURIComponent(role)}`, { method: 'POST', body: data });
  if (asset.kind !== 'image') throw new Error('Стартовый кадр — только изображение (PNG, JPEG, WebP)');
  studio().rememberAsset(asset);
  return asset;
}

function setStartFrame(cutId, asset, source = 'library') {
  if (!cuts().some((cut) => cut.id === cutId)) throw new Error('Шот не найден');
  if (asset) studio().rememberAsset(asset);
  patchCut(cutId, (cut) => ({ ...cut, startFrame: { source, asset_id: asset?.asset_id || null }, productionFrameChanged: false }));
  renderAll(true);
  void tellRun(cutId, { startFrame: { source, asset_id: asset?.asset_id || null }, productionFrameChanged: false }).catch((error) => say(error.message));
}

// --- chain -------------------------------------------------------------------------------

async function extractLastFrame(fromJobId) {
  const asset = await post(`/api/jobs/${encodeURIComponent(fromJobId)}/last-frame`, {});
  studio().rememberAsset(asset);
  setBoard({ lastFrames: { ...board().lastFrames, [fromJobId]: asset.asset_id } });
  return asset;
}

async function enqueueShot(cutId, plan) {
  const options = enqueueOptions(plan);
  if (board().motionContext && plan.startFrame?.source === 'previous') {
    const index = cuts().findIndex(cut => cut.id === cutId);
    const previous = cuts()[index - 1];
    const fromJob = nativeContinuationSource(plan, previous);
    const caps = await json('/api/studio/capabilities');
    if (!caps.continuation_v1) throw new Error('GPU не подтвердил продолжение по видео и звуку');
    const asset = await post(`/api/jobs/${encodeURIComponent(fromJob)}/reference-video`, {});
    studio().rememberAsset(asset);
    options.continuationAssetId = asset.asset_id;
    options.startFrameAssetId = null;
  }
  const job = await studio().enqueueCut(cutId, options);
  patchCut(cutId, (cut) => ({ ...cut, shotJobId: job.job_id, selectedTakeId: job.job_id,
    productionSpecChanged: false,
    takes: [...(cut.takes || (cut.shotJobId ? [{ id: cut.shotJobId, jobId: cut.shotJobId }] : [])), { id: job.job_id, jobId: job.job_id, createdAt: new Date().toISOString() }],
    shotQc: null, shotQcReview: null, shotQcPending: false, shotQcError: '',
    ...(plan.startFrame?.source === 'previous' ? { startFrame: plan.startFrame } : {}) }));
  await studio().saveDraft();
  jobsById.set(job.job_id, job);
  return job;
}

// «◇ Проверить»: the Studio agent reviews the finished shot (MCP job_frames: a contact sheet, full decode, sound,
// hard cuts) against what the shot must show. The old automatic Qwen VL check is gone: nothing blocks the scene.
async function checkShot(cutId) {
  const index = cuts().findIndex((cut) => cut.id === cutId);
  const cut = cuts()[index];
  if (!cut?.shotJobId || shotStatus(cuts(), index, jobsById) !== 'done') throw new Error('Для проверки нужен готовый актуальный шот');
  if (!window.h3AgentDock) throw new Error('Панель агента не загрузилась — обнови страницу');
  const cast = shotReferences(studio().references(), cut.prompt || '', [], null, cut.offRefs).references
    .filter((ref) => ref.kind === 'image').map((ref) => `${ref.owner || ref.role} (${ref.role}, asset_id ${ref.id})`);
  const shot = cut.sceneShot || {};
  const next = cuts()[index + 1]?.sceneShot?.state_before;
  window.h3AgentDock.ask([
    `Проверь шот ${index + 1} «${shot.title || ''}»: посмотри его кадры через job_frames(job_id=${cut.shotJobId}).`,
    `Что должно происходить: ${(shot.purpose || cut.prompt || '').slice(0, 600)}`,
    shot.state_before ? `В начале: ${shot.state_before}` : '', shot.state_after ? `В конце: ${shot.state_after}` : '',
    next ? `Следующий шот начинается так: ${next}` : '',
    cast.length ? `Герои — сверь лица и костюмы с референсами (view_image): ${cast.join('; ')}` : '',
    'Ответь: принять или переснять, и что именно не так (лица, анатомия, контакты, пространство, склейки, звук). Ничего не перезапускай сам.',
  ].filter(Boolean).join('\n'));
  say(`Шот ${index + 1} отправлен агенту на проверку — ответ в панели «✦ Агент».`);
}

// ▶ / ↻ for one shot: take the needed last frames first (start frame, colour sample), then enqueue.
async function runShot(cutId) {
  await refreshJobs();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const index = cuts().findIndex((cut) => cut.id === cutId);
    const plan = shotPlan(cuts(), index, jobsById, board());
    if (!plan.ok) throw new Error(plan.message);
    if (plan.extractFrom) { await extractLastFrame(plan.extractFrom); continue; }
    await enqueueShot(cutId, plan);
    say(`Шот ${index + 1} в очереди`);
    return;
  }
  throw new Error('Не удалось подготовить кадры шота');
}

// --- scene run on the Mac (local_studio/scene_runs.py) -------------------------------------------------------
// «Собрать сцену» compiles each pending shot's job here once and hands the scene to the Mac: keyframes, the stop
// for «✓ Принять кадр», renders, last frames and the glue go on there without this tab or an agent. The tab
// mirrors the run into the cuts (the fields below) and sends the user's word on a shot back.
const RUN_FIELDS = ['frameAcceptance', 'frameReview', 'shotJobId', 'selectedTakeId', 'takes', 'startFrame', 'keyframeJobId', 'keyframeJobIds', 'keyframeVariants',
  'takeReview', 'takeReviewHistory', 'keyframePick', 'keyframeSpecSnapshot', 'productionFrameChanged', 'productionSpecChanged', 'shotQc', 'shotQcReview', 'shotQcPending', 'shotQcError'];
let run = null;      // the active scene's run as the Mac last reported it
let runSeen = null;  // its updated_at, already in the cuts
const sceneId = () => studio().activeSceneId?.() || 'scene';
const runUrl = (tail = '') => `/api/scene-runs/${encodeURIComponent(sceneId())}${tail}`;

async function pullRun() {
  const response = await fetch(runUrl());
  if (response.status === 404) {
    run = null;
    if (board().running) setBoard({ running: false });  // a draft from the time the chain ran in the tab
    return;
  }
  const payload = await response.json().catch(() => null);
  if (response.ok && payload) { run = payload; mirrorRun(); }
}

function mirrorRun() {
  if (!run || run.scene_id !== sceneId() || run.updated_at === runSeen) return;
  runSeen = run.updated_at;
  const remote = new Map(run.cuts.map((cut) => [cut.id, cut]));
  studio().editCuts((list) => list.map((cut) => {
    const from = remote.get(cut.id);
    if (!from) return cut;
    const next = { ...cut };
    for (const key of RUN_FIELDS) if (key in from) next[key] = from[key]; else delete next[key];
    return next;
  }));
  setBoard({ running: run.state === 'running', message: run.message || '',
    lastFrames: { ...board().lastFrames, ...(run.board?.lastFrames || {}) },
    ...(run.board?.lastConcat ? { lastConcat: run.board.lastConcat } : {}) });
}

// The user's word on a shot of a scene the Mac is building (accept or replace the frame, review a take).
async function tellRun(cutId, change) {
  const targetScene = sceneId();
  const endpoint = `/api/scene-runs/${encodeURIComponent(targetScene)}`;
  if (!run || run.scene_id !== targetScene) {
    // A decision can arrive before the first polling tick. Read the receipt without
    // mirroring its old verdict over the decision that the user just made.
    const response = await fetch(endpoint);
    if (response.status === 404) return;
    if (!response.ok) throw new Error(`Не удалось прочитать сборку: HTTP ${response.status}`);
    run = await response.json();
  }
  if (sceneId() !== targetScene) throw new Error('Сцена изменилась во время сохранения решения');
  if (!run.cuts.some((cut) => cut.id === cutId)) return;
  run = await json(`${endpoint}/cuts/${encodeURIComponent(cutId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(change) });
  mirrorRun();
}

async function stopRun(message) {
  if (run && run.scene_id === sceneId()) { run = await post(runUrl('/stop'), {}); runSeen = null; }
  setBoard({ running: false, message });
}

async function startChain({ ask = true } = {}) {
  const fantasyCuts = cuts().filter(isFantasy);
  if (fantasyCuts.length) {
    const world = project().storyboard?.fantasyWorld;
    const ref = studio().references().find((item) => item.id === world?.refId &&
      (item.asset_id || item.id) === world?.assetId);
    if (!world?.assetId || !ref) throw new Error('Сначала нарисуй и привяжи «Мир фантазии» для этой сцены.');
    if (fantasyCuts.some((cut) => (cut.offRefs || []).includes(world.refId)))
      throw new Error('Мир фантазии выключен в одном из шотов фантазии. Нажми «Привязать к шотам».');
  }
  await refreshJobs();
  if (!jobsLoaded) throw new Error('Очередь Mac недоступна');
  const list = resetFailed(cuts(), jobsById);
  const pending = pendingShots(list, jobsById);
  if (!pending.length) { say('Все шоты уже готовы. Нажми «Склеить в ролик» или ↻ у шота, который нужно переснять.'); return { started: false, pending: [] }; }
  const drift = chainWarnings(list);
  const warning = drift.length ? `\n\nВнимание: шоты ${drift.join(', ')} — больше трёх продолжений подряд, лицо и цвет начнут «плыть». Лучше начать их с нового ключевого кадра.` : '';
  const keyframes = board().autoKeyframes ? list.filter((cut, index) => pending.includes(index + 1) && linkMode(cut) === 'keyframe' && !cut.startFrame?.asset_id).length : 0;
  const keyframeNote = keyframes ? `\n\nКлючевых кадров сделаю сам: ${keyframes} (локация — из последнего кадра прошлого шота, герои — из материалов сцены).` : '';
  // The agent (ui-bridge.mjs) starts it without the dialog: it asked the user before spending GPU.
  if (ask && !window.confirm(`Собрать сцену: сгенерировать шоты ${pending.join(', ')} по очереди? Готовые шоты пропускаются.${keyframeNote}${warning}`)) return { started: false, pending };
  studio().editCuts(() => list);
  say('Собираю задания шотов…');
  const selected = studio().activeCutId?.();
  const templates = {};
  try {
    for (const [index, cut] of cuts().entries()) {
      if (!pending.includes(index + 1)) continue;
      const { mode, ...sources } = runSources(cuts(), index, board());
      try { templates[cut.id] = { mode, body: await studio().enqueueCut(cut.id, { ...sources, compile: true }) }; }
      catch (error) { throw new Error(`Шот ${index + 1}: ${error.message}`); }
    }
  } finally { if (selected) studio().selectCut(selected); }
  await studio().saveDraft();
  const sourceScene = studio().sceneState?.().scenes?.find((scene) => scene.id === sceneId());
  run = await post(runUrl(), { title: studio().sceneState?.().scenes?.find((scene) => scene.id === sceneId())?.title || '',
    source_context: sourceScene ? Object.fromEntries(['sceneStyle', 'seed', 'renderState', 'referenceState']
      .filter((key) => key in sourceScene).map((key) => [key, sourceScene[key]])) : null,
    cuts: cuts(), board: { ...board(), fantasyWorld: project().storyboard?.fantasyWorld }, references: studio().references(),
    options: { sceneStyle: project().sceneStyle || '', aspect: project().aspect,
      franchise_id: project().sceneDirector?.agentScene?.franchise_id ||
        studio().sceneState?.().scenes?.find((scene) => scene.id === sceneId())?.franchiseSource?.franchise_id || '' }, templates });
  runSeen = null; mirrorRun();
  return { started: true, pending, keyframes, warnings: drift, onMac: true };
}

async function glue() {
  await refreshJobs();
  const ids = finishedJobIds(cuts(), jobsById);
  say(`Склеиваю ${ids.length} шот(а/ов)…`);
  const result = await post('/api/storyboard/concat', { job_ids: ids });
  setBoard({ lastConcat: result, message: `Склеено: ${result.duration.toFixed(1)} с, ${result.mode === 'copy' ? 'без перекодирования' : 'с перекодированием (параметры шотов различались)'}` });
}

async function assembleFilm() {
  if (filmBusy) return;
  filmBusy = true;
  zones.filmStatus.textContent = 'Собираем фильм из выбранных дублей…';
  renderFilmAssembly();
  try {
    await refreshJobs();
    if (!jobsLoaded) throw new Error('Очередь Mac недоступна. Проверь подключение и попробуй ещё раз.');
    const plan = planFilm(studio().sceneState().scenes, jobsById);
    if (!plan.ready) throw new Error(plan.issues.join(' · '));
    const result = await post('/api/storyboard/concat', { job_ids: plan.ids });
    project().filmAssembly = result;
    await studio().saveDraft();
    zones.filmStatus.textContent = `Фильм собран: ${plan.scenes} сцен, ${plan.shots} шотов, ${Number(result.duration).toFixed(1)} с.`;
  } catch (error) {
    zones.filmStatus.textContent = error.message || 'Не удалось собрать фильм';
  } finally {
    filmBusy = false;
    renderFilmAssembly();
  }
}

// --- library picker ----------------------------------------------------------------------

async function pickFromLibrary(onPick) {
  const assets = await json('/api/assets');
  const search = picker.querySelector('.storyboard-picker-search');
  const type = picker.querySelector('.storyboard-picker-type');
  const grid = picker.querySelector('.storyboard-picker-grid');
  const count = picker.querySelector('.storyboard-picker-count');
  search.value = ''; type.value = 'all';
  const render = () => {
    const choices = startFrameChoices(assets, project().library || {}, { query: search.value, type: type.value });
    count.textContent = `${choices.length} изображений`;
    grid.replaceChildren(...choices.map(({asset,info}) => el('button',
      { type: 'button', className: 'storyboard-pick', title: `${info.name} · ${info.category}`,
        onclick: () => { picker.close(); onPick(asset); } },
      el('img', { src: assetUrl(asset.asset_id), alt: '', loading: 'lazy' }),
      el('strong', {}, info.name), el('small', {}, info.category))));
    if (!choices.length) grid.append(el('p', { className: 'storyboard-hint' }, 'Ничего не найдено. Измени поиск или загрузи новый стартовый кадр.'));
  };
  search.oninput = render; type.onchange = render;
  render();
  picker.showModal();
  search.focus();
}

function filePrompt(accept, multiple, onFiles) {
  const input = el('input', { type: 'file', accept, multiple });
  input.addEventListener('change', () => { if (input.files.length) onFiles([...input.files]); });
  input.click();
}

function sceneStatusLabel(status) {
  return ({ idle: 'Черновик', queued: 'В очереди', running: 'Генерируется', done: 'Готов',
    failed: 'Не получилось', stale: 'Нужно обновить', missing: 'Нет видео' })[status] || STATUS_LABELS[status] || status;
}

function sceneLinkLabel(mode) {
  return ({ none: 'Отдельный шот', library: 'Свой кадр', previous: 'Продолжение', keyframe: 'Ключевой кадр' })[mode] || 'Отдельный шот';
}

function shotActionValue(cut) {
  const action = cut.sceneShot?.action || shotCardDescription(cut);
  return /^добавь действие\.?$/i.test(String(action).trim()) ? '' : action;
}

function shotCardSummary(cut) {
  return shotActionValue(cut) || 'Добавьте действие';
}

// --- zone A: scene bible -----------------------------------------------------------------

function renderBible(force) {
  const refs = studio().references();
  const roles = studio().referenceRoles();
  const style = project().sceneStyle || '';
  const seed = project().seed ?? '';
  const next = JSON.stringify([refs.map((ref) => [ref.id, ref.number, ref.role, ref.owner]), style, seed]);
  if (!force && next === signatures.bible) return;
  signatures.bible = next;
  const voices = new Map(bibleVoices(refs).map((ref) => [ref.id, ref.speaker]));
  const groups = new Map([
    ['Герои', []], ['Костюмы', []], ['Места', []], ['Предметы', []], ['Звук и другое', []],
  ]);
  const groupFor = (ref) => {
    if (['Лицо героя', 'Персонаж целиком', 'Актёрская игра'].includes(ref.role)) return 'Герои';
    if (ref.role === 'Костюм') return 'Костюмы';
    if (ref.role === 'Окружение') return 'Места';
    if (ref.role === 'Предмет') return 'Предметы';
    return 'Звук и другое';
  };
  refs.forEach((ref) => groups.get(groupFor(ref)).push(ref));
  const cards = refs.map((ref) => {
    const tag = `<${{ image: 'Picture', video: 'Video', audio: 'Audio' }[ref.kind]} ${ref.number}>`;
    const label = ({ 'Лицо героя': 'Личность', 'Персонаж целиком': 'Образ', 'Актёрская игра': 'Актёрская игра',
      'Костюм': 'Костюм', 'Окружение': 'Локация', 'Предмет': 'Предмет', 'Голос': 'Голос',
      'Стиль': 'Стиль', 'Свет': 'Свет', 'Цвет': 'Цвет', 'Движение': 'Движение', 'Движение и камера': 'Движение' })[ref.role] || 'Материал';
    const thumb = ref.kind === 'image' ? el('img', { src: studio().referenceUrl(ref.id), alt: '' })
      : el('span', { className: 'storyboard-bible-icon' }, ref.kind === 'audio' ? '♫' : '▶');
    const role = el('select', { dataset: { action: 'ref-role', id: ref.id }, 'aria-label': `Роль ${ref.name}` },
      ...(roles[ref.kind] || []).map((value) => el('option', { value, selected: value === ref.role }, value)));
    const owner = OWNER_ROLES.has(ref.role) ? el('input', { value: ref.owner || '', maxLength: 80, placeholder: ref.role === 'Голос' ? 'Чей голос' : 'Имя героя', dataset: { action: 'ref-owner', id: ref.id } }) : null;
    const settings = el('details', { className: 'storyboard-bible-settings', open: openReferenceSettings.has(ref.id) },
      el('summary', {}, 'Настроить'),
      el('small', { className: 'storyboard-bible-mapping' }, `Для генерации: ${tag}`), role, owner,
      el('div', { className: 'storyboard-bible-tools' },
        el('button', { type: 'button', title: 'Раньше', dataset: { action: 'ref-up', id: ref.id } }, '↑'),
        el('button', { type: 'button', title: 'Позже', dataset: { action: 'ref-down', id: ref.id } }, '↓'),
        el('button', { type: 'button', title: 'Убрать из сцены', dataset: { action: 'ref-remove', id: ref.id } }, 'Убрать')));
    settings.addEventListener('toggle', () => {
      if (settings.open) openReferenceSettings.add(ref.id);
      else openReferenceSettings.delete(ref.id);
    });
    return { ref, group: groupFor(ref), node: el('article', { className: 'storyboard-bible-ref' }, thumb,
      el('div', { className: 'storyboard-bible-copy' },
        el('div', { className: 'storyboard-bible-title' }, el('strong', {}, ref.owner?.trim() || (voices.has(ref.id) ? voices.get(ref.id) : ref.name)),
          el('span', {}, label)),
        el('small', { title: ref.name }, ref.owner?.trim() && voices.get(ref.id) ? voices.get(ref.id) : ref.name),
        settings)) };
  });
  const sections = [...groups].filter(([, items]) => items.length).map(([title, items]) =>
    el('section', { className: 'storyboard-asset-group' },
      el('h4', {}, title, el('span', {}, String(items.length))),
      el('div', { className: 'storyboard-asset-grid' }, ...items.map((ref) => cards.find((card) => card.ref.id === ref.id).node))));
  zones.bibleList.replaceChildren(...(sections.length ? sections : [el('div', { className: 'storyboard-asset-empty' },
    el('strong', {}, 'Добавьте материалы для сцены'), el('span', {}, 'Перетащите сюда героя, локацию или предмет — они появятся отдельными карточками.'))]));
  zones.voices.textContent = voices.size ? `Аудиообразцов: ${voices.size}` : '';
  if (document.activeElement !== zones.style) zones.style.value = style;
  if (document.activeElement !== zones.seed) zones.seed.value = seed;
}

// --- zone B: shot cards ------------------------------------------------------------------

function startSlot(cut, index) {
  const mode = linkMode(cut);
  const frame = cut.startFrame;
  const thumb = frame?.asset_id
    ? el('img', { className: 'storyboard-thumb', alt: 'Стартовый кадр', src: assetUrl(frame.asset_id) })
    : el('div', { className: 'storyboard-thumb storyboard-empty' },
      mode === 'previous' ? (index ? `возьмётся конец шота ${index}` : 'нет предыдущего шота')
        : mode === 'keyframe' ? 'Кадр ещё не готов' : mode === 'library' ? 'Выберите изображение'
        : index && board().linkPrevious ? `Новый ракурс · окружение из конца шота ${index}` : 'Начало без изображения');
  const select = el('select', { dataset: { action: 'frame', id: cut.id }, 'aria-label': `Стартовый кадр шота ${index + 1}` },
    el('option', { value: 'none', selected: mode === 'none' }, 'Начать без кадра'),
    el('option', { value: 'library', selected: mode === 'library' }, 'Выбрать изображение'),
    el('option', { value: 'previous', selected: mode === 'previous', disabled: index === 0 }, 'Продолжить с конца предыдущего'),
    el('option', { value: 'keyframe', selected: mode === 'keyframe' }, 'Создать ключевой кадр'));
  const tools = [];
  if (mode === 'library' || mode === 'keyframe') tools.push(
    el('button', { type: 'button', dataset: { action: 'frame-upload', id: cut.id } }, 'Загрузить'),
    el('button', { type: 'button', dataset: { action: 'frame-library', id: cut.id } }, 'Из медиатеки'));
  if (mode === 'keyframe') tools.unshift(el('button', { type: 'button', className: 'storyboard-keyframe', dataset: { action: 'keyframe', id: cut.id },
    title: 'Сделать или поправить ключевой кадр по промпту шота и библии сцены' }, frame?.asset_id ? '✨ Поправить кадр' : '✨ Ключевой кадр'));
  const continuity = el('label', { className: 'storyboard-continuity-toggle' },
    el('input', { type: 'checkbox', checked: mode === 'previous', disabled: index === 0,
      dataset: { action: 'continuity', id: cut.id }, 'aria-label': `Продолжение предыдущего шота для шота ${index + 1}` }),
    'Продолжить с конца предыдущего');
  return { thumb, controls: el('div', { className: 'storyboard-slot' }, continuity, select, tools.length ? el('div', { className: 'storyboard-slot-tools' }, ...tools) : null) };
}

function card(cut, index, list) {
  const status = shotStatus(list, index, jobsById);
  const job = cut.shotJobId && jobsById.get(cut.shotJobId);
  const progress = jobProgress(job);
  const mode = linkMode(cut);
  const slot = startSlot(cut, index);
  const location = studio().references().find((ref) => ref.role === 'Окружение' && ref.kind === 'image');
  const previewAsset = cut.startFrame?.asset_id
    ? { url: assetUrl(cut.startFrame.asset_id), label: 'Стартовый кадр · видео ещё нет' }
    : location ? { url: studio().referenceUrl(location.id), label: 'Референс локации · не кадр' } : null;
  const result = status === 'done' || status === 'stale'
    ? el('video', { className: 'storyboard-result', src: `/api/jobs/${encodeURIComponent(cut.shotJobId)}/video`, controls: true, muted: true, preload: 'metadata', playsInline: true })
    : previewAsset
      ? el('div', { className: 'storyboard-result storyboard-reference-preview' },
        el('img', { src: previewAsset.url, alt: '', loading: 'lazy' }),
        el('span', {}, previewAsset.label),
        status === 'running' ? el('strong', {}, `Готовится${progress === null ? '…' : ` ${progress}%`}`) : null,
        status === 'queued' ? el('strong', {}, 'В очереди') : null)
    : el('div', { className: 'storyboard-result storyboard-empty' },
      status === 'running' ? `Готовится${progress === null ? '…' : ` ${progress}%`}` : status === 'queued' ? 'В очереди' : 'Видео появится после генерации');
  const running = status === 'queued' || status === 'running';
  const stagingButton = el('button', { type: 'button', className: 'k-btn storyboard-staging', disabled: running,
    title: 'Настроить действия и поведение этого шота' },
    cut.productionSpec ? `Постановка и поведение · v${cut.productionSpec.shot.revision}` : 'Создать постановку');
  stagingButton.addEventListener('click', () => {
    try { openProductionSpecEditor(cut, { studio: studio(), onSaved: () => renderAll(true) }); }
    catch (error) { say(error.message || String(error)); }
  });
  const drift = continuationWarning(list, index);
  const qc = status === 'done' && cut.shotQc?.job_id === cut.shotJobId ? cut.shotQc : null;
  const shotCamera = cut.sceneShot || {};
  const option = (value, label) => el('option', { value }, label);
  const fieldSelect = (label, field, choices) => el('label', { className: 'storyboard-shot-field' }, label,
    el('select', { dataset: { action: 'shot-field', id: cut.id, field }, 'aria-label': `${label} шота ${index + 1}` },
      option('', 'Авто'), ...choices.map(({ value, label: text }) => option(value, text.ru))));
  const framingOptions = directorFieldOptions('shot', 'framing');
  const movementOptions = directorFieldOptions('movement', 'movement').filter(({ value }) =>
    ['static','dolly','tracking','truck','orbit','pan','tilt'].includes(value));
  const lensField = directorField('lens', 'focal_length_mm');
  const colour = index > 0 && board().colorSource !== 'off'
    ? el('label', { className: 'storyboard-check' }, el('input', { type: 'checkbox', checked: cut.colorMatch !== false, dataset: { action: 'colour', id: cut.id } }),
      board().colorSource === 'first' ? ' цвет по эталону (шот 1)' : ' цвет по предыдущему шоту')
    : null;
  return el('article', { className: `storyboard-card is-${status}`, dataset: { id: cut.id } },
    el('header', {}, el('strong', {}, `Шот ${String(index + 1).padStart(2, '0')}`),
      el('span', { className: `storyboard-link is-${mode}` }, sceneLinkLabel(mode)),
      el('span', { className: `storyboard-badge is-${status}`, title: job?.error || '' }, sceneStatusLabel(status) + (progress === null ? '' : ` ${progress}%`))),
    progress === null ? null : el('progress', { max: 100, value: progress }),
    el('div', { className: 'storyboard-media' }, slot.thumb, result),
    el('div', { className: 'storyboard-captions' }, el('span', {}, 'Начало'), el('span', {}, 'Последний дуб')),
    slot.controls,
    (cut.takes?.length || cut.shotJobId) ? el('div', { className: 'storyboard-takes', 'aria-label': 'Дубли шота' },
      el('strong', {}, 'Дубли'),
      ...(cut.takes || [{ id: cut.shotJobId, jobId: cut.shotJobId }]).map((take, takeIndex) => el('button', {
        type: 'button', className: `storyboard-take${(take.jobId || take.id) === cut.shotJobId ? ' is-selected' : ''}`,
        dataset: { action: 'take', id: cut.id, jobId: take.jobId || take.id }, 'aria-pressed': String((take.jobId || take.id) === cut.shotJobId),
      }, `${String(takeIndex + 1).padStart(2, '0')}${(take.jobId || take.id) === cut.shotJobId ? ' ★' : ''}`))) : null,
    el('label', { className: 'storyboard-prompt-label' }, 'Что происходит',
      el('textarea', { rows: 3, value: shotActionValue(cut), placeholder: 'Опишите, что происходит в кадре', dataset: { action: 'shot-action', id: cut.id } })),
    el('div', { className: 'storyboard-shot-camera', 'aria-label': 'Камера шота' },
      fieldSelect('План', 'framing', framingOptions),
      el('label', { className: 'storyboard-shot-field' }, 'Объектив · мм',
        el('input', { type: 'number', min: lensField.min, max: lensField.max, step: lensField.step,
          value: shotCamera.lens_mm ?? '', placeholder: 'Авто', dataset: { action: 'shot-field', id: cut.id, field: 'lens_mm' },
          'aria-label': `Объектив шота ${index + 1}` })),
      fieldSelect('Камера', 'camera_motion', movementOptions)),
    el('div', { className: 'storyboard-row' },
      el('label', { className: 'storyboard-duration' }, 'Длительность ',
        el('input', { type: 'number', min: MIN_SECONDS, max: MAX_SECONDS, step: 1, value: cut.duration, dataset: { action: 'duration', id: cut.id } }), ' сек'),
      colour),
    stagingButton,
    cut.productionFrameChanged && cut.startFrame?.asset_id ? el('p', { className: 'storyboard-note' },
      'Опорный кадр относится к прежней постановке. Создай новый или явно выбери подходящий заново перед видео.') : null,
    drift ? el('p', { className: 'storyboard-note' }, drift) : null,
    status === 'stale' ? el('p', { className: 'storyboard-note' }, cut.productionSpecChanged
      ? 'Постановка изменена. Этот ролик создан по прежней версии; новая версия ещё не отрендерена.'
      : 'Предыдущий шот пересняли — этот начнётся с его нового последнего кадра при «Собрать сцену».') : null,
    status === 'failed' && job?.error ? el('p', { className: 'storyboard-note is-error' }, jobErrorText(job.error)) : null,
    qc ? el('div', { className: `storyboard-qc is-${qc.decision}` },
      el('strong', {}, `AI-проверка · ${{accept:'принять',adapt_next:'адаптировать следующий',regenerate:'переснять',manual_review:'проверить вручную'}[qc.decision] || qc.decision}`),
      el('p', {}, qc.finding),
      el('small', {}, `Герой: ${qc.identity} · костюм: ${qc.costume} · место: ${qc.environment} · действие: ${qc.action} · связность: ${qc.continuity} · камера: ${qc.camera}`)) : null,
    status === 'done' && cut.shotQcReview !== 'accepted' ? el('div', { className: 'storyboard-qc-actions', 'aria-label': 'Решение после просмотра готового MP4' },
      el('button', { type: 'button', dataset: { action: 'qc-accept', id: cut.id } }, 'Принять после просмотра'),
      el('button', { type: 'button', dataset: { action: 'qc-reject', id: cut.id } }, 'Отклонить дубль'),
      el('button', { type: 'button', dataset: { action: 'edit', id: cut.id } }, 'Изменить шот')) : null,
    cut.shotQcError ? el('p', { className: 'storyboard-note is-error' }, `AI-проверка: ${cut.shotQcError}`) : null,
    el('div', { className: 'storyboard-actions' },
      el('button', { type: 'button', dataset: { action: 'qc', id: cut.id }, disabled: status !== 'done', title: 'Отдать готовый шот агенту на проверку: кадры, лица, склейки, звук' }, '◇ Проверить'),
      el('button', { type: 'button', className: 'storyboard-action-main', dataset: { action: 'run', id: cut.id }, disabled: running || status === 'done', title: 'Создать дуб этого шота' }, '▶ Создать дуб'),
      el('button', { type: 'button', dataset: { action: 'rerun', id: cut.id }, disabled: running || !cut.shotJobId, title: 'Создать ещё один дуб' }, '↻ Новый дуб'),
      el('button', { type: 'button', dataset: { action: 'edit', id: cut.id }, title: 'Открыть дополнительные настройки шота' }, '✎ Дополнительно'),
      el('button', { type: 'button', dataset: { action: 'up', id: cut.id }, disabled: index === 0, title: 'Переместить шот раньше', 'aria-label': `Переместить шот ${index + 1} раньше` }, '↑'),
      el('button', { type: 'button', dataset: { action: 'down', id: cut.id }, disabled: index === list.length - 1, title: 'Переместить шот позже', 'aria-label': `Переместить шот ${index + 1} позже` }, '↓'),
      el('button', { type: 'button', dataset: { action: 'remove', id: cut.id }, disabled: list.length === 1, title: 'Удалить шот', 'aria-label': `Удалить шот ${index + 1}` }, '×')));
}

function miniCard(cut, index, list) {
  const status = shotStatus(list, index, jobsById);
  const prompt = `${cut.sceneShot?.title || ''} ${cut.sceneShot?.purpose || ''} ${cut.prompt || ''}`.toLocaleLowerCase();
  const characters = [...new Set(studio().references().filter((ref) => OWNER_ROLES.has(ref.role) && ref.owner?.trim())
    .map((ref) => ref.owner.trim()).filter((owner) => prompt.includes(owner.toLocaleLowerCase())))];
  const continuity = sceneLinkLabel(linkMode(cut));
  const framingNames = {wide:'Общий',full:'В полный рост',medium:'Средний',close:'Крупный',extreme_close:'Деталь',insert:'Врезка',two_shot:'Два героя',over_shoulder:'Через плечо',point_of_view:'От первого лица'};
  const movementNames = {static:'Статика',dolly:'Наезд',tracking:'Следование',truck:'Боковой проезд',orbit:'Облёт',pan:'Панорама',tilt:'Наклон'};
  const camera = cut.sceneShot?.framing ? `${framingNames[cut.sceneShot.framing] || cut.sceneShot.framing}${cut.sceneShot.lens_mm ? ` · ${cut.sceneShot.lens_mm} мм` : ''}${movementNames[cut.sceneShot.camera_motion] ? ` · ${movementNames[cut.sceneShot.camera_motion]}` : ''}` : '';
  const hasRealPreview = Boolean(cut.startFrame?.asset_id) || (['done', 'stale'].includes(status) && Boolean(cut.shotJobId));
  const image = cut.startFrame?.asset_id
    ? el('img', { src: assetUrl(cut.startFrame.asset_id), alt: '', loading: 'lazy' })
    : ['done', 'stale'].includes(status) && cut.shotJobId
      ? el('video', { src: `/api/jobs/${encodeURIComponent(cut.shotJobId)}/video`, muted: true, preload: 'metadata', playsInline: true, 'aria-label': `Превью шота ${index + 1}` })
      : el('span', { className: 'storyboard-mini-empty' }, linkMode(cut) === 'previous' ? '↳ Продолжение'
      : linkMode(cut) === 'keyframe' ? 'Кадр ещё не выбран' : 'Превью появится после дубля');
  const location = !hasRealPreview && !cut.startFrame?.asset_id
    ? studio().references().find((ref) => ref.role === 'Окружение' && ref.kind === 'image')
    : null;
  const preview = location
    ? el('div', { className: 'storyboard-mini-reference' },
      el('img', { src: studio().referenceUrl(location.id), alt: `Референс локации: ${location.name}`, loading: 'lazy' }),
      el('span', {}, 'Референс локации · не кадр'))
    : image;
  const card = el('article', { className: `storyboard-mini is-${status}${selectedShotIds.has(cut.id) ? ' is-selected' : ''}`,
    dataset: { id: cut.id, groupId: cut.groupId || '' } },
    el('button', { type: 'button', className: 'storyboard-mini-select', dataset: { action: 'select', id: cut.id },
      'aria-pressed': String(selectedShotIds.has(cut.id)), 'aria-current': selectedShotId === cut.id ? 'true' : 'false' },
      el('span', { className: 'storyboard-mini-top' }, el('strong', {}, `Шот ${String(index + 1).padStart(2,'0')}`), el('small', {}, `${cut.duration} с`)),
      preview,
      el('strong', {}, cut.sceneShot?.title || `Шот ${index + 1}`),
      el('small', {}, shotCardSummary(cut)),
      el('span', { className: 'storyboard-mini-labels' },
        ...characters.map((name) => el('span', { className: 'storyboard-mini-character' }, name)),
        el('span', { className: `storyboard-mini-continuity is-${linkMode(cut)}` }, continuity),
        camera ? el('span', { className: 'storyboard-mini-camera' }, `🎥 ${camera}`) : null),
      el('span', { className: `storyboard-badge is-${status}` }, sceneStatusLabel(status))),
    cut.takes?.length || cut.shotJobId ? el('div', { className: 'storyboard-mini-footer' },
      el('div', { className: 'storyboard-mini-takes', 'aria-label': 'Дубли шота' },
        ...(cut.takes?.length ? cut.takes : [{ id: cut.shotJobId, jobId: cut.shotJobId }]).map((take, takeIndex) => {
        const jobId = take.jobId || take.id;
        const selected = jobId === cut.shotJobId;
        return el('button', { type: 'button', className: `storyboard-mini-take${selected ? ' is-selected' : ''}`,
          dataset: { action: 'take', id: cut.id, jobId }, 'aria-pressed': String(selected), title: `${selected ? 'Выбранный' : 'Выбрать'} дуб ${takeIndex + 1}` },
        `${String(takeIndex + 1).padStart(2,'0')}${selected ? ' ★' : ''}`);
        }))) : null);
  return card;
}

function renderBoard(force) {
  const list = cuts();
  if (!list.some((cut) => cut.id === selectedShotId)) selectedShotId = list[0]?.id;
  selectedShotIds = new Set([...selectedShotIds].filter((id) => list.some((cut) => cut.id === id)));
  if (!selectedShotIds.size && selectedShotId) selectedShotIds.add(selectedShotId);
  if (!selectedShotIds.has(selectedShotId)) selectedShotId = [...selectedShotIds].at(-1) || list[0]?.id;
  const next = JSON.stringify([list.map((cut) => [cut.id, cut.prompt, cut.sceneShot, cut.duration, cut.startFrame, cut.colorMatch, cut.shotJobId, cut.takes, cut.groupId,
    cut.shotJobId && jobsById.get(cut.shotJobId)?.state, cut.shotJobId && jobsById.get(cut.shotJobId)?.sampling_value,
    cut.shotQc, cut.shotQcPending, cut.shotQcError]), studio().references().map((ref) => [ref.id, ref.name, ref.role, ref.owner]),
    board().colorSource, project().storyboard?.shotGroups, selectedShotId, [...selectedShotIds]]);
  if (!force && next === signatures.board) return;
  signatures.board = next;
  const children = [];
  for (let index = 0; index < list.length;) {
    const cut = list[index];
    if (!cut.groupId) { children.push(miniCard(cut, index, list)); index += 1; continue; }
    const groupId = cut.groupId;
    const groupCuts = [];
    while (index < list.length && list[index].groupId === groupId) { groupCuts.push(miniCard(list[index], index, list)); index += 1; }
    const groupInfo = project().storyboard?.shotGroups?.[groupId] || { title: 'Блок шотов' };
    children.push(el('section', { className: 'storyboard-shot-group', dataset: { groupId } },
      el('header', { className: 'storyboard-shot-group-head' }, el('strong', {}, groupInfo.title),
        el('span', {}, `${groupCuts.length} шота`),
        el('button', { type: 'button', dataset: { action: 'group-rename', groupId }, title: 'Переименовать блок' }, '✎'),
        el('button', { type: 'button', dataset: { action: 'group-ungroup', groupId }, title: 'Снять рамку блока' }, 'Разгруппировать')),
      el('div', { className: 'storyboard-shot-group-cards' }, ...groupCuts)));
  }
  children.push(el('button', { type: 'button', className: 'storyboard-add', dataset: { action: 'add' } }, '＋ Шот'));
  zones.grid.replaceChildren(...children);
  const index = list.findIndex((cut) => cut.id === selectedShotId);
  zones.inspector.replaceChildren(index >= 0
    ? arrangeShotInspector(card(list[index], index, list), studio().references(), (id) => studio().referenceUrl(id))
    : null);
  const totalSeconds = list.reduce((sum, cut) => sum + (Number(cut.duration) || 0), 0);
  const totalLabel = zones.timeline.querySelector('.scene-timeline-total');
  totalLabel.textContent = `${list.length} шота · ${String(Math.floor(totalSeconds / 60)).padStart(2, '0')}:${String(totalSeconds % 60).padStart(2, '0')}`;
  const ruler = zones.timeline.querySelector('.scene-timeline-ruler');
  const step = totalSeconds > 90 ? 15 : totalSeconds > 45 ? 10 : 5;
  ruler.replaceChildren(...Array.from({ length: Math.floor(totalSeconds / step) + 1 }, (_, tickIndex) => {
    const second = tickIndex * step;
    return el('span', { style: `left:${totalSeconds ? second / totalSeconds * 100 : 0}%` }, `${Math.floor(second / 60)}:${String(second % 60).padStart(2, '0')}`);
  }));
  const track = zones.timeline.querySelector('.scene-timeline-track');
  track.replaceChildren(...list.map((cut, shotIndex) => {
    const block = el('button', { type: 'button', className: `scene-timeline-shot is-${shotStatus(list, shotIndex, jobsById)}${cut.id === selectedShotId ? ' is-selected' : ''}`,
      style: `flex-grow:${Math.max(1, Number(cut.duration) || 1)};flex-basis:48px`,
      title: `Шот ${String(shotIndex + 1).padStart(2, '0')} · ${cut.duration} сек`, 'aria-pressed': String(cut.id === selectedShotId) },
      el('strong', {}, String(shotIndex + 1).padStart(2, '0')), el('span', {}, `${Number(cut.duration) || 0}с`));
    block.addEventListener('click', () => zones.grid.querySelectorAll('.storyboard-mini-select')[shotIndex]?.click());
    return block;
  }));
  renderSceneTimelineLanes(zones.timeline.querySelector('.scene-timeline-lanes'), list, studio().references(),
    (shotIndex) => zones.grid.querySelectorAll('.storyboard-mini-select')[shotIndex]?.click());
  zones.inspector.querySelectorAll('[data-action="shot-field"]').forEach((select) => {
    const value = list[index]?.sceneShot?.[select.dataset.field];
    select.value = value == null ? '' : String(value);
  });
}

// --- zone C: assembly --------------------------------------------------------------------

function renderAssembly(force) {
  renderFilmAssembly();
  const state = board();
  const summary = sceneSummary(cuts(), jobsById);
  const last = project().storyboard?.lastConcat;
  const next = JSON.stringify([state, summary, last?.concat_id]);
  if (!force && next === signatures.assembly) return;
  signatures.assembly = next;
  zones.run.disabled = state.running;
  zones.stop.hidden = !state.running;
  zones.colourSource.value = state.colorSource;
  zones.strength.value = state.strength;
  zones.strength.disabled = state.colorSource === 'off';
  zones.linkPrevious.checked = state.linkPrevious;
  zones.motionContext.checked = state.motionContext;
  zones.autoKeyframes.checked = state.autoKeyframes;
  zones.keyframesFirst.checked = state.keyframesFirst;
  zones.summary.textContent = `${summary.shots} шот(а/ов) · ${summary.seconds} с по плану · готово ${summary.done} из ${summary.shots}`;
  if (state.message) say(state.message);
  zones.final.replaceChildren(...(last?.url ? [
    el('video', { src: last.url, controls: true, preload: 'metadata', playsInline: true }),
    el('a', { href: `${last.url}?download=true`, download: '' }, `Скачать ролик сцены · ${last.job_ids?.length || ''} шот(а/ов) · ${Number(last.duration).toFixed(1)} с`)] : []));
}

function renderFilmAssembly() {
  if (!zones?.filmRun || !studio()?.ready?.()) return;
  const plan = planFilm(studio().sceneState().scenes, jobsById);
  zones.filmRun.disabled = filmBusy || !jobsLoaded || !plan.ready;
  zones.editSheet?.render();
  const last = project().filmAssembly;
  const signature = JSON.stringify([plan.ids, plan.issues, plan.scenes, plan.shots, plan.seconds, jobsLoaded, last?.concat_id]);
  if (signature === signatures.film) return;
  signatures.film = signature;
  zones.filmSummary.textContent = `${plan.scenes} сцен · ${plan.shots} шотов · ${plan.seconds} сек по плану`;
  zones.filmIssues.textContent = !jobsLoaded ? 'Проверяем готовность всех шотов…'
    : plan.ready ? 'Все выбранные дубли готовы. Фильм будет собран в порядке сцен и шотов.'
      : `${plan.issues.slice(0, 3).join(' · ')}${plan.issues.length > 3 ? ` · и ещё ${plan.issues.length - 3}` : ''}`;
  const current = plan.ready && JSON.stringify(last?.job_ids) === JSON.stringify(plan.ids);
  zones.filmFinal.replaceChildren();
  if (last?.url) {
    const note = el('p', { className: `film-version${current ? '' : ' is-old'}` },
      current ? 'Текущая версия фильма' : 'Собранная версия из предыдущего выбора дублей. Для обновления собери фильм ещё раз.');
    const video = el('video', { src: last.url, controls: true, preload: 'metadata', playsInline: true });
    const download = el('a', { href: `${last.url}?download=true`, download: 'studio-film.mp4' }, '↓ Скачать фильм MP4');
    zones.filmFinal.append(note, video, download);
  }
}

function renderAll(force = false) {
  if (!zones || !studio()?.ready?.()) return;
  const active = document.activeElement;
  const editing = zones.root.contains(active) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(active.tagName);
  if (!editing || !zones.bible.contains(active)) renderBible(force && !editing);
  if (!editing || !(zones.grid.contains(active) || zones.inspector.contains(active))) renderBoard(force && !editing);
  renderAssembly(force);
}

// --- events ------------------------------------------------------------------------------

async function guarded(action) {
  try { await action(); } catch (error) { say(error.message); } finally { renderAll(true); }
}

function onClick(event) {
  const button = event.target.closest('button[data-action]');
  if (!button || !zones.root.contains(button)) return;
  const { action, id } = button.dataset;
  const index = cuts().findIndex((cut) => cut.id === id);
  switch (action) {
    case 'select': {
      const list = cuts();
      const targetIndex = list.findIndex((cut) => cut.id === id);
      const anchorIndex = list.findIndex((cut) => cut.id === selectedShotId);
      if (event.shiftKey && anchorIndex >= 0) {
        const [from, to] = [anchorIndex, targetIndex].sort((a,b) => a-b);
        selectedShotIds = new Set(list.slice(from, to + 1).map((cut) => cut.id));
      } else if (event.metaKey || event.ctrlKey) {
        if (selectedShotIds.has(id) && selectedShotIds.size > 1) selectedShotIds.delete(id);
        else selectedShotIds.add(id);
      } else selectedShotIds = new Set([id]);
      selectedShotId = id; renderBoard(true); return undefined;
    }
    case 'group-rename': {
      const groupId = button.dataset.groupId;
      const group = project().storyboard?.shotGroups?.[groupId];
      const header = button.closest('.storyboard-shot-group-head');
      if (!header || !group || header.querySelector('.storyboard-group-editor')) return undefined;
      const editor = document.createElement('form'); editor.className = 'storyboard-group-editor';
      const input = document.createElement('input'); input.value = group.title; input.maxLength = 80;
      input.setAttribute('aria-label', 'Название рамки');
      const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Сохранить';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Отмена';
      cancel.addEventListener('click', () => window.h3Storyboard?.refresh());
      editor.addEventListener('submit', async (submitEvent) => {
        submitEvent.preventDefault();
        if (!input.value.trim()) return;
        await guarded(async () => studio().renameShotGroup(groupId, input.value));
      });
      editor.append(input, save, cancel); header.replaceChildren(editor); input.focus(); input.select();
      return undefined;
    }
    case 'group-ungroup': return guarded(async () => { studio().ungroupShots(button.dataset.groupId); });
    case 'take': return guarded(async () => {
      patchCut(id, (cut) => ({ ...cut, shotJobId: button.dataset.jobId, selectedTakeId: button.dataset.jobId,
        shotQc: null, shotQcReview: null, shotQcPending: false, shotQcError: '' }));
      await studio().saveDraft();
    });
    case 'add': return guarded(async () => { studio().addShot(); });
    case 'up': case 'down': return guarded(async () => studio().editCuts((list) => moveCut(list, id, action === 'up' ? -1 : 1)));
    case 'edit': studio().selectCut(id); window.dispatchEvent(new CustomEvent('h3:scene-edit', { detail: { cutId: id } })); return undefined;
    case 'remove':
      if (!window.confirm('Удалить этот шот? Готовый ролик останется в очереди.')) return undefined;
      return guarded(async () => studio().editCuts((list) => list.filter((cut) => cut.id !== id)));
    case 'run': return guarded(() => runShot(id));
    case 'qc': return guarded(() => checkShot(id));
    case 'qc-accept': return guarded(async () => {
      const resumeAssembly = board().qcPausedShotId === id;
      const acceptance = { job_id: cuts().find((cut) => cut.id === id)?.shotJobId, decision: 'manual_review' };
      patchCut(id, (cut) => ({ ...cut, shotQc: acceptance, shotQcReview: 'accepted',
        takeReviewHistory: [...(cut.takeReviewHistory || []), ...(cut.takeReview ? [cut.takeReview] : [])],
        takeReview: { job_id: acceptance.job_id, state: 'accepted', by: 'user', source: 'reviewer_reported' } }));
      await studio().saveDraft();
      await tellRun(id, { shotQc: acceptance, shotQcReview: 'accepted' });
      setBoard({ running: resumeAssembly, qcPausedShotId: null,
        message: resumeAssembly ? `Шот ${index + 1} принят вручную; продолжаю сцену.` : `Шот ${index + 1} принят вручную.` });
    });
    case 'qc-reject': return guarded(async () => {
      const rejection = { job_id: cuts().find((cut) => cut.id === id)?.shotJobId, decision: 'manual_review' };
      patchCut(id, (cut) => ({ ...cut, shotQc: rejection, shotQcReview: 'rejected',
        takeReviewHistory: [...(cut.takeReviewHistory || []), ...(cut.takeReview ? [cut.takeReview] : [])],
        takeReview: { job_id: rejection.job_id, state: 'rejected', by: 'user', source: 'reviewer_reported' } }));
      await studio().saveDraft();
      await tellRun(id, { shotQc: rejection, shotQcReview: 'rejected' });
      setBoard({ running: false, message: `Шот ${index + 1} отклонён. Исправь постановку и создай новый дубль.` });
    });
    case 'qc-retake': return guarded(async () => {
      if (!window.confirm(`Отклонить дуб ${index + 1} и потратить GPU-время на новый дуб только этого шота?`)) return undefined;
      setBoard({ running: false, qcPausedShotId: null, message: `Шот ${index + 1} отклонён; создаю новый дуб только этого шота.` });
      patchCut(id, (cut) => ({ ...cut, shotQcReview: 'rejected' }));
      await studio().saveDraft();
      await runShot(id);
    });
    case 'rerun':
      if (!window.confirm('Переснять шот? Старый ролик останется в истории; следующие шоты-продолжения станут «Устарел».')) return undefined;
      return guarded(() => runShot(id));
    case 'frame-upload': return filePrompt('image/png,image/jpeg,image/webp', false, (files) => guarded(async () => {
      setStartFrame(id, await uploadImage(files[0]), linkMode(cuts()[index]));
    }));
    case 'frame-library': return guarded(() => pickFromLibrary((asset) => setStartFrame(id, asset, linkMode(cuts()[index]))));
    case 'keyframe': {
      const cut = cuts()[index];
      const detail = { cutId: id, index, prompt: cut.prompt || '', startFrameAssetId: cut.startFrame?.asset_id || null,
        references: studio().references().map((ref) => ({ id: ref.id, kind: ref.kind, number: ref.number, role: ref.role, owner: ref.owner || '' })),
        sceneStyle: project().sceneStyle || '', seed: project().seed ?? '' };
      const handled = !window.dispatchEvent(new CustomEvent('h3:storyboard-keyframe', { detail, cancelable: true }));
      say(handled ? `Шот ${index + 1}: ключевой кадр готовится…` : `Генератор ключевых кадров ещё не подключён. Пока загрузи картинку для шота ${index + 1} или возьми её из медиатеки.`);
      return undefined;
    }
    case 'ref-up': case 'ref-down': return guarded(async () => studio().moveReference(id, action === 'ref-up' ? -1 : 1));
    case 'ref-remove': return guarded(async () => studio().removeReference(id));
    default: return undefined;
  }
}

function onChange(event) {
  const { action, id } = event.target.dataset;
  if (action === 'shot-field' && id) {
    const { field } = event.target.dataset;
    const value = event.target.value;
    patchCut(id, (cut) => {
      const sceneShot = { ...(cut.sceneShot || {}) };
      if (value) sceneShot[field] = field === 'lens_mm' ? Number(value) : value;
      else delete sceneShot[field];
      return { ...cut, sceneShot };
    });
    void studio().saveDraft().catch(() => {});
    renderAll(true);
    return;
  }
  if (action === 'shot-action' && id) {
    const cut = cuts().find((item) => item.id === id);
    if (cut?.shotJobId) patchCut(id, (item) => ({ ...item, shotQc: null, shotQcPending: false,
      shotQcError: 'Действие изменено после генерации. Проверка относится к старому замыслу; пересними шот или проверь его заново.' }));
    return;
  }
  if (action === 'prompt' && id) {
    const cut = cuts().find((item) => item.id === id);
    if (cut?.shotJobId) patchCut(id, (item) => ({ ...item, shotQc: null, shotQcPending: false,
      shotQcError: 'Описание изменено после генерации. Проверка относится к старому замыслу; пересними шот или проверь его заново.' }));
    return;
  }
  if (action === 'ref-role') { studio().updateReference(id, { role: event.target.value }); event.target.blur(); renderAll(true); return; }
  if (action === 'ref-owner') { studio().updateReference(id, { owner: event.target.value }); return; }
  if (!id) return;
  if (action === 'continuity') {
    patchCut(id, (cut) => ({ ...cut, startFrame: event.target.checked ? { source: 'previous', asset_id: null } : null }));
    event.target.blur(); renderAll(true);
    void studio().saveDraft().catch(() => {});
    return;
  }
  if (action === 'frame') {
    const value = event.target.value;
    patchCut(id, (cut) => ({ ...cut, startFrame: value === 'none' ? null
      : { source: value, asset_id: value === 'previous' ? null : (cut.startFrame?.source !== 'previous' ? cut.startFrame?.asset_id || null : null) } }));
    event.target.blur(); renderAll(true);
    if (value === 'library' && !cuts().find((cut) => cut.id === id)?.startFrame?.asset_id) say('Загрузи картинку или выбери её из медиатеки');
  }
  if (action === 'colour') { patchCut(id, (cut) => ({ ...cut, colorMatch: event.target.checked })); event.target.blur(); renderAll(true); }
  if (action === 'duration') {
    const seconds = Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(Number(event.target.value) || 5)));
    patchCut(id, (cut) => ({ ...cut, duration: seconds }));
    event.target.blur(); renderAll(true);
  }
}

function onInput(event) {
  const { action, id } = event.target.dataset;
  if (action === 'shot-action' && id) studio().setCutSceneAction(id, event.target.value);
  if (action === 'prompt' && id) studio().setCutPrompt(id, event.target.value);
}

function install() {
  if (new URLSearchParams(window.location.search).get('view') !== 'scenes') return;
  const rail = document.querySelector('#sceneRail');
  if (!rail || document.querySelector('#storyboard')) return;
  rail.classList.add('has-storyboard');
  const head = rail.querySelector('.scene-rail-head p');
  if (head) head.textContent = 'План сцены → раскадровка → короткие генерации H3 → сборка.';
  const z = {};
  z.style = el('input', { id: 'sceneStyle', maxLength: 300, placeholder: 'Например: тёплый пастельный 3D-мультфильм, мягкий дневной свет' });
  z.seed = el('input', { id: 'sceneSeed', type: 'number', min: 0, placeholder: 'случайный' });
  z.bibleList = el('div', { className: 'storyboard-bible-list' });
  z.voices = el('p', { className: 'storyboard-hint' });
  const drop = el('div', { className: 'storyboard-drop', tabIndex: 0 },
    el('span', {}, 'Перетащи фото героя, костюм, место или предмет'),
    el('button', { type: 'button', onclick: () => filePrompt('image/png,image/jpeg,image/webp,audio/*,video/mp4,video/quicktime,video/webm', true, (files) => guarded(() => studio().addFiles(files))) }, 'Загрузить'),
    el('button', { type: 'button', onclick: () => studio().openLibrary() }, 'Из медиатеки'));
  drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('is-over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('is-over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault(); event.stopPropagation(); drop.classList.remove('is-over');
    const files = [...(event.dataTransfer?.files || [])];
    if (files.length) guarded(() => studio().addFiles(files));
  });
  z.style.addEventListener('change', () => { project().sceneStyle = z.style.value.trim(); void studio().saveDraft().catch(() => {}); });
  z.seed.addEventListener('change', () => studio().setSeed(z.seed.value));
  z.bible = el('section', { className: 'storyboard-zone', id: 'scene-setup', 'aria-label': 'Ресурсы этой сцены' },
    el('h3', {}, el('span', { className: 'storyboard-step' }, '2'), 'Материалы сцены'),
    el('p', { className: 'storyboard-hint' }, 'Герои, костюмы, места и предметы, которые можно использовать в любом шоте.'),
    z.bibleList, z.voices,
    el('details', { className: 'scene-asset-options' },
      el('summary', {}, '＋ Добавить и настроить материалы'),
      drop,
      el('div', { className: 'storyboard-row' },
        el('label', { className: 'storyboard-field' }, 'Стиль сцены (добавляется в промпт каждого шота)', z.style),
        el('label', { className: 'storyboard-field storyboard-seed' }, 'Сид сцены (один на все шоты)', z.seed))));
  z.grid = el('div', { className: 'storyboard-grid', role: 'group', 'aria-label': 'Шоты сцены' });
  z.inspector = el('div', { className: 'storyboard-inspector', 'aria-label': 'Выбранный шот' });
  z.timeline = el('section', { className: 'scene-timeline', id: 'scene-timeline', 'aria-label': 'Последовательность шотов' },
    el('div', { className: 'scene-timeline-head' }, el('strong', {}, 'ПОСЛЕДОВАТЕЛЬНОСТЬ'), el('span', { className: 'scene-timeline-total' })),
    el('div', { className: 'scene-timeline-ruler', 'aria-hidden': 'true' }),
    el('div', { className: 'scene-timeline-track' }),
    el('div', { className: 'scene-timeline-lanes' }),
    el('p', {}, 'Выбери шот на таймлайне или доске. Порядок меняется кнопками ↑ ↓ в инспекторе.'));
  const boardZone = el('section', { className: 'storyboard-zone', id: 'scene-board', 'aria-label': 'Раскадровка' },
    el('h3', {}, el('span', { className: 'storyboard-step' }, '3'), 'Раскадровка'),
    el('p', { className: 'storyboard-hint' }, `Выберите шот, чтобы изменить действие, камеру или референсы. Каждый дуб создаётся отдельно (${MIN_SECONDS}–${MAX_SECONDS} сек).`),
    z.grid, z.inspector, z.timeline);
  z.run = el('button', { type: 'button', className: 'storyboard-primary', title: 'Шоты по порядку; продолжение начинается с последнего кадра предыдущего' }, '▶▶ Собрать сцену');
  z.stop = el('button', { type: 'button', hidden: true }, '■ Остановить сборку');
  const glueButton = el('button', { type: 'button', title: 'Склеить готовые шоты по порядку в один MP4 со звуком' }, '⧉ Склеить в ролик');
  const motionButton = el('button', { type: 'button', className: 'k-btn' }, 'Непрерывная сцена · Motion Director');
  motionButton.addEventListener('click', () => openMotionDirectorEditor(studio()).catch(error => window.alert(error.message)));
  z.colourSource = el('select', { 'aria-label': 'Образец цвета' },
    el('option', { value: 'first' }, 'Цвет по кадру шота 1 (эталон сцены)'),
    el('option', { value: 'previous' }, 'Цвет по предыдущему шоту'),
    el('option', { value: 'off' }, 'Без подгонки цвета'));
  z.strength = el('input', { type: 'range', min: 0.2, max: 1, step: 0.1, value: 0.6, 'aria-label': 'Сила подгонки цвета' });
  z.linkPrevious = el('input', { type: 'checkbox', checked: true, 'aria-label': 'Склейки берут окружение из предыдущего шота' });
  z.motionContext = el('input', { type:'checkbox', 'aria-label':'Продолжать по видео и звуку' });
  z.autoKeyframes = el('input', { type: 'checkbox', checked: true, 'aria-label': 'Сборка сама делает ключевые кадры' });
  z.keyframesFirst = el('input', { type: 'checkbox', checked: true, 'aria-label': 'Сначала опорные кадры, видео после их принятия' });
  z.summary = el('p', { className: 'storyboard-summary' });
  z.status = el('p', { className: 'storyboard-status', role: 'status', 'aria-live': 'polite' });
  z.final = el('div', { className: 'storyboard-final' });
  z.run.addEventListener('click', () => guarded(startChain));
  z.stop.addEventListener('click', () => guarded(() => stopRun('Сборка остановлена. Уже отправленные шоты доделаются в очереди.')));
  glueButton.addEventListener('click', () => guarded(glue));
  z.colourSource.addEventListener('change', () => setBoard({ colorSource: z.colourSource.value }));
  z.strength.addEventListener('change', () => setBoard({ strength: Number(z.strength.value) }));
  z.linkPrevious.addEventListener('change', () => setBoard({ linkPrevious: z.linkPrevious.checked }));
  z.motionContext.addEventListener('change', () => setBoard({ motionContext:z.motionContext.checked }));
  z.autoKeyframes.addEventListener('change', () => setBoard({ autoKeyframes: z.autoKeyframes.checked }));
  z.keyframesFirst.addEventListener('change', () => setBoard({ keyframesFirst: z.keyframesFirst.checked }));
  const assembly = el('section', { className: 'storyboard-zone', id: 'scene-assembly', 'aria-label': 'Сборка' },
    el('h3', {}, el('span', { className: 'storyboard-step' }, '4'), 'Сборка'),
    el('div', { className: 'storyboard-controls' }, z.run, z.stop, glueButton, motionButton),
    el('details', { className: 'scene-export-settings' }, el('summary', {}, 'Цвет и параметры'),
      el('div', { className: 'storyboard-controls' }, z.colourSource, el('label', { className: 'storyboard-check' }, 'сила ', z.strength)),
      el('label', { className:'storyboard-check', title:'Только для шотов «Продолжение»: последние 22 кадра и звук принятого дубля; перекрытие удаляется при сборке MP4.' },
        z.motionContext, ' продолжать по видео и звуку · 22 кадра (стык убирает ≈0,92 с)'),
      el('label', { className: 'storyboard-check', title: 'Шот, который не продолжает предыдущий, получает его последний кадр как «Окружение»: та же локация, свет, реквизит' },
        z.linkPrevious, ' склейки берут окружение из последнего кадра предыдущего шота'),
      el('label', { className: 'storyboard-check', title: 'Шот «Ключевой кадр» без картинки: сборка сама заказывает кадр в Qwen-Image-Edit и ждёт его' },
        z.autoKeyframes, ' сборка сама делает недостающие ключевые кадры'),
      el('label', { className: 'storyboard-check', title: 'Композиция до видео: сборка делает опорные кадры всех новых планов и ждёт, пока ты примешь каждый (✓ Принять кадр); видео шота снимается только от принятого кадра' },
        z.keyframesFirst, ' сначала опорные кадры — видео только от принятых'),
),
    z.summary, z.status, z.final);
  boardZone.append(assembly);
  z.filmRun = el('button', { type: 'button', className: 'storyboard-primary', disabled: true }, 'Собрать весь фильм');
  z.filmRun.addEventListener('click', () => { void assembleFilm(); });
  z.filmSummary = el('p', { className: 'film-summary' });
  z.filmIssues = el('p', { className: 'film-issues', role: 'status', 'aria-live': 'polite' });
  z.filmStatus = el('p', { className: 'film-status', role: 'status', 'aria-live': 'polite' });
  z.filmFinal = el('div', { className: 'film-final' });
  z.film = el('section', { className: 'storyboard-zone film-assembly', id: 'film-assembly', 'aria-label': 'Весь фильм' },
    el('div', { className: 'film-heading' }, el('div', {}, el('span', { className: 'create-step' }, 'ФИНАЛЬНАЯ СБОРКА'),
      el('h3', {}, 'Весь фильм'), z.filmSummary), z.filmRun), z.filmIssues, z.filmStatus, z.filmFinal);
  z.editSheet = mountEditSheet(z.film, { studio, jobs: () => jobsById, json, post, el });
  z.root = el('div', { className: 'storyboard', id: 'storyboard' }, z.bible, boardZone, z.film);
  rail.append(z.root);
  zones = z;
  picker = el('dialog', { className: 'scenarist-dialog storyboard-picker' },
    el('h2', {}, 'Картинка из медиатеки'),
    el('p', { className: 'scenarist-hint' }, 'Шот начнётся ровно с этой картинки (кадр 0). Другие шоты её не получат.'),
    el('div', { className: 'storyboard-picker-tools' },
      el('input', { type: 'search', className: 'storyboard-picker-search', placeholder: 'Найти по названию или категории', 'aria-label': 'Поиск стартового кадра' }),
      el('select', { className: 'storyboard-picker-type', 'aria-label': 'Тип стартового кадра' },
        ...START_FRAME_TYPES.map(([value,label]) => el('option', { value }, label))),
      el('span', { className: 'storyboard-picker-count' })),
    el('div', { className: 'storyboard-picker-grid' }),
    el('div', { className: 'scenarist-actions' }, el('button', { type: 'button', onclick: () => picker.close() }, 'Закрыть')));
  document.body.append(picker);
  z.root.addEventListener('click', onClick);
  z.root.addEventListener('change', onChange);
  z.root.addEventListener('input', onInput);
  z.root.addEventListener('focusout', () => setTimeout(() => renderAll(), 0));
  window.addEventListener('h3:scene-active-changed', () => {
    run = null; runSeen = null;
    selectedShotId = cuts()[0]?.id || null;
    selectedShotIds = new Set(selectedShotId ? [selectedShotId] : []);
    renderAll(true);
  });
  window.addEventListener('h3:scenes-changed', () => renderAll(true));
  window.addEventListener('h3:shot-added', (event) => {
    selectedShotId = event.detail?.cutId || cuts().at(-1)?.id || null;
    selectedShotIds = new Set(selectedShotId ? [selectedShotId] : []);
    renderAll(true);
  });
  const loop = async () => {
    if (!studio()?.ready?.()) return;
    await refreshJobs();
    await pullRun().catch(() => {});
    renderAll();
  };
  setInterval(loop, POLL_MS);
  void loop();
}

// Keyframe backend (separate module) answers 'h3:storyboard-keyframe' with this.
window.h3Storyboard = {
  setStartFrame(cutId, asset, source = 'keyframe') { setStartFrame(cutId, asset, source); },
  refresh() { renderAll(true); },
  // ui-bridge.mjs: the Studio agent presses «Собрать сцену» / «Стоп» through the same code as the buttons.
  build: () => startChain({ ask: false }),
  stop: () => stopRun('Сборка остановлена агентом. Уже отправленные шоты доделаются в очереди.'),
  state: () => ({ running: Boolean(board().running), message: board().message || '', qcPausedShotId: board().qcPausedShotId || null,
    onMac: run?.scene_id === sceneId() ? { state: run.state, awaiting: run.awaiting || [] } : null }),
  // Story canvas panel: the shot's staging editor and the user's acceptance of its start frame.
  staging(cutId) {
    const cut = cuts().find((item) => item.id === cutId);
    if (cut) openProductionSpecEditor(cut, { studio: studio(), onSaved: () => renderAll(true) });
  },
  frameAccepted: (cutId) => frameAccepted(cuts().find((cut) => cut.id === cutId), window.h3Studio.references()),
  async acceptFrame(cutId, accept = true) {
    patchCut(cutId, (cut) => ({ ...cut, frameAcceptance: accept ? frameAcceptance(cut, studio().references()) : null }));
    await studio().saveDraft();
    renderAll(true);
    // A scene waiting on the Mac for this frame goes on by itself.
    await tellRun(cutId, { frameAcceptance: cuts().find((cut) => cut.id === cutId)?.frameAcceptance || null });
  },
};

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
}
