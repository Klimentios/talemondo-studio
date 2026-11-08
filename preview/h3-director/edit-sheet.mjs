// «Монтажный лист» under «Весь фильм»: transition per seam, music, fades → check → assemble with transitions.
// Server: local_studio/edl.py (/api/edl/plan, /api/edl/renders). Settings and the last assembly live in
// project().editSheet, so the saved draft keeps the MP4 out of retention pruning.
import { ACCENT_NAMES, EFFECT_NAMES, GRAPHIC_NAMES, buildSheet, planNotes, sheetRows, sheetSettings, transitionOptions } from './edit-sheet-model.mjs?v=20261106-aspects-1';
import { aspectOptions, loadAspects } from './aspects.mjs?v=20261106-aspects-1';
import { directorFieldLabel } from './director-config.mjs?v=20261007-camera-options-5';

const POLL_MS = 1500;

export function mountEditSheet(container, { studio, jobs, json, post, el }) {
  const project = () => studio().project();
  const settings = () => sheetSettings(project());
  let defaults = {};
  let effects = [];
  let graphicsReady = false;
  let looks = [];
  let colorNotes = [];
  let audio = [];
  let plan = null;
  let busy = false;
  let signature = '';

  const z = {};
  z.status = el('p', { className: 'edit-sheet-status', role: 'status', 'aria-live': 'polite' });
  z.rows = el('div', { className: 'edit-sheet-rows' });
  z.options = el('div', { className: 'edit-sheet-options' });
  z.notes = el('ul', { className: 'edit-sheet-notes' });
  z.issues = el('p', { className: 'film-issues edit-sheet-issues' });
  z.final = el('div', { className: 'film-final edit-sheet-final' });
  z.check = el('button', { type: 'button', className: 'storyboard-secondary' }, 'Проверить монтаж');
  z.render = el('button', { type: 'button', className: 'storyboard-primary' }, 'Собрать с переходами');
  z.export = el('button', { type: 'button', className: 'storyboard-secondary',
    title: 'Таймлайн с исходниками, титрами, звуками и LUT лука: Premiere (XML) и DaVinci Resolve / Final Cut (FCPXML)' },
  'Экспорт в Premiere / DaVinci');
  z.exported = el('div', { className: 'edit-sheet-export' });
  z.root = el('details', { className: 'edit-sheet', open: true },
    el('summary', {}, el('strong', {}, 'Монтажный лист'), el('span', {}, 'переходы, акценты, титры, цвет, звук, музыка')),
    z.options, z.rows, z.issues, el('div', { className: 'edit-sheet-actions' }, z.check, z.render, z.export), z.status,
    z.notes, z.exported, z.final);
  container.append(z.root);

  async function save(patch) {
    project().editSheet = { ...settings(), ...patch };
    plan = null;
    colorNotes = [];
    await studio().saveDraft();
    render(true);
  }

  function setTransition(cutId, patch) {
    const transitions = { ...settings().transitions };
    const next = { ...(transitions[cutId] || {}), ...patch };
    if (!next.type && next.seconds == null && !next.effect && !next.accent && !next.freeze && !next.exposure && !next.graphic?.template) delete transitions[cutId];
    else transitions[cutId] = next;
    void save({ transitions });
  }

  function current() {
    return buildSheet(studio().sceneState().scenes, jobs(), settings());
  }

  async function check() {
    const sheet = current();
    if (!sheet.ready) throw new Error(sheet.issues.slice(0, 3).join(' · '));
    z.status.textContent = 'Проверяю монтаж по готовым роликам…';
    plan = await post('/api/edl/plan', sheet.body);
    const errors = planNotes(plan).filter((note) => note.level === 'error').length;
    z.status.textContent = `Монтаж: ${plan.total.toFixed(1)} с, ${plan.clips.length} шотов` +
      (errors ? ` · операторских ошибок: ${errors}` : ' · ошибок нет');
    return sheet;
  }

  async function assemble() {
    const sheet = await check();
    z.status.textContent = 'Собираю фильм с переходами…';
    const started = await post('/api/edl/renders', sheet.body);
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      const state = await json(`/api/edl/renders/${started.render_id}`);
      if (state.state === 'running') continue;
      if (state.state !== 'completed') throw new Error(`Сборка не удалась: ${state.detail || state.state}`);
      colorNotes = (state.color_qc || []).flatMap((item) => item.notes.map((note) => ({ level: 'warning', text: `Цвет, шот ${item.clip}: ${note}` })));
      project().editSheet = { ...settings(), lastRender: { render_id: started.render_id, url: state.url,
        duration: state.duration, lufs: state.loudness?.input_lufs ?? null, job_ids: sheet.body.clips.map((clip) => clip.job_id), sheet: JSON.stringify(sheet.body) } };
      await studio().saveDraft();
      z.status.textContent = `Фильм собран с переходами: ${Number(state.duration).toFixed(1)} с.`;
      return;
    }
  }

  async function run(action) {
    if (busy) return;
    busy = true;
    render(true);
    try { await action(); } catch (error) { z.status.textContent = error.message || 'Не получилось'; } finally {
      busy = false;
      render(true);
    }
  }
  async function exportTimeline() {
    const sheet = current();
    if (!sheet.ready) throw new Error(sheet.issues.slice(0, 3).join(' · '));
    z.status.textContent = 'Готовлю таймлайн для Premiere и DaVinci: исходники, титры, звуки, LUT…';
    const done = await post('/api/edl/exports', sheet.body);
    const resolve = el('button', { type: 'button', className: 'storyboard-secondary' }, 'Открыть в DaVinci Resolve');
    resolve.addEventListener('click', () => { void run(async () => {
      z.status.textContent = 'Импортирую таймлайн в открытый DaVinci Resolve…';
      const result = await post(`/api/edl/exports/${done.export_id}/resolve`, {});
      z.status.textContent = `DaVinci Resolve: таймлайн «${result.timeline}» в проекте «${result.project}».`;
    }); });
    const reveal = el('button', { type: 'button', className: 'storyboard-secondary' }, 'Показать папку');
    reveal.addEventListener('click', () => { void post(`/api/edl/exports/${done.export_id}/reveal`, {}).catch(() => {}); });
    z.exported.replaceChildren(el('p', {}, `Экспорт готов: ${done.media} файлов, маркеров ${done.markers}` +
      (done.lut ? ', лук в look.cube' : '') + '. Premiere: Файл → Импорт → STUDIO.xml. Resolve/Final Cut: STUDIO.fcpxml.'),
    el('div', { className: 'edit-sheet-actions' }, resolve, reveal,
      el('a', { href: done.zip, download: '', className: 'storyboard-secondary' }, '↓ Скачать ZIP')));
    z.status.textContent = 'Таймлайн для монтажных программ готов.';
  }
  z.export.addEventListener('click', () => { void run(exportTimeline); });
  z.check.addEventListener('click', () => { void run(check); });
  z.render.addEventListener('click', () => { void run(assemble); });

  function lookSelect(state) {
    const select = el('select', { 'aria-label': 'Цвет фильма' }, el('option', { value: '' }, 'Без лука'),
      ...looks.map((look) => el('option', { value: look.look, selected: look.look === state.look, title: look.description },
        look.description.split(':')[0])));
    select.addEventListener('change', () => { void save({ look: select.value }); });
    return el('label', {}, 'Цвет', select);
  }

  function check(label, value, onChange, title) {
    const input = el('input', { type: 'checkbox', checked: value });
    input.addEventListener('change', () => { void onChange(input.checked); });
    return el('label', { className: 'edit-sheet-check', title }, input, label);
  }

  function number(label, value, onChange, attrs = {}) {
    const input = el('input', { type: 'number', value: String(value), step: '0.1', min: '0', ...attrs });
    input.addEventListener('change', () => onChange(input.value === '' ? null : Number(input.value)));
    return el('label', {}, label, input);
  }

  function renderOptions(state) {
    const format = el('select', { 'aria-label': 'Формат' },
      ...aspectOptions(state.aspect_ratio).map(([value, label]) =>
        el('option', { value, selected: state.aspect_ratio === value }, label)));
    format.addEventListener('change', () => { void save({ aspect_ratio: format.value }); });
    const music = el('select', { 'aria-label': 'Музыка' }, el('option', { value: '' }, 'Без музыки'),
      ...audio.map((asset) => el('option', { value: asset.asset_id, selected: asset.asset_id === state.music_asset_id },
        asset.original_name || asset.asset_id.slice(0, 8))));
    music.addEventListener('change', () => { void save({ music_asset_id: music.value }); });
    const duck = el('input', { type: 'checkbox', checked: state.music_duck !== false, disabled: !state.music_asset_id });
    duck.addEventListener('change', () => { void save({ music_duck: duck.checked }); });
    z.options.replaceChildren(
      el('label', {}, 'Формат', format),
      el('label', {}, 'Музыка', music),
      number('Громкость музыки', state.music_volume, (value) => save({ music_volume: value ?? 0.35 }),
        { max: '2', step: '0.05', disabled: !state.music_asset_id }),
      el('label', { className: 'edit-sheet-check' }, duck, 'Тише под речь'),
      check('Звуковой дизайн', state.auto_sfx !== false, (value) => save({ auto_sfx: value }),
        'Свист на whip pan, удар на smash cut, нарастание к удару, затвор на стоп-кадре — встроенная библиотека звуков'),
      check('Громкость −14 LUFS', state.loudness !== false, (value) => save({ loudness: value }),
        'Выровнять итог по EBU R128 для TikTok/Reels, пик −1 dBTP'),
      check('Плёнка', Boolean(state.film_look), (value) => save({ film_look: value }), 'Лёгкое зерно и виньетка'),
      lookSelect(state),
      number('Сила лука', state.look_intensity ?? 0.6, (value) => save({ look_intensity: value ?? 0.6 }),
        { max: '1', step: '0.05', disabled: !state.look }),
      check('Выровнять цвет шотов', state.match_color !== false, (value) => save({ match_color: value }),
        'Подтянуть цвет каждого шота к первому: H3-шоты расходятся по тону'),
      number('Из чёрного, с', state.fade_in, (value) => save({ fade_in: value ?? 0 }), { max: '3' }),
      number('В чёрное, с', state.fade_out, (value) => save({ fade_out: value ?? 0 }), { max: '5' }));
  }

  function renderRows(state) {
    const options = transitionOptions();
    const timings = new Map((plan?.clips || []).map((clip) => [clip.index, clip]));
    const rows = sheetRows(studio().sceneState().scenes, state);
    z.rows.replaceChildren(el('div', { className: 'edit-sheet-row edit-sheet-head' },
      el('span', {}, '№'), el('span', {}, 'Шот'), el('span', {}, 'Вход'), el('span', {}, 'Длина перехода'), el('span', {}, 'На таймлайне')),
    ...rows.map((row) => {
      const select = el('select', { 'aria-label': `Вход в шот ${row.number}` },
        ...options.map((option) => el('option', { value: option.value, selected: option.value === row.transition },
          directorFieldLabel('shot', 'transition_in', option.value))));
      select.addEventListener('change', () => setTransition(row.cutId, { type: select.value }));
      const auto = row.auto ? el('small', {}, 'из плана') : null;
      const fallback = defaults[row.transition]?.seconds ?? 0;
      const seconds = el('input', { type: 'number', min: '0', max: '3', step: '0.05', value: row.seconds ?? '',
        placeholder: String(fallback), 'aria-label': `Длина перехода шота ${row.number}`,
        disabled: !fallback && row.transition !== 'sound_bridge' });
      seconds.addEventListener('change', () => setTransition(row.cutId, { seconds: seconds.value === '' ? null : Number(seconds.value) }));
      const timing = timings.get(row.number);
      const look = el('select', { 'aria-label': `Вид перехода шота ${row.number}`, disabled: row.number === 1 },
        el('option', { value: '' }, 'Вид: по переходу'),
        ...effects.map((name) => el('option', { value: name, selected: name === row.effect }, EFFECT_NAMES[name] || name)));
      look.addEventListener('change', () => setTransition(row.cutId, { effect: look.value }));
      const accent = el('select', { 'aria-label': `Акцент шота ${row.number}` }, el('option', { value: '' }, 'Без акцента'),
        ...Object.entries(ACCENT_NAMES).map(([value, label]) => el('option', { value, selected: value === row.accent }, label)));
      accent.addEventListener('change', () => setTransition(row.cutId, { accent: accent.value }));
      const freeze = el('input', { type: 'number', min: '0', max: '4', step: '0.1', value: row.freeze || '', placeholder: '0',
        'aria-label': `Стоп-кадр в конце шота ${row.number}, с` });
      freeze.addEventListener('change', () => setTransition(row.cutId, { freeze: freeze.value === '' ? 0 : Number(freeze.value) }));
      const exposure = el('input', { type: 'number', min: '-2', max: '2', step: '0.1', value: row.exposure || '', placeholder: '0',
        'aria-label': `Яркость шота ${row.number}, стопы` });
      exposure.addEventListener('change', () => setTransition(row.cutId, { exposure: exposure.value === '' ? 0 : Number(exposure.value) }));
      return el('div', { className: 'edit-sheet-row' },
        el('span', {}, String(row.number).padStart(2, '0')),
        el('span', { className: 'edit-sheet-label' }, row.label, el('small', {}, `${row.planned} с по плану`)),
        el('span', {}, select, auto), el('span', {}, seconds),
        el('span', { className: 'edit-sheet-time' }, timing ? `${timing.start.toFixed(2)}–${timing.end.toFixed(2)} с` : '—'),
        el('span', { className: 'edit-sheet-fx' }, look, accent, el('label', {}, 'Стоп-кадр, с', freeze),
          el('label', { title: 'Экспозиция шота в стопах: + светлее, − темнее' }, 'Яркость', exposure)),
        titleControls(row));
    }));
    if (!rows.length) z.rows.append(el('p', { className: 'film-issues' }, 'Добавь шоты в сцены, и здесь появится монтажный лист.'));
  }

  function titleControls(row) {
    const current = row.graphic || {};
    const kind = el('select', { 'aria-label': `Титр шота ${row.number}`, disabled: !graphicsReady,
      title: graphicsReady ? '' : 'Моушен-графика не установлена (tools/motion)' }, el('option', { value: '' }, 'Без титра'),
      ...Object.entries(GRAPHIC_NAMES).map(([value, label]) => el('option', { value, selected: value === current.template }, label)));
    const text = el('input', { type: 'text', maxLength: 120, value: current.text || '', placeholder: 'Текст (английский)',
      'aria-label': `Текст титра шота ${row.number}` });
    const line = el('input', { type: 'text', maxLength: 160, value: current.line || '', placeholder: 'Вторая строка',
      'aria-label': `Вторая строка титра шота ${row.number}` });
    const commit = () => setTransition(row.cutId, { graphic: kind.value ? { template: kind.value, text: text.value, line: line.value } : null });
    kind.addEventListener('change', commit);
    text.addEventListener('change', commit);
    line.addEventListener('change', commit);
    return el('span', { className: 'edit-sheet-fx edit-sheet-title' }, kind, current.template ? text : null,
      current.template && current.template !== 'KineticTitle' ? line : null);
  }

  function renderResult(state) {
    z.notes.replaceChildren(...[...planNotes(plan), ...colorNotes].slice(0, 14).map((note) =>
      el('li', { className: `is-${note.level}` }, note.text)));
    const last = state.lastRender;
    const sheet = current();
    z.final.replaceChildren();
    if (last?.url) {
      const fresh = sheet.ready && last.sheet === JSON.stringify(sheet.body);
      z.final.append(el('p', { className: `film-version${fresh ? '' : ' is-old'}` },
        fresh ? `Текущая версия фильма${last.lufs != null ? ` · громкость выровнена (было ${Number(last.lufs).toFixed(1)} LUFS)` : ''}`
        : 'Собрано по прежнему листу. Для обновления собери ещё раз.'),
      el('video', { src: last.url, controls: true, preload: 'metadata', playsInline: true }),
      el('a', { href: last.url, download: 'studio-film-edit.mp4' }, `↓ Скачать MP4 · ${Number(last.duration).toFixed(1)} с`));
    }
    z.issues.textContent = sheet.ready ? '' : `Собрать можно, когда готовы все шоты: ${sheet.issues.slice(0, 3).join(' · ')}` +
      (sheet.issues.length > 3 ? ` · и ещё ${sheet.issues.length - 3}` : '');
    z.check.disabled = busy || !sheet.ready;
    z.render.disabled = busy || !sheet.ready;
    z.export.disabled = busy || !sheet.ready;
  }

  function render(force = false) {
    if (!studio()?.ready?.()) return;
    const state = settings();
    const scenes = studio().sceneState().scenes;
    const next = JSON.stringify([state, scenes.map((scene) => [scene.title, (scene.cuts || []).map((cut) =>
      [cut.id, cut.shotJobId, cut.duration, cut.sceneShot?.transition_in, cut.sceneShot?.transition_out])]),
    buildSheet(scenes, jobs(), state).ready, audio.length, Object.keys(defaults).length, effects.length, graphicsReady, busy, plan?.total]);
    if (!force && next === signature) return;
    if (!force && z.root.contains(document.activeElement) && ['INPUT', 'SELECT'].includes(document.activeElement.tagName)) return;
    signature = next;
    renderOptions(state);
    renderRows(state);
    renderResult(state);
  }

  void loadAspects().then(() => render(true));
  json('/api/edl/toolkit').then((kit) => { defaults = kit.transitions; effects = kit.effects; graphicsReady = Boolean(kit.graphics_ready); looks = kit.looks || []; render(true); })
    .catch(() => json('/api/edl/transitions').then((table) => { defaults = table; render(true); }).catch(() => {}));
  json('/api/assets').then((assets) => {
    audio = assets.filter((asset) => asset.kind === 'audio');
    render(true);
  }).catch(() => {});
  return { render };
}
