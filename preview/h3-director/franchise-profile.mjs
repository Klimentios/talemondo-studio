// «Паспорт франшизы» page: look, camera and edit rules, director defaults and asset roles of one franchise.
// Every story shot of that franchise resolves its faces, costumes, weapons, places and style from here
// (POST /api/franchises/{fid}/resolve-shots); the «Проверить шот» box shows what a shot would get.
import { aspectOptions, loadAspects } from './aspects.mjs?v=20261106-aspects-1';
import { DIRECTOR_SCHEMA, directorFieldLabel, directorFieldOptions } from './director-config.mjs?v=20261007-camera-options-5';
import { ASSET_ROLES, DEFAULT_ROLE, ROLE_KINDS, bibleCards, emptyProfile, newRole, profileBody, splitList, trialShot }
  from './franchise-profile-model.mjs?v=20261106-aspects-1';

const DEFAULT_CATEGORIES = [['style', 'Стиль'], ['lighting', 'Свет'], ['color', 'Цвет'], ['camera', 'Камера'], ['lens', 'Объектив'],
  ['acting', 'Игра']];
const thumb = (id) => `/api/assets/${encodeURIComponent(id)}/thumbnail`;

const state = { franchises: [], fid: '', profile: emptyProfile(), entities: [], assets: [], looks: [], byId: new Map(), dirty: false };
const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'className' || key === 'value' || key === 'checked' || key === 'disabled' || key === 'selected' || key === 'hidden') node[key] = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false && child !== ''));
  return node;
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ') : payload.detail;
    throw Object.assign(new Error(detail || `HTTP ${response.status}`), { status: response.status });
  }
  return payload;
}
const send = (url, method, body) => json(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function say(text, level = '') {
  const status = $('#fpStatus');
  status.textContent = text;
  status.dataset.level = level;
}

function changed() {
  state.dirty = true;
  $('#fpSave').disabled = false;
  say('Есть несохранённые изменения');
}

// --- inputs bound to the profile ----------------------------------------------------------

function text(label, get, set, { area = false, rows = 3, placeholder = '' } = {}) {
  const input = el(area ? 'textarea' : 'input', { className: 'k-field', rows: area ? rows : null, placeholder,
    value: get() ?? '', oninput: (event) => { set(event.target.value); changed(); } });
  return el('label', { className: 'fp-field' }, el('span', {}, label), input);
}

function number(label, get, set, attrs = {}) {
  const input = el('input', { className: 'k-field', type: 'number', value: get() ?? '', ...attrs,
    oninput: (event) => { set(event.target.value === '' ? null : Number(event.target.value)); changed(); } });
  return el('label', { className: 'fp-field fp-field--short' }, el('span', {}, label), input);
}

function select(label, options, get, set, { empty = '' } = {}) {
  const input = el('select', { className: 'k-field', onchange: (event) => { set(event.target.value); changed(); } },
    ...(empty ? [el('option', { value: '' }, empty)] : []),
    ...options.map(([value, title]) => el('option', { value, selected: value === get() }, title)));
  return el('label', { className: 'fp-field' }, el('span', {}, label), input);
}

function checkbox(label, get, set) {
  return el('label', { className: 'fp-check' }, el('input', { type: 'checkbox', checked: Boolean(get()),
    onchange: (event) => { set(event.target.checked); changed(); } }), label);
}

function chips(options, list, title) {
  return el('div', { className: 'fp-chips', role: 'group', 'aria-label': title }, ...options.map(([value, label]) =>
    el('button', { type: 'button', className: 'k-chip', 'aria-pressed': String(list.includes(value)), onclick: (event) => {
      const index = list.indexOf(value);
      if (index >= 0) list.splice(index, 1); else list.push(value);
      event.currentTarget.setAttribute('aria-pressed', String(index < 0));
      changed();
    } }, label)));
}

const schemaOptions = (category, key) => directorFieldOptions(category, key)
  .map((option) => [option.value, directorFieldLabel(category, key, option.value)]);

// --- sections -----------------------------------------------------------------------------

function lookSection(p) {
  return el('section', { className: 'k-panel fp-section' }, el('h2', {}, 'Облик'),
    el('p', { className: 'fp-hint' }, 'Пишется по-английски: эти строки открывают описание облика в каждом промпте франшизы.'),
    text('Стиль', () => p.look.style, (v) => { p.look.style = v; }, { area: true, rows: 3,
      placeholder: 'Premium 3D animated feature look, soft subsurface fur, expressive eyes…' }),
    el('div', { className: 'fp-row' },
      text('Фактура', () => p.look.texture, (v) => { p.look.texture = v; }, { area: true, rows: 2 }),
      text('Палитра', () => p.look.palette, (v) => { p.look.palette = v; }, { area: true, rows: 2 })),
    el('div', { className: 'fp-row' },
      select('Формат', aspectOptions(p.look.aspect_ratio), () => p.look.aspect_ratio, (v) => { p.look.aspect_ratio = v; }),
      text('Язык речи и текста', () => p.look.language, (v) => { p.look.language = v; })));
}

function cameraSection(p) {
  return el('section', { className: 'k-panel fp-section' }, el('h2', {}, 'Камера'),
    el('div', { className: 'fp-row' },
      number('Объектив от, мм', () => p.camera.lens_min, (v) => { p.camera.lens_min = v; }, { min: 8, max: 600 }),
      number('до, мм', () => p.camera.lens_max, (v) => { p.camera.lens_max = v; }, { min: 8, max: 600 }),
      checkbox('Заваленный горизонт допустим', () => p.camera.dutch, (v) => { p.camera.dutch = v; })),
    el('span', { className: 'fp-label' }, 'Язык движения (пусто — любое)'),
    chips(schemaOptions('movement', 'movement'), p.camera.moves, 'Разрешённые движения камеры'),
    text('Почерк камеры (англ.)', () => p.camera.signature, (v) => { p.camera.signature = v; }, { area: true, rows: 2,
      placeholder: 'Motivated moves only; Milo at his eye level; reaction close-ups after every gag' }));
}

function editSection(p) {
  const audio = state.assets.filter((asset) => asset.kind === 'audio')
    .map((asset) => [asset.asset_id, asset.original_name || asset.asset_id.slice(0, 8)]);
  return el('section', { className: 'k-panel fp-section' }, el('h2', {}, 'Монтаж'),
    el('span', { className: 'fp-label' }, 'Палитра переходов (пусто — любые)'),
    chips(schemaOptions('shot', 'transition_in'), p.edit.transitions, 'Переходы франшизы'),
    el('div', { className: 'fp-row' },
      select('Фирменный переход', schemaOptions('shot', 'transition_in'), () => p.edit.signature_transition,
        (v) => { p.edit.signature_transition = v; }, { empty: 'Нет' }),
      number('Шот от, с', () => p.edit.min_shot, (v) => { p.edit.min_shot = v ?? 1; }, { min: 0.2, max: 60, step: 0.1 }),
      number('до, с', () => p.edit.max_shot, (v) => { p.edit.max_shot = v ?? 15; }, { min: 0.5, max: 60, step: 0.1 })),
    el('div', { className: 'fp-row' },
      select('Музыка по умолчанию', audio, () => p.edit.music_asset_id, (v) => { p.edit.music_asset_id = v; }, { empty: 'Без музыки' }),
      number('Громкость', () => p.edit.music_volume, (v) => { p.edit.music_volume = v ?? 0.35; }, { min: 0, max: 2, step: 0.05 }),
      number('Из чёрного, с', () => p.edit.fade_in, (v) => { p.edit.fade_in = v ?? 0; }, { min: 0, max: 3, step: 0.1 }),
      number('В чёрное, с', () => p.edit.fade_out, (v) => { p.edit.fade_out = v ?? 0; }, { min: 0, max: 5, step: 0.1 })),
    el('div', { className: 'fp-row' },
      select('Цвет франшизы (лук)', state.looks.map((look) => [look.look, look.description]), () => p.edit.look,
        (v) => { p.edit.look = v; }, { empty: 'Без лука' }),
      number('Сила лука', () => p.edit.look_intensity, (v) => { p.edit.look_intensity = v ?? 0.6; }, { min: 0, max: 1, step: 0.05 }),
      checkbox('Выравнивать цвет шотов по первому', () => p.edit.match_color !== false, (v) => { p.edit.match_color = v; })));
}

function directorSection(p) {
  const groups = DEFAULT_CATEGORIES.map(([category, title]) => {
    const fields = (DIRECTOR_SCHEMA.categories[category] || []).filter((field) => field.type === 'enum');
    const values = p.director_defaults[category] ||= {};
    const set = Object.values(values).filter(Boolean).length;
    return el('details', { className: 'fp-defaults', open: set > 0 },
      el('summary', {}, title, set ? el('small', {}, ` · задано: ${set}`) : null),
      el('div', { className: 'fp-grid' }, ...fields.map((field) => select(field.label?.ru || field.key,
        schemaOptions(category, field.key), () => values[field.key] || '',
        (v) => { if (v) values[field.key] = v; else delete values[field.key]; }, { empty: '—' }))));
  });
  return el('section', { className: 'k-panel fp-section' }, el('h2', {}, 'Режиссура по умолчанию'),
    el('p', { className: 'fp-hint' }, 'Подставляется в каждый шот франшизы; поля шота (крупность, ракурс, переход) задаёт сам шот.'),
    ...groups);
}

// --- roles --------------------------------------------------------------------------------

function assetPicker(onPick) {
  const dialog = el('dialog', { className: 'fp-picker k-panel' });
  const search = el('input', { className: 'k-field', type: 'search', placeholder: 'Название файла или роль' });
  const grid = el('div', { className: 'fp-picker-grid' });
  const render = () => {
    const needle = search.value.trim().toLowerCase();
    const found = state.assets.filter((asset) => !needle || `${asset.original_name} ${asset.role}`.toLowerCase().includes(needle)).slice(0, 120);
    grid.replaceChildren(...found.map((asset) => el('button', { type: 'button', className: 'fp-pick', title: asset.original_name || '',
      onclick: () => { dialog.close(); dialog.remove(); onPick(asset); } },
    asset.kind === 'image' ? el('img', { src: thumb(asset.asset_id), alt: '', loading: 'lazy' }) : el('span', { className: 'fp-pick-kind' }, asset.kind === 'audio' ? '♫' : '▶'),
    el('small', {}, asset.original_name || asset.asset_id.slice(0, 8)), el('small', {}, asset.role || ''))));
  };
  search.addEventListener('input', render);
  dialog.append(el('h2', {}, 'Файл из библиотеки'), search, grid,
    el('button', { type: 'button', className: 'k-btn', onclick: () => { dialog.close(); dialog.remove(); } }, 'Закрыть'));
  document.body.append(dialog);
  render();
  dialog.showModal();
  search.focus();
}

function roleCard(p, role, index) {
  const assets = el('div', { className: 'fp-assets' }, ...role.assets.map((item, assetIndex) => {
    const asset = state.byId.get(item.asset_id);
    const kind = asset?.kind || 'image';
    return el('div', { className: 'fp-asset' },
      kind === 'image' ? el('img', { src: thumb(item.asset_id), alt: '', loading: 'lazy' }) : el('span', { className: 'fp-pick-kind' }, kind === 'audio' ? '♫' : '▶'),
      el('select', { className: 'k-field', 'aria-label': 'Роль файла', onchange: (event) => { item.role = event.target.value; changed(); } },
        el('option', { value: '' }, `${DEFAULT_ROLE[role.kind]} (по умолчанию)`),
        ...(ASSET_ROLES[kind] || []).map((name) => el('option', { value: name, selected: item.role === name }, name))),
      asset ? null : el('small', { className: 'fp-bad' }, 'нет в библиотеке'),
      el('button', { type: 'button', className: 'k-btn k-btn--ghost k-btn--sm', 'aria-label': 'Убрать файл',
        onclick: () => { role.assets.splice(assetIndex, 1); changed(); renderRoles(p); } }, '×'));
  }), el('button', { type: 'button', className: 'k-btn k-btn--sm', disabled: role.assets.length >= 8,
    onclick: () => assetPicker((asset) => { role.assets.push({ asset_id: asset.asset_id, role: '' }); changed(); renderRoles(p); }) }, '＋ Файл'));
  const ownerKinds = ['costume', 'weapon', 'prop', 'voice', 'fantasy'];
  return el('article', { className: 'fp-role' },
    el('div', { className: 'fp-row' },
      text('Название', () => role.name, (v) => { role.name = v; }),
      select('Тип', ROLE_KINDS, () => role.kind, (v) => { role.kind = v; renderRoles(p); }),
      text('Другие имена (через запятую)', () => role.aliases.join(', '), (v) => { role.aliases = splitList(v); }),
      ownerKinds.includes(role.kind) ? text('Чей', () => role.owner, (v) => { role.owner = v; }) : null),
    el('div', { className: 'fp-row fp-row--checks' },
      ownerKinds.includes(role.kind) ? checkbox('Всегда вместе с владельцем', () => role.with_owner, (v) => { role.with_owner = v; }) : null,
      checkbox('В каждом шоте франшизы', () => role.always, (v) => { role.always = v; }),
      el('button', { type: 'button', className: 'k-btn k-btn--danger k-btn--sm', onclick: () => {
        if (!window.confirm(`Удалить роль «${role.name || 'без названия'}»?`)) return;
        p.roles.splice(index, 1); changed(); renderRoles(p);
      } }, 'Удалить роль')),
    text('Строка в промпт, когда роль в шоте (англ.)', () => role.prompt, (v) => { role.prompt = v; }),
    assets);
}

function renderRoles(p) {
  const box = $('#fpRoles');
  const auto = bibleCards(state.entities, p);
  box.replaceChildren(
    el('div', { className: 'fp-head' }, el('h2', {}, 'Роли ассетов'),
      el('div', { className: 'fp-row' }, ...ROLE_KINDS.slice(0, 6).map(([kind, label]) => el('button', { type: 'button', className: 'k-btn k-btn--sm',
        onclick: () => { p.roles.push(newRole(kind)); changed(); renderRoles(p); box.querySelector('.fp-role:last-of-type input')?.focus(); } }, `＋ ${label}`)))),
    el('p', { className: 'fp-hint' }, 'Шот получает роль, когда её имя есть в героях, названии или описании шота. ',
      auto.length ? `Из библии подтянутся сами: ${auto.map((card) => card.title.split('·')[0].trim()).join(', ')}.` : ''),
    ...p.roles.map((role, index) => roleCard(p, role, index)));
}

// --- try a shot ---------------------------------------------------------------------------

async function tryShot() {
  const out = $('#fpTrialOut');
  if (state.dirty) say('Проверка идёт по сохранённому паспорту — сохрани изменения, чтобы они учитывались', 'warn');
  out.replaceChildren(el('p', { className: 'fp-hint' }, 'Проверяю…'));
  try {
    const result = await send(`/api/franchises/${state.fid}/resolve-shots`, 'POST',
      { shots: [trialShot($('#fpTrialCast').value, $('#fpTrialText').value)] });
    const shot = result.shots[0];
    out.replaceChildren(...[
      el('div', { className: 'fp-assets' }, ...shot.asset_ids.map((id) => el('div', { className: 'fp-asset' },
        state.byId.get(id)?.kind === 'image' ? el('img', { src: thumb(id), alt: '' }) : el('span', { className: 'fp-pick-kind' }, '♫'),
        el('small', {}, shot.reference_roles[id]), shot.reference_owners[id] ? el('small', {}, shot.reference_owners[id]) : null))),
      shot.asset_ids.length ? null : el('p', { className: 'fp-hint' }, 'Ни одной роли — проверь имена или добавь псевдонимы.'),
      shot.missing.length ? el('p', { className: 'fp-bad' }, `Не найдены в паспорте и библии: ${shot.missing.join(', ')}`) : null,
      ...shot.notes.map((note) => el('p', { className: 'fp-bad' }, note)),
      shot.look.length ? el('pre', { className: 'fp-look' }, shot.look.join('\n')) : null,
      el('p', { className: 'fp-hint' }, `Режиссура: ${Object.entries(shot.director_config).map(([category, entry]) =>
        `${category}: ${Object.entries(entry.parameters).map(([k, v]) => `${k}=${v}`).join(', ')}`).join(' · ') || 'не задана'}`)]
      .filter(Boolean));
  } catch (error) {
    out.replaceChildren(el('p', { className: 'fp-bad' }, error.message));
  }
}

// --- load / save --------------------------------------------------------------------------

function render() {
  const p = state.profile;
  $('#fpSections').replaceChildren(lookSection(p), cameraSection(p), editSection(p), directorSection(p));
  renderRoles(p);
  $('#fpNotes').replaceChildren(text('Заметки команды', () => p.notes, (v) => { p.notes = v; }, { area: true, rows: 3 }));
  $('#fpSave').disabled = !state.dirty;
  $('#fpRevision').textContent = p.revision ? `версия ${p.revision}` : 'ещё не сохранён';
}

async function load(fid) {
  if (state.dirty && !window.confirm('Есть несохранённые изменения. Перейти без сохранения?')) {
    $('#fpFranchise').value = state.fid;
    return;
  }
  state.fid = fid;
  history.replaceState(null, '', `?fid=${encodeURIComponent(fid)}`);
  say('Загружаю паспорт…');
  const [profile, entities] = await Promise.all([json(`/api/franchises/${fid}/profile`), json(`/api/franchises/${fid}/entities`)]);
  state.profile = { ...emptyProfile(), ...profile };
  state.entities = entities;
  state.dirty = false;
  render();
  say(profile.revision ? '' : 'Паспорт пуст: заполни облик, правила и роли и нажми «Сохранить».');
}

async function save() {
  try {
    const saved = await send(`/api/franchises/${state.fid}/profile`, 'PUT', profileBody(state.profile));
    state.profile = { ...emptyProfile(), ...saved };
    state.dirty = false;
    render();
    say(`Сохранено · версия ${saved.revision}`, 'good');
  } catch (error) {
    say(error.status === 409 ? 'Паспорт изменили в другом месте (например, агент из чата). Скопируй правки, перезагрузи страницу и повтори.'
      : `Не сохранено: ${error.message}`, 'bad');
  }
}

async function start() {
  try {
    [state.franchises, state.assets] = await Promise.all([json('/api/franchises'), json('/api/assets')]);
    state.looks = await json('/api/edl/toolkit').then((kit) => kit.looks || []).catch(() => []);
  } catch (error) {
    say(`Studio недоступна: ${error.message}`, 'bad');
    return;
  }
  state.byId = new Map(state.assets.map((asset) => [asset.asset_id, asset]));
  const wanted = new URLSearchParams(location.search).get('fid');
  const picker = $('#fpFranchise');
  picker.replaceChildren(...state.franchises.map((f) => el('option', { value: f.id, selected: f.id === wanted }, f.name)));
  picker.addEventListener('change', () => { void load(picker.value).catch((error) => say(error.message, 'bad')); });
  $('#fpSave').addEventListener('click', () => { void save(); });
  $('#fpTrial').addEventListener('click', () => { void tryShot(); });
  if (!state.franchises.length) {
    // A fresh Studio: nothing to edit until a franchise exists (created on the franchise board).
    for (const id of ['#fpSave', '#fpTrial', '#fpFranchise']) $(id).disabled = true;
    say('Франшиз пока нет. Создай первую на доске франшиз («Франшизы» → «＋ Франшиза»), затем вернись к паспорту.', 'warn');
    return;
  }
  window.addEventListener('beforeunload', (event) => { if (state.dirty) event.preventDefault(); });
  await load(picker.value).catch((error) => say(error.message, 'bad'));
  // The format list comes from the GPU (/api/studio/capabilities); redraw once it is known, keeping edits in state.
  void loadAspects().then(() => { if (state.fid) render(); });
}

// The page-boot cover waits for the first render, so the page does not grow in front of the user.
window.studioBoot?.hold(start());
