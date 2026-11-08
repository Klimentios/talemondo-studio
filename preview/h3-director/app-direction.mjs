// Create: per-shot direction (director presets, profiles, the preset dialog) and prompt sync.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { parsePromptDirection, promptDirectionErrors, writePromptDirection } from './prompt-direction.mjs?v=20261008-auto-camera-1';
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS, displayDirectorCategory, displayDirectorPreset, getPreset, validateDirectorState } from './director-presets.mjs?v=20261031-lighting-15-1';
import { formatParameters } from './director-labels.mjs?v=20261007-camera-options-5';
import { installDirectorPresetForm } from './director-preset-form.mjs?v=20261007-camera-options-5';
import { applyDirectorVisual } from './director-visuals.mjs';
import { createMotionPreview } from './motion-preview.mjs';
import { DIRECTOR_PROFILES, displayDirectorProfile } from './director-profiles.mjs?v=20261003-camera-photos';
import { getLocale } from './locale.mjs?v=20261108-i18n-1';
import { createProject } from './model.mjs?v=20261106-refmatch';
import { apiReady } from './app-bootstrap.mjs?v=20261106-refmatch';
import { promptDirectionHelp } from './app-composer.mjs?v=20261106-refmatch';
import { actingControl } from './app-controls.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { showLibraryTab } from './app-library.mjs?v=20261108-clean';
import { renderBrief, renderCuts, renderReferences } from './app-references.mjs?v=20261108-clean';
import { $, allAssets, showToast } from './app-shared.mjs?v=20261106-refmatch';
import { renderStage } from './app-stage.mjs?v=20261106-refmatch';

let directorCategory = 'camera';
export let presetForm;

export function directorPresets() { return Array.isArray(appState.project.customDirectorPresets) ? appState.project.customDirectorPresets : []; }

export function activeCut() {
  if (!appState.project.cuts?.length) appState.project.cuts = createProject().cuts;
  const cut = appState.project.cuts.find((item) => item.id === appState.selectedCutId) || appState.project.cuts[0];
  appState.selectedCutId = cut.id;
  cut.director ||= { ...(appState.project.director || {}) };
  return cut;
}

export function activeDirector() { return activeCut().director; }

export function directorChoice(key) { return getPreset(activeDirector()[key], directorPresets()); }

export function syncPromptDirection(source = 'prompt', strict = false) {
  const input = $('#mainPrompt');
  if (source === 'cards') {
    const parsed = parsePromptDirection(input.value, directorPresets());
    if (parsed.present) {
      if (parsed.errors.length) {
        showToast('Сначала исправь блок режиссуры: ' + parsed.errors.join(' '));
        // Leave the invalid block intact; it will block submission.
      } else input.value = writePromptDirection(input.value, activeDirector(), directorPresets());
    }
  }
  const parsed = parsePromptDirection(input.value, directorPresets());
  if (parsed.present && !parsed.errors.length) {
    activeCut().director = { ...parsed.state };
    activeCut().directorProfileId = '';
    appState.project.director = { ...parsed.state };
  }
  activeCut().prompt = input.value;
  if (source === 'cards') renderCuts();
  const errors = promptDirectionErrors(input.value, activeDirector(), directorPresets(), appState.project.mode);
  if (promptDirectionHelp) {
    promptDirectionHelp.status.hidden = !parsed.present;
    promptDirectionHelp.status.textContent = errors.length ? errors.join(' ') : 'Режиссура из промпта применена к карточкам.' + (parsed.autoFilled?.length ? ' Автоматически: ' + parsed.autoFilled.join(', ') + '.' : '');
    promptDirectionHelp.status.style.color = errors.length ? '#ffb4ab' : '';
  }
  if (strict && errors.length) throw new Error(errors.join(' '));
  return parsed;
}

export function selectCut(id) {
  appState.selectedCutId = id;
  const cut = activeCut();
  $('#mainPrompt').value = cut.prompt || '';
  syncPromptDirection();
  $('#duration').value = cut.duration;
  $('#activeShotLabel').textContent = `· кадр ${String(appState.project.cuts.indexOf(cut) + 1).padStart(2, '0')}`;
  renderReferences();
  renderCuts(); renderDirectorStrip(); renderBrief(); renderStage();
}

export function renderDirectorStrip() {
  const strip = $('#directorStrip');
  strip.replaceChildren();
  $('#directorProfile').value = activeCut().directorProfileId || '';
  for (const [key] of DIRECTOR_CATEGORIES) {
    const { title: label, description: help } = displayDirectorCategory(key, getLocale());
    const preset = directorChoice(key);
    const display = displayDirectorPreset(preset, getLocale());
    const button = document.createElement('button');
    button.type = 'button'; button.className = `director-tile${preset ? ' has-preset' : ''}`;
    button.dataset.category = key;
    button.setAttribute('aria-label', `${label}: ${display.title || (getLocale() === 'en' ? 'Auto' : 'Авто')}. ${getLocale() === 'en' ? 'Open library' : 'Открыть библиотеку'}`);
    const art = document.createElement('span');
    art.className = `director-tile-art tone-${preset?.tone || 'slate'} kind-${key}`;
    art.setAttribute('aria-hidden', 'true');
    const motion = preset && createMotionPreview(preset.id);
    if (motion) art.append(motion);
    else if (preset?.image) {
      const picture = document.createElement('img'); picture.src = preset.image; picture.alt = '';
      picture.className = 'director-camera-photo'; art.append(picture); art.classList.add('has-photo');
    } else if (preset) applyDirectorVisual(art, preset);
    const caption = document.createElement('span'); caption.className = 'director-tile-caption';
    const title = document.createElement('span'); title.textContent = label;
    const value = document.createElement('strong'); value.textContent = display.title || (getLocale() === 'en' ? 'Auto' : 'Авто');
    const arrow = document.createElement('span'); arrow.textContent = '⌄'; arrow.className = 'director-tile-arrow';
    caption.append(title, value); button.append(art, caption, arrow); button.title = help; strip.append(button);
  }
  const warnings = validateDirectorState(activeDirector(), directorPresets(), appState.project.mode);
  $('#directorWarning').hidden = !warnings.length;
  $('#directorWarning').textContent = warnings.join(' ');
}

export function renderDirectorGrid() {
  const grid = $('#directorGrid'); grid.replaceChildren();
  const query = $('#directorSearch').value.trim().toLocaleLowerCase();
  const view = $('#directorView').value;
  const favorites = new Set(appState.project.directorFavorites || []);
  const recents = new Set(appState.project.directorRecent || []);
  const presets = [...DIRECTOR_PRESETS, ...directorPresets()].filter((preset) => preset.category === directorCategory &&
    (!preset.legacy || preset.id === activeDirector()[directorCategory] ||
      (view === 'favorites' && favorites.has(preset.id)) || (view === 'recent' && recents.has(preset.id))) &&
    (!query || `${preset.name} ${preset.descriptionRu || ''} ${preset.description} ${preset.tags?.join(' ') || ''}`.toLocaleLowerCase().includes(query)) &&
    (view === 'all' || (view === 'favorites' && favorites.has(preset.id)) ||
      (view === 'recent' && recents.has(preset.id)) || (view === 'custom' && preset.source === 'custom')));
  if (!presets.length) {
    const empty = document.createElement('p'); empty.className = 'director-empty';
    empty.textContent = getLocale() === 'en' ? 'No presets match. Change the search or create one.' : 'Пресетов по этому запросу нет. Измени поиск или создай свой.'; grid.append(empty);
  }
  for (const preset of presets) {
    const display = displayDirectorPreset(preset, getLocale());
    const card = document.createElement('article');
    card.className = `director-preset${activeDirector()[directorCategory] === preset.id ? ' selected' : ''}`;
    if (preset.source === 'custom') card.dataset.userContent = 'true';
    const art = document.createElement('div');
    art.className = `preset-art tone-${preset.tone || 'slate'} kind-${directorCategory}`;
    art.setAttribute('aria-hidden', 'true');
    const motion = createMotionPreview(preset.id);
    if (motion) art.append(motion);
    else if (preset.image) {
      const picture = document.createElement('img'); picture.src = preset.image; picture.alt = '';
      picture.className = 'director-camera-photo'; art.append(picture); art.classList.add('has-photo');
    } else if (directorCategory === 'color') {
      const swatches = document.createElement('div'); swatches.className = 'preset-swatches';
      const colors = preset.parameters?.swatches || [];
      for (let i = 0; i < Math.max(6, colors.length); i++) { const swatch = document.createElement('i'); swatch.style.setProperty('--swatch-index', i); if (colors[i]) swatch.style.backgroundColor = colors[i]; swatches.append(swatch); }
      art.append(swatches);
    } else if (!applyDirectorVisual(art, preset)) {
      const frame = document.createElement('div'); frame.className = 'preset-scene';
      frame.innerHTML = '<span class="scene-window"></span><span class="scene-person"></span><span class="scene-floor"></span>';
      art.append(frame);
    }
    if (directorCategory === 'color' && preset.image && Array.isArray(preset.parameters?.swatches)) {
      const strip = document.createElement('span'); strip.className = 'preset-color-strip';
      for (const color of preset.parameters.swatches.slice(0, 8)) {
        const swatch = document.createElement('i'); swatch.style.backgroundColor = color; strip.append(swatch);
      }
      art.append(strip);
    }
    const choose = document.createElement('button'); choose.type = 'button'; choose.className = 'preset-choose';
    choose.dataset.preset = preset.id; choose.setAttribute('aria-label', `${getLocale() === 'en' ? 'Select' : 'Выбрать'} ${display.title}`);
    const name = document.createElement('strong'); name.textContent = display.title;
    const detail = document.createElement('small'); detail.textContent = display.description;
    choose.append(art, name, detail);
    if (display.spec) { const spec = document.createElement('small'); spec.className = 'preset-spec'; spec.textContent = display.spec; choose.append(spec); }
    else if (preset.parameters && Object.keys(preset.parameters).length) {
      const spec = document.createElement('small'); spec.className = 'preset-spec';
      spec.textContent = formatParameters(preset.category, preset.parameters, getLocale(), 3);
      choose.append(spec);
    }
    if (preset.imageSource) {
      const credit = document.createElement('a'); credit.className = 'preset-credit'; credit.href = preset.imageSource;
      credit.target = '_blank'; credit.rel = 'noopener noreferrer'; credit.textContent = `Фото: ${preset.imageCredit}`;
      card.append(credit);
    }
    const favorite = document.createElement('button'); favorite.type = 'button'; favorite.className = 'preset-favorite';
    favorite.dataset.favorite = preset.id; favorite.textContent = favorites.has(preset.id) ? '★' : '☆';
    favorite.setAttribute('aria-label', favorites.has(preset.id) ? `${getLocale() === 'en' ? 'Remove' : 'Убрать'} ${display.title} ${getLocale() === 'en' ? 'from favorites' : 'из избранного'}` : `${getLocale() === 'en' ? 'Add' : 'Добавить'} ${display.title} ${getLocale() === 'en' ? 'to favorites' : 'в избранное'}`);
    card.append(choose, favorite);
    if (preset.source === 'custom') {
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'preset-remove';
      remove.dataset.removePreset = preset.id; remove.textContent = getLocale() === 'en' ? 'Delete preset' : 'Удалить пресет';
      card.append(remove);
    }
    grid.append(card);
  }
  $('#directorSelection').textContent = displayDirectorPreset(directorChoice(directorCategory), getLocale()).title || (getLocale() === 'en' ? 'Auto' : 'Авто');
  const chosen = directorChoice(directorCategory);
  $('#directorParameters').textContent = chosen ? formatParameters(chosen.category, chosen.parameters || {}, getLocale()) : (getLocale() === 'en' ? 'No parameters selected' : 'Параметры не выбраны');
}

function openDirectorDialog(category) {
  directorCategory = category;
  const meta = DIRECTOR_CATEGORIES.find(([key]) => key === category);
  if (!meta) return;
  const display = displayDirectorCategory(category, getLocale());
  $('#directorDialogTitle').textContent = display.title;
  $('#directorDialogDescription').textContent = display.description + (['camera', 'lens', 'shot', 'movement', 'focus'].includes(category)
    ? getLocale() === 'en' ? ' · geometry and focus reach H3 as text; the camera guide is server controlled and may be disabled.' : ' · геометрия и фокус передаются H3 текстом; видеогид камеры зависит от настройки сервера и может быть выключен.'
    : getLocale() === 'en' ? ' · settings reach H3 as validated direction.' : ' · параметры передаются H3 как проверенная режиссёрская инструкция.');
  $('#directorSearch').value = ''; $('#directorView').value = 'all';
  $('#directorCustomForm').hidden = true;
  $('#directorReference').hidden = !['lighting', 'color', 'style'].includes(category);
  actingControl.show(category === 'acting');
  $('#directorDialog .director-dialog-tools').hidden = false;
  $('#directorSearch').hidden = category === 'acting';
  $('#directorView').hidden = category === 'acting';
  $('#newDirectorPreset').hidden = category === 'acting';
  renderDirectorGrid(); $('#directorDialog').showModal(); $('#directorSearch').focus();
}

export function installDirectionControls() {
  // «Свой пресет» form lives in director-preset-form.mjs.
  presetForm = installDirectorPresetForm({
    category: () => directorCategory,
    locale: () => getLocale(),
    choice: () => directorChoice(directorCategory),
    apiReady: () => apiReady,
    toast: (message) => showToast(message),
    registerAsset: (asset) => allAssets.set(asset.asset_id, asset),
    savePreset: (preset) => {
      appState.project.customDirectorPresets ||= []; appState.project.customDirectorPresets.push(preset);
      activeDirector()[preset.category] = preset.id;
      activeCut().directorProfileId = '';
      appState.project.director = { ...activeDirector() };
      syncPromptDirection('cards');
      renderDirectorStrip(); renderDirectorGrid(); renderBrief(); scheduleDraft();
    },
  });
  $('#directorStrip').addEventListener('click', (event) => {
    const category = event.target.closest('[data-category]')?.dataset.category;
    if (category) openDirectorDialog(category);
  });
  renderDirectorProfiles();
  $('#directorProfile').addEventListener('change', (event) => {
    const profile = DIRECTOR_PROFILES.find((item) => item.id === event.target.value);
    if (!profile) { activeCut().directorProfileId = ''; scheduleDraft(); return; }
    activeCut().director = { ...profile.director };
    activeCut().directorProfileId = profile.id;
    appState.project.director = { ...profile.director };
    if (appState.project.mode !== profile.mode) {
      $('#modeSelect').value = profile.mode;
      $('#modeSelect').dispatchEvent(new Event('change', { bubbles: true }));
    }
    syncPromptDirection('cards');
    renderDirectorStrip(); renderBrief(); scheduleDraft();
  showToast(`Профиль «${profile.name}» применён к текущему кадру. Все 8 настроек можно изменить.`);
  });
  $('#directorGrid').addEventListener('click', (event) => {
    const removeId = event.target.closest('[data-remove-preset]')?.dataset.removePreset;
    if (removeId) {
      const preset = directorPresets().find((item) => item.id === removeId);
      if (!preset || !window.confirm(`Удалить свой пресет «${preset.name}»?`)) return;
      appState.project.customDirectorPresets = directorPresets().filter((item) => item.id !== removeId);
      for (const cut of appState.project.cuts) if (cut.director?.[preset.category] === removeId) delete cut.director[preset.category];
      if (appState.project.director?.[preset.category] === removeId) delete appState.project.director[preset.category];
      renderDirectorStrip(); renderDirectorGrid(); renderBrief(); scheduleDraft(); return;
    }
    const favorite = event.target.closest('[data-favorite]')?.dataset.favorite;
    if (favorite) {
      const set = new Set(appState.project.directorFavorites || []);
      if (set.has(favorite)) set.delete(favorite); else set.add(favorite);
      appState.project.directorFavorites = [...set]; renderDirectorGrid(); scheduleDraft(); return;
    }
    const id = event.target.closest('[data-preset]')?.dataset.preset;
    const preset = getPreset(id, directorPresets());
    if (!preset || preset.category !== directorCategory) return;
    activeDirector()[directorCategory] = id;
    if (directorCategory === 'acting') {
      appState.project.actingMode = preset.parameters?.cast === 'animal' ? 'animal' : preset.parameters?.cast === 'human' ? 'human' : 'auto';
      actingControl.sync({ mode: appState.project.actingMode, owner: appState.project.actingOwner, voice: appState.project.actingVoice, timbre: appState.project.actingTimbre,
        texture: appState.project.actingTexture, accent: appState.project.actingAccent, emotion: appState.project.actingEmotion, pace: appState.project.actingPace });
    }
    activeCut().directorProfileId = '';
    appState.project.director = { ...activeDirector() };
    syncPromptDirection('cards');
    appState.project.directorRecent = [id, ...(appState.project.directorRecent || []).filter((item) => item !== id)].slice(0, 24);
    renderDirectorStrip(); renderDirectorGrid(); renderBrief(); scheduleDraft();
  });
  $('#directorSearch').addEventListener('input', renderDirectorGrid);
  $('#directorView').addEventListener('change', renderDirectorGrid);
  $('#closeDirectorDialog').addEventListener('click', () => $('#directorDialog').close());
  $('#doneDirectorPreset').addEventListener('click', () => $('#directorDialog').close());
  $('#directorDialog').addEventListener('click', (event) => { if (event.target === $('#directorDialog')) $('#directorDialog').close(); });
  $('#clearDirectorPreset').addEventListener('click', () => {
    delete activeDirector()[directorCategory]; activeCut().directorProfileId = ''; appState.project.director = { ...activeDirector() }; syncPromptDirection('cards'); renderDirectorStrip(); renderDirectorGrid(); renderBrief(); scheduleDraft();
  });
  $('#directorReference').addEventListener('click', () => {
    $('#directorDialog').close(); $('#openLibrary').click();
    $('#uploadRole').value = 'Стиль';
    $('#uploadType').value = 'styles';
    $('#uploadCategory').value = directorCategory === 'lighting' ? 'Свет' : directorCategory === 'color' ? 'Цвет' : 'Стиль';
    showLibraryTab('upload');
  });
}

export function renderDirectorProfiles() {
  const select = $('#directorProfile');
  const selected = select.value || activeCut().directorProfileId || '';
  select.replaceChildren(new Option(getLocale() === 'en' ? 'Custom setup' : 'Свой набор', ''));
  for (const profile of DIRECTOR_PROFILES) select.add(new Option(displayDirectorProfile(profile, getLocale()), profile.id));
  select.value = selected;
}
