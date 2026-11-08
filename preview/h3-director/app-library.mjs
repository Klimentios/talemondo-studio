// Assets: upload, classification, library dialog, results and stock pictures.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { installStockLibrary } from './stock-library.mjs?v=20261005-4';
import { collapseLibraryCopies, inferredLibraryCategory, inferredLibraryType } from './library-quality.mjs?v=20261024-1';
import { characterReferenceSet, matchesHierarchy } from './library-hierarchy.mjs?v=20261022-1';
import { COLLECTIONS, inferredCollection, sectionFor } from './library-collections.mjs?v=20261030-library-roles-1';
import { assetsWorkspace, closeAssetLibrary, showAssetLibrary } from './assets-workspace.mjs?v=20261102-talemondo-1';
import { sendJobToStory } from './story-handoff.mjs?v=20261013-link';
import { renderAssetResults } from './assets-results.mjs?v=20261008-titles-1';
import { addReference, mediaKind } from './model.mjs?v=20261106-refmatch';
import { apiReady, franchiseMemberships } from './app-bootstrap.mjs?v=20261106-refmatch';
import { activeCut } from './app-direction.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { knownJobs } from './app-queue.mjs?v=20261106-refmatch';
import { renderCuts, renderReferences } from './app-references.mjs?v=20261108-clean';
import { $, allAssets, audioRoles, files, imageRoles, libraryDefaults, librarySelected, libraryTypes, referenceLimits, showToast, stockByFilename, videoRoles } from './app-shared.mjs?v=20261106-refmatch';

let libraryVisibleCount = 48;
let libraryManageMode = false;
let showHiddenAssets = false;
let showLibraryCopies = false;
let libraryType = 'all';
let libraryCollection = ''; // '' = every file; a franchise id narrows the library to that franchise
let librarySection = '';
let libraryResultLimit = 12;
let libraryResultsSignature = '';
let stockLibrary;

export async function addFiles(incoming, forcedRole = null, options = {}) {
  const attach = options.attach !== false;
  let added = 0;
  let unsupported = 0;
  for (const file of incoming) {
    const kind = mediaKind(file);
    if (!kind || (!options.allowMixed && ['Окружение', 'Лицо героя', 'Костюм'].includes(forcedRole) && kind !== 'image')) { unsupported++; continue; }
    const limit = referenceLimits[kind];
    if (attach && (appState.project.references.filter((ref) => ref.kind === kind).length >= limit || appState.project.references.length >= 12)) {
      showToast(appState.project.references.length >= 12 ? 'Достигнут общий лимит: 12 файлов' : `Достигнут лимит: ${limit} файлов типа ${kind}`);
      continue;
    }
    const allowed = kind === 'image' ? imageRoles : kind === 'video' ? videoRoles : audioRoles;
    const role = forcedRole && allowed.includes(forcedRole) ? forcedRole : options.type === 'characters' && kind === 'image' ? 'Лицо героя' : 'Авто — определить по содержимому';
    let id = crypto.randomUUID();
    let url = URL.createObjectURL(file);
    if (apiReady) {
      try {
        const data = new FormData();
        data.append('file', file, file.name || `buffer-${kind}`);
        const response = await fetch(`/api/assets?role=${encodeURIComponent(role)}`, { method: 'POST', body: data });
        const asset = await response.json();
        if (!response.ok) throw new Error(asset.detail || 'Ошибка импорта');
        id = asset.asset_id;
        allAssets.set(id, asset);
        URL.revokeObjectURL(url);
        url = `/api/assets/${encodeURIComponent(id)}/file`;
      } catch (error) {
        URL.revokeObjectURL(url);
        showToast(`Не удалось сохранить ${file.name}: ${error.message}`);
        continue;
      }
    }
    if (apiReady) {
      appState.project.library ||= {};
      appState.project.library[id] = { ...options.provenance, name: options.characterName ? `${options.characterName} · ${file.name}` : (file.name || `Буфер ${kind}`), category: String(options.category || (options.characterName ? `Персонажи / ${options.characterName}` : libraryDefaults[kind])).trim().slice(0, 80), type: options.type || (kind === 'video' ? 'motion' : 'other'), characterName: options.characterName || '' };
    }
    if (attach) {
      files.set(id, { file: apiReady ? null : file, url });
      appState.project = addReference(appState.project, { id, kind, name: appState.project.library?.[id]?.name || file.name || `Буфер ${kind}`, role, owner: options.characterName || '',
        ...(kind === 'video' ? { retention: 'partially_preserved' } : {}) });
    } else {
      URL.revokeObjectURL(url);
    }
    added++;
  }
  if (unsupported) showToast('Не все файлы поддерживаются: PNG, JPEG, WebP, MP4, MOV, WebM, MP3, WAV, M4A, FLAC, OGG');
  if (added && attach) { $('#mainPrompt').value = activeCut().prompt; renderCuts(); }
  if (attach) renderReferences();
  renderLibrary();
  scheduleDraft();
  return added;
}

function libraryInfo(asset) {
  const saved = appState.project.library?.[asset.asset_id] || {};
  const memberships = franchiseMemberships.get(asset.asset_id) || [];
  const activeName = COLLECTIONS.find(([id]) => id === libraryCollection)?.[1];
  const linked = memberships.find(item => item.franchise === activeName && item.source === 'entity')
    || memberships.find(item => item.franchise === activeName);
  const collection = linked ? { ...inferredCollection(asset, saved), ...linked } : inferredCollection(asset, saved);
  const catalog = stockByFilename.get(asset.original_name);
  const inferred = catalog?.type || inferredLibraryType(asset);
  const type = saved.type || inferred;
  const savedCategory = String(saved.category || '').trim();
  const categoryNeedsReview = /^\d+$/.test(savedCategory) || savedCategory.length === 1;
  const collectionCategory = collection.category || (collection.franchise ? `${collection.franchise} / ${collection.characterName || 'Материалы'}` : '');
  return {
    name: linked?.displayName || collection.displayName || saved.name || catalog?.title || asset.original_name,
    category: linked || collection.franchise ? collectionCategory : categoryNeedsReview ? collectionCategory || 'На разбор' : savedCategory && !['Изображения', 'Видео', 'Аудио'].includes(savedCategory)
      ? savedCategory === 'На разбор' && collectionCategory ? collectionCategory : savedCategory : collectionCategory || catalog?.category || inferredLibraryCategory(asset, type),
    type,
    characterName: linked?.characterName || collection.characterName || saved.characterName || '',
    franchise: linked?.franchise || collection.franchise || saved.franchise || '', look: collection.look || saved.look || '', hidden: Boolean(saved.hidden),
    preferredRole: linked?.preferredRole || collection.preferredRole || saved.preferredRole || catalog?.role || '',
    sourceUrl: saved.sourceUrl || catalog?.source || '', license: saved.license || catalog?.license || '',
    catalogId: saved.catalogId || catalog?.id || '', origin: saved.origin || catalog?.origin || '',
  };
}

function belongsToCollection(asset, id) {
  if (!id) return true;
  const names = new Set((franchiseMemberships.get(asset.asset_id) || []).map(item => item.franchise));
  return names.size === 1 && names.has(COLLECTIONS.find(([key]) => key === id)?.[1]);
}

export function renderLibraryTypes() {
  for (const selector of ['#referenceTypes']) {
    const host = $(selector); host.replaceChildren();
    for (const [key, name] of libraryTypes) {
      const button = document.createElement('button'); button.type = 'button';
      button.dataset.type = key; button.textContent = name;
      button.setAttribute('aria-pressed', String(libraryType === key));
      host.append(button);
    }
  }
}

export function renderLibrary() {
  const grid = $('#libraryGrid');
  if (!grid) return;
  grid.classList.toggle('managing', libraryManageMode);
  const franchiseSelect = $('#libraryFranchise');
  const characterSelect = $('#libraryCharacter');
  const lookSelect = $('#libraryLook');
  const infos = [...allAssets.values()].map(libraryInfo);
  const collections = $('#libraryCollections');
  collections.replaceChildren(...COLLECTIONS.map(([id, label]) => {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.collection = id;
    const count = [...allAssets.values()].filter(asset => belongsToCollection(asset, id)).length;
    button.textContent = `${label} · ${count}`; button.setAttribute('aria-pressed', String(id === libraryCollection)); return button;
  }));
  const sectionHost = $('#librarySections');
  const sectionNames = [...new Set([...allAssets.values()].filter(asset => belongsToCollection(asset, libraryCollection))
    .map(asset => sectionFor(asset, libraryInfo(asset))))].sort((a,b) => a.localeCompare(b, 'ru'));
  if (librarySection && !sectionNames.includes(librarySection)) librarySection = '';
  sectionHost.replaceChildren(...['Все материалы', ...sectionNames].map((name, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.dataset.section = index ? name : '';
    button.textContent = name; button.setAttribute('aria-pressed', String(librarySection === (index ? name : ''))); return button;
  }));
  const fillHierarchy = (select, values, placeholder) => {
    const current = select.value;
    select.replaceChildren(new Option(placeholder, ''), ...[...new Set(values.filter(Boolean))].sort((a,b) => a.localeCompare(b, 'ru')).map(value => new Option(value, value)));
    select.value = [...select.options].some(option => option.value === current) ? current : '';
  };
  fillHierarchy(franchiseSelect, infos.map(info => info.franchise), 'Все франшизы');
  const selected = COLLECTIONS.find(([id]) => id === libraryCollection)?.[1];
  if (selected) franchiseSelect.value = selected;
  fillHierarchy(characterSelect, infos.filter(info => !franchiseSelect.value || info.franchise === franchiseSelect.value).map(info => info.characterName), 'Все персонажи');
  fillHierarchy(lookSelect, infos.filter(info => matchesHierarchy(info, { franchise: franchiseSelect.value, character: characterSelect.value })).map(info => info.look), 'Все образы');
  $('#libraryAttachCharacter').disabled = !franchiseSelect.value || !characterSelect.value || !apiReady;
  const category = $('#libraryFilter').value;
  const categories = [...new Set([...allAssets.values()].filter(asset => belongsToCollection(asset, libraryCollection))
    .map((asset) => libraryInfo(asset).category))]
    .sort((a, b) => a.localeCompare(b, 'ru'));
  const filter = $('#libraryFilter');
  filter.replaceChildren(new Option('Все категории', ''));
  for (const name of categories) filter.add(new Option(name, name));
  filter.value = categories.includes(category) ? category : '';
  const suggestions = $('#libraryCategories');
  suggestions.replaceChildren(...categories.map((name) => new Option(name)));
  const query = $('#librarySearch').value.trim().toLocaleLowerCase();
  const matching = [...allAssets.values()].reverse().filter((asset) => {
    const info = libraryInfo(asset);
    return Boolean(appState.project.library?.[asset.asset_id]?.hidden) === showHiddenAssets &&
      belongsToCollection(asset, libraryCollection) &&
      (!librarySection || sectionFor(asset, info) === librarySection) &&
      (libraryType === 'all' || info.type === libraryType) && (!filter.value || info.category === filter.value) &&
      matchesHierarchy(info, { franchise: franchiseSelect.value, character: characterSelect.value, look: lookSelect.value }) &&
      (!query || `${info.name} ${asset.original_name} ${info.category} ${info.franchise} ${info.characterName} ${info.look}`.toLocaleLowerCase().includes(query));
  });
  const { assets, collapsed } = collapseLibraryCopies(matching, libraryInfo, showLibraryCopies);
  $('#libraryCount').textContent = `Показано ${Math.min(assets.length, libraryVisibleCount)} из ${assets.length}${collapsed ? ` · ${collapsed} копий скрыто` : ''}`;
  grid.replaceChildren();
  if (!assets.length) {
    const empty = document.createElement('p');
    empty.className = 'library-empty';
    empty.textContent = allAssets.size ? 'По этому запросу файлов нет. Измени поиск или категорию.' : 'Библиотека пуста. Загрузи первые файлы.';
    grid.append(empty);
  }
  for (const asset of assets.slice(0, libraryVisibleCount)) {
    const info = libraryInfo(asset);
    const card = document.createElement('article');
    card.className = `library-item${librarySelected.has(asset.asset_id) ? ' selected' : ''}`;
    card.dataset.id = asset.asset_id;
    const choice = document.createElement('input');
    choice.type = 'checkbox'; choice.checked = librarySelected.has(asset.asset_id);
    choice.dataset.action = 'select'; choice.setAttribute('aria-label', `Выбрать ${info.name}`);
    const thumb = document.createElement(asset.kind === 'audio' ? 'div' : 'img');
    thumb.className = 'library-thumb';
    if (asset.kind === 'image' || asset.kind === 'video') {
      thumb.src = `/api/assets/${encodeURIComponent(asset.asset_id)}/thumbnail`;
      if (asset.kind === 'image') thumb.onerror = () => { thumb.onerror = null; thumb.src = `/api/assets/${encodeURIComponent(asset.asset_id)}/file`; };
      thumb.loading = 'lazy';
      thumb.alt = asset.kind === 'video' ? `Кадр видео ${info.name}` : '';
      if (asset.kind === 'video') thumb.onerror = () => {
        const preview = document.createElement('video'); preview.className = 'library-thumb'; preview.muted = true;
        preview.preload = 'metadata'; preview.src = `/api/assets/${encodeURIComponent(asset.asset_id)}/file#t=0.3`;
        preview.addEventListener('loadedmetadata', () => { preview.currentTime = Math.min(0.3, Math.max(0, preview.duration - 0.01)); }, { once: true });
        preview.onerror = () => { const fallback = document.createElement('div'); fallback.className = 'library-thumb library-video-fallback'; fallback.textContent = '▶'; preview.replaceWith(fallback); };
        thumb.replaceWith(preview);
      };
    } else thumb.textContent = '♫';
    const fields = document.createElement('div'); fields.className = 'library-item-fields';
    const title = document.createElement('strong'); title.className = 'library-item-title'; title.textContent = info.name; title.title = info.name;
    const categoryLabel = document.createElement('span'); categoryLabel.className = 'library-item-category'; categoryLabel.textContent = info.category; categoryLabel.title = info.category;
    const name = document.createElement('input');
    name.value = info.name; name.maxLength = 120; name.dataset.action = 'name';
    name.setAttribute('aria-label', `Название ${asset.original_name}`);
    const group = document.createElement('input');
    group.value = info.category; group.maxLength = 80; group.dataset.action = 'category';
    group.setAttribute('aria-label', `Категория ${info.name}`);
    group.setAttribute('list', 'libraryCategories');
    const franchise = document.createElement('input'); franchise.value = info.franchise; franchise.maxLength = 80;
    franchise.dataset.action = 'franchise'; franchise.placeholder = 'Франшиза'; franchise.setAttribute('aria-label', `Франшиза ${info.name}`);
    const character = document.createElement('input'); character.value = info.characterName; character.maxLength = 80;
    character.dataset.action = 'characterName'; character.placeholder = 'Персонаж'; character.setAttribute('aria-label', `Персонаж ${info.name}`);
    const look = document.createElement('input'); look.value = info.look; look.maxLength = 80;
    look.dataset.action = 'look'; look.placeholder = 'Образ / костюм (необязательно)'; look.setAttribute('aria-label', `Образ ${info.name}`);
    const type = document.createElement('select'); type.dataset.action = 'type';
    type.setAttribute('aria-label', `Тип элемента ${info.name}`);
    for (const [key, label] of libraryTypes.slice(1)) type.add(new Option(label, key));
    type.value = info.type;
    const role = document.createElement('select'); role.dataset.action = 'role';
    role.setAttribute('aria-label', `Роль элемента ${info.name}`);
    const choices = asset.kind === 'image' ? imageRoles : asset.kind === 'video' ? videoRoles : audioRoles;
    for (const choice of choices) role.add(new Option(choice, choice));
    role.value = choices.includes(info.preferredRole) ? info.preferredRole : choices.includes(asset.role) ? asset.role : choices[0];
    const detail = document.createElement('small');
    detail.textContent = `${asset.kind === 'image' ? 'Фото' : asset.kind === 'video' ? 'Видео' : 'Аудио'} · ${asset.original_name}`;
    fields.append(title, categoryLabel, name, franchise, character, look, group, type, role, detail);
    if (asset.kind === 'video' && !asset.original_name.includes('· ReShot ')) {
      const control = document.createElement('select'); control.dataset.action = 'motion-control';
      control.setAttribute('aria-label', `Тип карты движения для ${info.name}`);
      for (const [value, label] of [['depth', 'Глубина и камера'], ['pose', 'Позы'], ['canny', 'Контуры']])
        control.add(new Option(label, value));
      const prepare = document.createElement('button'); prepare.type = 'button'; prepare.className = 'k-btn';
      prepare.dataset.action = 'motion-reference'; prepare.textContent = 'Подготовить карту движения';
      prepare.title = 'Создать отдельный видеореференс ReShot; исходный ролик сохраняется';
      fields.append(control, prepare);
    }
    if (info.sourceUrl) {
      const source = document.createElement('a');
      source.href = info.sourceUrl;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      source.textContent = `${info.origin || 'Источник'} · ${info.license || 'условия не указаны'} ↗`;
      source.className = 'library-source';
      fields.append(source);
    } else if (info.origin) {
      const source = document.createElement('span');
      source.className = 'library-source';
      source.textContent = `${info.origin} · ${info.license || 'условия не указаны'}`;
      fields.append(source);
    }
    if (libraryManageMode || showHiddenAssets) {
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'library-hide';
      remove.dataset.action = showHiddenAssets ? 'restore' : 'hide'; remove.textContent = showHiddenAssets ? 'Восстановить' : 'Убрать'; remove.title = 'Файл и старые задания сохранятся';
      fields.append(remove);
    }
    card.append(choice, thumb, fields); grid.append(card);
  }
  $('#libraryMore').hidden = assets.length <= libraryVisibleCount;
  $('#libraryMore').textContent = `Показать ещё · ${Math.max(0, Math.min(48, assets.length - libraryVisibleCount))}`;
  $('#librarySelection').textContent = librarySelected.size ? `Выбрано: ${librarySelected.size}` : 'Ничего не выбрано';
  $('#attachLibrary').disabled = !apiReady || librarySelected.size === 0 || showHiddenAssets;
}

export function attachLibraryAssets(ids, metadata = new Map()) {
  let added = 0;
  for (const id of ids) {
    const asset = allAssets.get(id);
    if (!asset || appState.project.references.some((ref) => ref.id === id)) continue;
    const limit = referenceLimits[asset.kind];
    if (appState.project.references.length >= 12 || appState.project.references.filter((ref) => ref.kind === asset.kind).length >= limit) {
      showToast(`Лимит сцены: до 9 фото, 3 видео, 3 аудио и 12 файлов всего. Добавлено: ${added}.`);
      break;
    }
    const allowed = asset.kind === 'image' ? imageRoles : asset.kind === 'video' ? videoRoles : audioRoles;
    const info = metadata.get(id) || libraryInfo(asset);
    const role = allowed.includes(info.preferredRole) ? info.preferredRole :
      info.type === 'characters' && asset.kind === 'image' ? 'Лицо героя' :
      allowed.includes(asset.role) ? asset.role : 'Авто — определить по содержимому';
    files.set(id, { url: `/api/assets/${encodeURIComponent(id)}/file` });
    appState.project = addReference(appState.project, { id, kind: asset.kind, name: info.name || info.displayName || asset.original_name, role, owner: info.characterName,
      ...(asset.kind === 'video' ? { retention: 'partially_preserved' } : {}) });
    added++;
  }
  if (added) { renderReferences(); renderCuts(); scheduleDraft(); }
  return added;
}

export function refreshLibraryResults(force = false) {
  if ($('#libraryResults').hidden) return;
  const completed = knownJobs.filter((job) => job.state === 'completed');
  const signature = `${libraryResultLimit}:${completed.map((job) => job.job_id).join(',')}`;
  if (!force && signature === libraryResultsSignature) return;
  libraryResultsSignature = signature;
  const { shown, total } = renderAssetResults($('#libraryResultsGrid'), knownJobs, libraryResultLimit);
  $('#libraryResultsCount').textContent = `${shown} из ${total}`;
  $('#libraryResultsMore').hidden = shown >= total;
}

export function showLibraryTab(tab) {
  for (const name of ['Existing', 'Results', 'Stock', 'Upload']) {
    const active = name.toLowerCase() === tab;
    $(`#library${name}`).hidden = !active;
    $(`#libraryTab${name}`).setAttribute('aria-selected', String(active));
  }
  if (tab === 'existing') renderLibrary();
  if (tab === 'results') refreshLibraryResults(true);
  if (tab === 'stock') stockLibrary.render();
}

async function resolveStockAsset(item) {
  if (!apiReady) throw new Error('Локальная Studio недоступна');
  let asset = [...allAssets.values()].find((entry) =>
    appState.project.library?.[entry.asset_id]?.catalogId === item.id || entry.original_name === `${item.id}.jpg`);
  if (!asset) {
    const response = await fetch(item.image);
    if (!response.ok) throw new Error('Файл подборки недоступен');
    const file = new File([await response.blob()], `${item.id}.jpg`, { type: 'image/jpeg' });
    const count = await addFiles([file], item.role, { attach: false, type: item.type, category: item.category,
      provenance: { catalogId: item.id, sourceUrl: item.source, license: item.license, origin: item.origin } });
    if (!count) throw new Error('Не удалось сохранить файл');
    asset = [...allAssets.values()].find((entry) => appState.project.library?.[entry.asset_id]?.catalogId === item.id);
  }
  if (!asset) throw new Error('Файл не найден после сохранения');
  appState.project.library ||= {};
  appState.project.library[asset.asset_id] = { ...libraryInfo(asset), name: item.title, category: item.category,
    type: item.type, preferredRole: item.role, catalogId: item.id,
    sourceUrl: item.source, license: item.license, origin: item.origin };
  renderLibrary(); scheduleDraft();
  return asset.asset_id;
}

export function installLibraryControls() {
  stockLibrary = installStockLibrary({
    findImported: (catalogId) => [...allAssets.values()].find((asset) =>
      appState.project.library?.[asset.asset_id]?.catalogId === catalogId || asset.original_name === `${catalogId}.jpg`)?.asset_id,
    showToast,
    onImport: async (item) => {
      await resolveStockAsset(item);
      showToast(`${item.title} добавлен в библиотеку`);
    },
    onUse: async (item) => {
      const id = await resolveStockAsset(item);
      if (!attachLibraryAssets([id])) throw new Error('Лимит сцены или файл уже добавлен');
      const ref = appState.project.references.find((entry) => entry.id === id);
      if (ref) ref.role = item.role;
      renderReferences(); scheduleDraft();
      showToast(`${item.title} добавлен в сцену`);
    },
  });
  $('#openLibrary').addEventListener('click', () => {
    if (!apiReady) { showToast('Библиотека доступна после подключения локальной Studio'); return; }
    librarySelected.clear(); libraryVisibleCount = 48; libraryManageMode = false; libraryType = 'all'; librarySection = ''; $('#libraryFilter').value = ''; renderLibraryTypes();
    $('#libraryManage').setAttribute('aria-pressed', 'false'); $('#libraryManage').textContent = 'Управлять';
    showLibraryTab('existing'); showAssetLibrary($('#libraryDialog')); $('#librarySearch').focus();
  });
  $('#referenceTypes').addEventListener('click', (event) => {
    const type = event.target.closest('[data-type]')?.dataset.type;
    if (!type) return;
    $('#openLibrary').click(); libraryType = type; $('#libraryFilter').value = ''; renderLibraryTypes(); renderLibrary();
  });
  $('#libraryCollections').addEventListener('click', (event) => {
    const id = event.target.closest('[data-collection]')?.dataset.collection;
    if (!id) return;
    libraryCollection = id; librarySection = ''; libraryVisibleCount = 48; $('#libraryFilter').value = ''; renderLibrary();
  });
  $('#librarySections').addEventListener('click', (event) => {
    const button = event.target.closest('[data-section]'); if (!button) return;
    librarySection = button.dataset.section; libraryVisibleCount = 48; renderLibrary();
  });
  $('#closeLibrary').addEventListener('click', () => closeAssetLibrary($('#libraryDialog')));
  $('#libraryDialog').addEventListener('click', (event) => {
    if (!assetsWorkspace && event.target === $('#libraryDialog')) $('#libraryDialog').close();
  });
  $('#libraryTabExisting').addEventListener('click', () => showLibraryTab('existing'));
  $('#libraryTabResults').addEventListener('click', () => showLibraryTab('results'));
  $('#libraryTabUpload').addEventListener('click', () => showLibraryTab('upload'));
  $('#libraryTabStock').addEventListener('click', () => showLibraryTab('stock'));
  $('#libraryResultsMore').addEventListener('click', () => { libraryResultLimit += 12; refreshLibraryResults(true); });
  $('#libraryResultsGrid').addEventListener('click', async (event) => {
    const jobId = event.target.closest('button[data-job-id]')?.dataset.jobId;
    if (!jobId) return;
    const job = knownJobs.find((item) => item.job_id === jobId && item.state === 'completed');
    if (!job) { showToast('Готовый ролик не найден. Обнови страницу и повтори.'); return; }
    try {
      await sendJobToStory(job, window.h3Studio);
      window.location.assign('/?view=scenes#scene-board');
    } catch (error) { showToast(error.message); }
  });
  $('#librarySearch').addEventListener('input', () => { libraryVisibleCount = 48; renderLibrary(); });
  $('#libraryFilter').addEventListener('change', () => { libraryVisibleCount = 48; renderLibrary(); });
  for (const id of ['libraryFranchise', 'libraryCharacter', 'libraryLook']) $(`#${id}`).addEventListener('change', () => { libraryVisibleCount = 48; renderLibrary(); });
  $('#libraryAttachCharacter').addEventListener('click', () => {
    const franchise = $('#libraryFranchise').value, character = $('#libraryCharacter').value, look = $('#libraryLook').value;
    const chosen = characterReferenceSet([...allAssets.values()], libraryInfo, franchise, character, look);
    if (!chosen.some(({ asset, info }) => ['Лицо героя', 'Персонаж целиком'].includes(info.preferredRole || asset.role))) {
      showToast(`У ${character} нет проверенного фото лица или тела. Назначь его в библиотеке.`); return;
    }
    const counts = new Map();
    for (const { asset, info } of chosen) {
      const role = info.preferredRole || asset.role;
      counts.set(role, (counts.get(role) || 0) + 1);
    }
    if ([...counts.values()].some(count => count > 1)) {
      showToast('Найдено несколько фото одной роли. Выбери нужные карточки вручную, чтобы не смешать лица или костюмы.'); return;
    }
    if (appState.project.references.some(ref => ref.owner === character && ref.role === 'Костюм' && !chosen.some(({ asset }) => asset.asset_id === ref.id))) {
      showToast(`В сцене уже есть другой костюм ${character}. Убери его перед сменой образа.`); return;
    }
    const added = attachLibraryAssets(chosen.map(({ asset }) => asset.asset_id));
    showToast(added ? `Добавлено ${added} референсов: ${character}${look ? ` · ${look}` : ''}` : 'Референсы уже в сцене или достигнут лимит.');
  });
  $('#libraryMore').addEventListener('click', () => { libraryVisibleCount += 48; renderLibrary(); });
  $('#libraryManage').addEventListener('click', () => {
    libraryManageMode = !libraryManageMode;
    $('#libraryManage').setAttribute('aria-pressed', String(libraryManageMode));
    $('#libraryManage').textContent = libraryManageMode ? 'Готово' : 'Управлять';
    renderLibrary();
  });
  $('#libraryHidden').addEventListener('click', () => {
    showHiddenAssets = !showHiddenAssets;
    $('#libraryHidden').setAttribute('aria-pressed', String(showHiddenAssets));
    librarySelected.clear(); renderLibrary();
  });
  $('#libraryCopies').addEventListener('click', () => {
    showLibraryCopies = !showLibraryCopies;
    $('#libraryCopies').setAttribute('aria-pressed', String(showLibraryCopies));
    $('#libraryCopies').textContent = showLibraryCopies ? 'Все копии' : 'Копии скрыты';
    libraryVisibleCount = 48; renderLibrary();
  });
  $('#libraryGrid').addEventListener('change', (event) => {
    const id = event.target.closest('[data-id]')?.dataset.id;
    const asset = allAssets.get(id);
    if (!asset) return;
    const action = event.target.dataset.action;
    if (action === 'select') {
      if (event.target.checked) librarySelected.add(id); else librarySelected.delete(id);
      event.target.closest('.library-item').classList.toggle('selected', event.target.checked);
      $('#librarySelection').textContent = librarySelected.size ? `Выбрано: ${librarySelected.size}` : 'Ничего не выбрано';
      $('#attachLibrary').disabled = librarySelected.size === 0;
      return;
    }
    if (['name', 'category', 'type', 'role', 'franchise', 'characterName', 'look'].includes(action)) {
      appState.project.library ||= {};
      const info = libraryInfo(asset);
      const field = action === 'role' ? 'preferredRole' : action;
      appState.project.library[id] = { ...info, [field]: event.target.value.trim() || (action === 'name' ? asset.original_name : action === 'type' ? 'other' : action === 'role' ? asset.role : libraryDefaults[asset.kind]) };
      const ref = appState.project.references.find((item) => item.id === id);
      if (ref && action === 'name') { ref.name = appState.project.library[id].name; renderReferences(); }
      const card = event.target.closest('.library-item');
      card.querySelector('.library-item-title').textContent = appState.project.library[id].name;
      card.querySelector('.library-item-category').textContent = appState.project.library[id].category;
      if (['type', 'franchise', 'characterName', 'look', 'role'].includes(action)) renderLibrary();
      scheduleDraft();
    }
  });
  $('#libraryGrid').addEventListener('click', (event) => {
    const prepare = event.target.closest('button[data-action="motion-reference"]');
    if (prepare) {
      const id = prepare.closest('[data-id]')?.dataset.id;
      const control = prepare.closest('.library-item')?.querySelector('select[data-action="motion-control"]')?.value || 'depth';
      if (!id || prepare.disabled) return;
      prepare.disabled = true; prepare.textContent = 'Готовим карту…';
      void fetch(`/api/assets/${encodeURIComponent(id)}/motion-reference`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ control }) })
        .then(async response => { const result = await response.json(); if (!response.ok) throw new Error(result.detail || 'ReShot не подготовил видео'); return result; })
        .then(asset => {
          allAssets.set(asset.asset_id, asset);
          appState.project.library ||= {};
          appState.project.library[asset.asset_id] = { ...libraryInfo(allAssets.get(id)),
            name: asset.original_name, preferredRole: asset.role, origin: 'ReShot', license: 'Apache-2.0' };
          renderLibrary(); scheduleDraft(); showToast('Карта движения готова в библиотеке. Проверь её перед добавлением в шот.');
        })
        .catch(error => { prepare.disabled = false; prepare.textContent = 'Подготовить карту движения'; showToast(error.message); });
      return;
    }
    if (!event.target.closest('button,input,select,a,label')) {
      const card = event.target.closest('.library-item');
      const choice = card?.querySelector('input[data-action="select"]');
      if (choice && !showHiddenAssets) { choice.checked = !choice.checked; choice.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    const button = event.target.closest('button[data-action="hide"],button[data-action="restore"]');
    if (!button) return;
    const id = button.closest('[data-id]')?.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'hide' && !window.confirm('Убрать элемент из библиотеки? Его можно восстановить через «Скрытые».')) return;
    appState.project.library ||= {};
    appState.project.library[id] = { ...libraryInfo(allAssets.get(id)), hidden: button.dataset.action === 'hide' };
    librarySelected.delete(id);
    if (appState.project.references.some((ref) => ref.id === id)) showToast('Элемент скрыт в библиотеке, но закреплён в текущей сцене.');
    renderLibrary(); scheduleDraft();
  });
  $('#attachLibrary').addEventListener('click', () => {
    const added = attachLibraryAssets([...librarySelected]);
    if (added) {
      if (!assetsWorkspace) $('#libraryDialog').close();
      else { librarySelected.clear(); renderLibrary(); }
      showToast(`${added} файлов добавлено в сцену`);
    }
  });
  $('#libraryFiles').addEventListener('change', (event) => {
    $('#uploadSummary').textContent = event.target.files.length ? `Выбрано файлов: ${event.target.files.length}` : 'Файлы не выбраны';
  });
  $('#uploadType').addEventListener('change', () => {
    $('#characterNameField').hidden = $('#uploadType').value !== 'characters';
  });
  $('#uploadToLibrary').addEventListener('click', async () => {
    const input = $('#libraryFiles');
    const selected = [...input.files];
    if (!selected.length) { showToast('Сначала выбери файлы'); return; }
    const characterName = $('#uploadType').value === 'characters' ? $('#uploadCharacterName').value.trim() : '';
    if ($('#uploadType').value === 'characters' && !characterName) { showToast('Укажи имя персонажа'); $('#uploadCharacterName').focus(); return; }
    if (characterName && selected.some((file) => mediaKind(file) !== 'image')) { showToast('Для персонажа выбери изображения'); return; }
    const button = $('#uploadToLibrary'); button.disabled = true;
    try {
      const added = await addFiles(selected, $('#uploadRole').value, {
        category: $('#uploadCategory').value.trim(), type: $('#uploadType').value, characterName,
        attach: $('#uploadAttach').checked, allowMixed: true,
      });
      if (added) {
        input.value = ''; $('#uploadSummary').textContent = 'Файлы не выбраны';
        showToast(`${added} файлов сохранено в библиотеке`);
        if ($('#uploadAttach').checked && !assetsWorkspace) $('#libraryDialog').close(); else showLibraryTab('existing');
      }
    } finally { button.disabled = false; }
  });
}
