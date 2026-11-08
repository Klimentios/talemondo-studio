// Create: references, their numbering and owners, @-mentions, brief, effective size and the shot strip.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { alignPromptPictures } from './prompt-reference-bindings.mjs?v=20261014-heading-1';
import { excerptButton } from './reference-excerpts.mjs?v=20261029-scene-run-1';
import { renderProfileLabel } from './render-experiments.mjs?v=20261021-2';
import { parsePromptDirection } from './prompt-direction.mjs?v=20261008-auto-camera-1';
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS } from './director-presets.mjs?v=20261031-lighting-15-1';
import { promptWarnings } from './prompt-warnings.mjs?v=20261004-contract-1';
import { cameraOpticsForPresets, cameraStateForPresets } from './director-camera.mjs?v=20261008-auto-camera-1';
import { opticsSummary } from './optics-display.mjs?v=20261004-contract-1';
import { getLocale } from './locale.mjs?v=20261108-i18n-1';
import { shotSummary } from './workspace-context.mjs?v=20261005-1';
import { referenceOwnerControlState, referenceOwnerLabels, subjectNames } from './reference-owners.mjs?v=20261029-scene-run-2';
import { openSwapMaskDialog, swapMaskButton } from './swap-mask-editor.mjs?v=20261105-review-1';
import { heroNames, openPairingDialog, pairingButton } from './reference-pairing.mjs?v=20261015-1';
import { renderVideoWorkflow, videoWorkflowState as baseVideoWorkflowState } from './video-workflow.mjs?v=20261022-swap-recipe';
import { compileBrief, effectiveFrameCount, effectiveResolution, findMentionQuery, insertReferenceMention, mentionedReferences, referenceDisplayTag, referenceTag } from './model.mjs?v=20261106-refmatch';
import { selectedReference } from './app-composer.mjs?v=20261106-refmatch';
import { officialControls } from './app-controls.mjs?v=20261106-refmatch';
import { activeCut, activeDirector, directorChoice, directorPresets } from './app-direction.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { renderExperiments } from './app-settings.mjs?v=20261106-refmatch';
import { $, allAssets, apiJson, audioRoles, files, imageRoles, resolvedProjectStyle, showToast, videoRoles } from './app-shared.mjs?v=20261106-refmatch';
import { renderReferencePreview, renderStage, setThumb } from './app-stage.mjs?v=20261106-refmatch';
import { replacementWorkspace } from './app-workflow.mjs?v=20261108-clean';

let renderedSubjectNames = [];

export function syncVisiblePictureBindings() {
  const input = $('#mainPrompt');
  const owners = referenceOwnerLabels(appState.project.references, input.value);
  const bindings = appState.project.references.map(ref => ({ ...ref, owner: ref.manualOwner ? (ref.owner || '') : (owners.get(ref.id) || ref.owner || '') }));
  let fixed;
  try { fixed = alignPromptPictures(input.value, bindings); }
  catch { return; } // Incomplete choices stay editable until the remaining roles are selected.
  if (fixed === input.value) return;
  activeCut().referenceBindingOriginalPrompt ??= input.value;
  const position = [input.selectionStart, input.selectionEnd];
  input.value = fixed; activeCut().prompt = fixed;
  input.setSelectionRange(...position);
  renderSelectedMentions();
}

export function refreshReferenceOwners() {
  const names = subjectNames(activeCut()?.prompt);
  if (JSON.stringify(names) !== JSON.stringify(renderedSubjectNames)) renderReferences();
}

export function videoWorkflowState(references, mode, duration) {
  return baseVideoWorkflowState(references, mode, duration, Boolean(activeCut()?.motionTemplateBackup));
}

export function renderReferences() {
  appState.project.styleAdapter = resolvedProjectStyle();
  renderPromptWarnings();
  const list = $('#referenceList');
  const previousScroll = list.scrollLeft;
  renderedSubjectNames = subjectNames(activeCut()?.prompt);
  list.replaceChildren();
  list.classList.toggle('is-empty', appState.project.references.length === 0);
  if (!appState.project.references.length) {
    const empty = document.createElement('div');
    empty.className = 'reference-empty';
    const title = document.createElement('strong');
    title.textContent = 'В сцене пока нет референсов';
    const detail = document.createElement('span');
    detail.textContent = allAssets.size
      ? `В библиотеке ${allAssets.size} файлов. Выбери нужные или загрузи новые ниже.`
      : 'Выбери файлы из библиотеки или загрузи новые ниже.';
    const choose = document.createElement('button');
    choose.type = 'button';
    choose.textContent = allAssets.size ? 'Выбрать из библиотеки' : 'Открыть библиотеку';
    choose.addEventListener('click', () => $('#openLibrary').click());
    empty.append(title, detail, choose);
    list.append(empty);
  }
  for (const ref of appState.project.references) {
    const card = document.createElement('div');
    card.className = `reference-card${ref.id === selectedReference ? ' selected' : ''}`;
    card.dataset.id = ref.id;
    const thumb = document.createElement('button');
    thumb.type = 'button';
    thumb.className = 'reference-thumb';
    thumb.setAttribute('aria-label', `Просмотреть ${ref.name}`);
    thumb.dataset.action = 'preview';
    const thumbMedia = document.createElement(ref.kind === 'image' ? 'img' : ref.kind === 'video' ? 'video' : 'span');
    if (ref.kind === 'audio') {
      thumbMedia.className = 'audio-thumb';
      thumbMedia.textContent = '♫';
      thumbMedia.setAttribute('aria-hidden', 'true');
    } else {
      if (ref.kind === 'image') { setThumb(thumbMedia, ref.id); thumbMedia.alt = ''; thumbMedia.loading = 'lazy'; }
      else thumbMedia.src = ref.preparedExcerptId ? `/api/assets/${encodeURIComponent(ref.preparedExcerptId)}/file` : files.get(ref.id)?.url || `/api/assets/${encodeURIComponent(ref.id)}/file`;
      if (ref.kind === 'image') { /* thumbnail set above */ }
      else {
        thumbMedia.muted = true;
        thumbMedia.preload = 'metadata';
        thumbMedia.addEventListener('loadedmetadata', () => {
          if (!Number.isFinite(thumbMedia.duration) || thumbMedia.duration <= 0) return;
          ref.sourceDuration = thumbMedia.duration;
          renderVideoWorkflow($('#videoWorkflow'), videoWorkflowState(appState.project.references, appState.project.mode, activeCut()?.duration));
        }, { once: true });
      }
    }
    thumb.append(thumbMedia);
    const info = document.createElement('div');
    info.className = 'reference-info';
    const tag = document.createElement('span');
    tag.className = 'reference-tag';
    tag.textContent = referenceDisplayTag(ref, appState.project.references);
    const name = document.createElement('span');
    name.className = 'reference-name';
    name.title = ref.name;
    name.textContent = ref.name;
    const role = document.createElement('select');
    role.className = 'reference-role';
    role.setAttribute('aria-label', `Роль файла ${ref.name}`);
    role.dataset.action = 'role';
    for (const value of ref.kind === 'image' ? imageRoles : ref.kind === 'video' ? videoRoles : audioRoles) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      role.append(option);
    }
    role.value = ref.role;
    info.append(tag, name, role);
    if ((ref.kind === 'image' && ['Лицо героя', 'Персонаж целиком', 'Костюм'].includes(ref.role)) ||
        (ref.kind === 'video' && ['Актёрская игра', 'Движение', 'Движение и камера'].includes(ref.role)) ||
        (ref.kind === 'audio' && ref.role === 'Голос')) {
      const state = referenceOwnerControlState(ref, activeCut()?.prompt);
      const owner = document.createElement(state.kind);
      owner.className = 'reference-owner';
      owner.dataset.action = 'owner';
      if (state.kind === 'select') {
        for (const [value, label] of [['', 'Выбери героя'], ...state.names.map((name) => [name, name])]) {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = label;
          owner.append(option);
        }
        owner.value = state.value;
      } else {
        owner.value = state.value;
        owner.placeholder = 'Имя из промпта · можно исправить';
        owner.maxLength = 80;
      }
      owner.setAttribute('aria-label', `Имя героя для ${ref.name}; определено из промпта, можно исправить`);
      info.append(owner);
    }
    const pairControl = pairingButton(ref, appState.project.references, activeCut()?.prompt || '');
    if (pairControl) info.append(pairControl);
    const maskControl = swapMaskButton(ref, appState.project, activeCut()?.referenceWindow?.start || 0);
    if (maskControl) info.append(maskControl);
    const excerpt = excerptButton(ref, () => activeCut()?.duration || 10, () => { renderReferences(); scheduleDraft(); }, apiJson, showToast);
    if (excerpt) info.append(excerpt);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'reference-remove';
    remove.dataset.action = 'remove';
    remove.setAttribute('aria-label', `Удалить ${ref.name}`);
    remove.textContent = '×';
    card.append(thumb, info, remove);
    list.append(card);
  }
  renderReferencePreview();
  list.scrollLeft = previousScroll;
  requestAnimationFrame(updateReferenceScroll);
  renderVideoWorkflow($('#videoWorkflow'), videoWorkflowState(appState.project.references, appState.project.mode, activeCut()?.duration));
  replacementWorkspace?.render();
  renderStage();
  renderBrief();
  renderMentionMenu();
  renderSelectedMentions();
  officialControls.render();
}

function updateReferenceScroll() {
  const list = $('#referenceList');
  $('#referenceScrollLeft').disabled = list.scrollLeft < 2;
  $('#referenceScrollRight').disabled = list.scrollLeft + list.clientWidth >= list.scrollWidth - 2;
}

export function installReferenceScroll() {
  for (const [id, direction] of [['#referenceScrollLeft', -1], ['#referenceScrollRight', 1]]) {
    $(id).addEventListener('click', () => $('#referenceList').scrollBy({ left: direction * Math.max(180, $('#referenceList').clientWidth * 0.8), behavior: 'smooth' }));
  }
  $('#referenceList').addEventListener('scroll', updateReferenceScroll, { passive: true });
  window.addEventListener('resize', updateReferenceScroll);
}

export function editVideoMask(ref) {
  openSwapMaskDialog(ref, `/api/assets/${encodeURIComponent(ref.id)}/file`, mask => {
    if (mask) ref.swapMask = mask; else delete ref.swapMask;
    renderReferences();
    scheduleDraft();
  }, activeCut()?.referenceWindow?.start || 0);
}

export function editVideoPairing(ref, prompt) {
  openPairingDialog(ref, heroNames(appState.project.references), prompt, spec => {
    ref.performers = spec;
    ref.owner = '';
    renderReferences();
    scheduleDraft();
  });
}

export function renderSelectedMentions() {
  const strip = $('#selectedMentions');
  strip.replaceChildren();
  const mentioned = new Set(mentionedReferences($('#mainPrompt').value, appState.project.references).map(ref => ref.id));
  const attached = appState.project.references.filter(ref => ref.role !== 'Продолжение');
  strip.hidden = attached.length === 0;
  for (const ref of attached) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'selected-mention';
    chip.dataset.id = ref.id;
    const source = mentioned.has(ref.id) ? 'указан в тексте' : 'привязан по роли файла';
    chip.title = `${referenceDisplayTag(ref, appState.project.references)} · ${ref.role} · ${ref.name} (${source})`;
    chip.setAttribute('aria-label', `Просмотреть ${referenceDisplayTag(ref, appState.project.references)}: ${ref.role}, ${ref.name}; ${source}`);
    const preview = document.createElement('span');
    preview.className = 'selected-mention-thumb';
    if (ref.kind === 'image') {
      const img = document.createElement('img');
      setThumb(img, ref.id);
      img.loading = 'lazy';
      img.alt = '';
      preview.append(img);
    } else {
      preview.textContent = ref.kind === 'video' ? '▶' : '♫';
    }
    const label = document.createElement('span');
    label.textContent = `${referenceDisplayTag(ref, appState.project.references)} · ${ref.role}`;
    chip.append(preview, label);
    strip.append(chip);
  }
}

export function matchingMentions() {
  if (!appState.mentionState) return [];
  const query = appState.mentionState.query.toLocaleLowerCase();
  return appState.project.references.filter((ref) => ref.role !== 'Продолжение' &&
    `${referenceTag(ref)} ${referenceDisplayTag(ref, appState.project.references)} ${ref.name} ${ref.role}`.toLocaleLowerCase().includes(query)
  );
}

export function renderMentionMenu() {
  const menu = $('#mentionMenu');
  menu.replaceChildren();
  const visible = Boolean(appState.mentionState);
  menu.hidden = !visible;
  $('#mainPrompt').setAttribute('aria-expanded', String(visible));
  if (!visible) return;
  const matches = matchingMentions();
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.className = 'mention-empty';
    empty.textContent = appState.project.references.length ? 'Нет подходящих референсов' : 'Сначала добавь фото, видео или звук';
    menu.append(empty);
    return;
  }
  appState.mentionState.active = Math.min(appState.mentionState.active, matches.length - 1);
  matches.forEach((ref, index) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = `mention-option${index === appState.mentionState.active ? ' active' : ''}`;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(index === appState.mentionState.active));
    option.dataset.id = ref.id;
    const icon = document.createElement('span');
    icon.className = 'mention-thumb';
    if (ref.kind === 'image') {
      const image = document.createElement('img');
      setThumb(image, ref.id);
      image.loading = 'lazy';
      image.alt = '';
      icon.append(image);
    } else {
      icon.textContent = ref.kind === 'video' ? '▶' : '♫';
    }
    const info = document.createElement('span');
    info.className = 'mention-info';
    const tag = document.createElement('strong');
    tag.textContent = referenceDisplayTag(ref, appState.project.references);
    const name = document.createElement('small');
    name.textContent = `${ref.role} · ${ref.name}`;
    info.append(tag, name);
    option.append(icon, info);
    menu.append(option);
  });
}

export function updateMentionFromPrompt() {
  const prompt = $('#mainPrompt');
  const found = findMentionQuery(prompt.value, prompt.selectionStart);
  appState.mentionState = found ? { ...found, active: 0 } : null;
  renderMentionMenu();
}

export function selectMention(ref) {
  if (!appState.mentionState) return;
  const prompt = $('#mainPrompt');
  const { start, end, direct } = appState.mentionState;
  const tag = referenceTag(ref);
  const insertion = direct
    ? `${start > 0 && !/\s/.test(prompt.value[start - 1]) ? ' ' : ''}${tag}${end < prompt.value.length && !/\s/.test(prompt.value[end]) ? ' ' : ''}`
    : tag;
  const changed = insertReferenceMention(prompt.value, prompt.selectionStart, direct ? end : prompt.selectionEnd, start, insertion);
  prompt.value = changed.text;
  prompt.focus();
  prompt.setSelectionRange(changed.caret, changed.caret);
  appState.mentionState = null;
  renderMentionMenu();
  prompt.dispatchEvent(new Event('input', { bubbles: true }));
}

export function renderBrief() {
  const directorNotes = DIRECTOR_CATEGORIES.flatMap(([key, label]) => directorChoice(key) ? [`${label}: ${directorChoice(key).name} — ${directorChoice(key).prompt}`] : []);
  $('#brief').textContent = compileBrief(appState.project) + (directorNotes.length ? `\n\nРежиссура кадра:\n${directorNotes.join('\n')}` : '');
  const firstCut = activeCut();
  if (document.activeElement !== $('#duration')) $('#duration').value = firstCut?.duration ?? 5;
  renderEffectiveSize();
  renderPromptWarnings();
}
// Non-blocking: an owner name absent from the prompt, or a prompt far beyond the MiniMax guide.
function renderPromptWarnings() {
  const host = $('#promptWarnings');
  if (!host) return;
  const warnings = promptWarnings(activeCut()?.prompt || '', appState.project.references, { en: getLocale() === 'en' });
  host.hidden = !warnings.length;
  host.textContent = warnings.join(' ');
}
// Display only: the lens of a camera move with the sensor's field of view (settingsSnapshot decides what is sent).
function opticsLine(frames) {
  if (appState.project.references.some((ref) => ref.kind === 'video' && ['Камера', 'Движение и камера'].includes(ref.role))) return '';
  const presets = [...DIRECTOR_PRESETS, ...directorPresets()];
  const promptCamera = parsePromptDirection(activeCut().prompt, directorPresets()).camera || {};
  const cameraState = cameraStateForPresets(activeDirector(), presets, frames, activeCut().prompt, promptCamera);
  const optics = cameraState && cameraOpticsForPresets(activeDirector(), presets, cameraState);
  return optics ? ` Оптика: ${opticsSummary(optics, cameraState.keys[0].fov, getLocale())}.` : '';
}

export function renderEffectiveSize() {
  renderExperiments?.render();
  const summary = $('#executionSummary');
  try {
    const size = effectiveResolution(appState.project.aspect, appState.project.quality, appState.project.megapixels);
    $('#effectiveSize').textContent = `${size.width}×${size.height}`;
    if (/⟦(?:удалённый|неизвестный) (?:Picture|Video|Audio) \d+⟧/.test(activeCut().prompt)) {
      summary.textContent = 'В промпте есть ссылка на удалённый или неизвестный файл. Замените её через @ перед отправкой.';
      return;
    }
    const seconds = Number(activeCut()?.duration);
    if (!Number.isInteger(Number(activeCut().duration)) || Number(activeCut().duration) < 4 || Number(activeCut().duration) > 15) {
      summary.textContent = 'Для каждой сцены выберите длительность 4–15 сек.';
      return;
    }
    const frames = effectiveFrameCount(seconds);
    const style = resolvedProjectStyle();
    const replacement = appState.project.mode === 'replace' && appState.project.references.some(ref => ref.kind === 'video')
      && appState.project.references.some(ref => ref.kind === 'image');
    const adapterSummary = style !== 'none' ? `LoRA ${style}` : replacement
      ? 'Character Swap LoRA (экспериментально)' : appState.project.mode === 'realistic'
        ? 'LoRA: автовыбор на сервере (может быть без LoRA)' : 'без LoRA';
    const modules = [adapterSummary,
      `кеш ${appState.project.cacheMode || 'safe'}`, 'нативный стереозвук'];
    summary.textContent = (appState.project.cuts.length > 1 ? `Отправляется только выбранная сцена (${appState.project.cuts.findIndex(c => c.id === activeCut().id) + 1} из ${appState.project.cuts.length}). ` : '') + `H3 получит: ${size.width}×${size.height} · ${seconds} сек → ${frames} кадров (${(frames / 24).toFixed(2)} сек при 24 fps) · ${appState.project.steps} шагов · ${renderProfileLabel(appState.project.profile)} · ${modules.join(' · ')}.${opticsLine(frames)} Для короткой проверки выбери 5 сек.`;
  } catch {
    $('#effectiveSize').textContent = 'Недопустимый размер';
    summary.textContent = 'Проверьте размер: параметр H3 от 0,20 до 0,98 и не выше нативного лимита.';
  }
}

export function renderCuts() {
  const timeline = $('#timeline');
  timeline.replaceChildren();
  appState.project.cuts.forEach((cut, index) => {
    const card = document.createElement('article');
    card.className = `cut${cut.id === activeCut().id ? ' selected' : ''}`;
    card.dataset.id = cut.id;
    const marker = document.createElement('div');
    marker.className = 'cut-index';
    marker.textContent = String(index + 1).padStart(2, '0');
    const body = document.createElement('div');
    body.className = 'cut-body';
    const top = document.createElement('div');
    top.className = 'cut-top';
    const label = document.createElement('button');
    label.type = 'button'; label.dataset.action = 'select';
    label.textContent = `Кадр ${String(index + 1).padStart(2, '0')}`;
    const controls = document.createElement('div');
    controls.className = 'cut-controls';
    const duration = document.createElement('input');
    duration.className = 'cut-duration';
    duration.type = 'number';
    duration.min = '4';
    duration.max = '15';
    duration.value = cut.duration;
    duration.dataset.action = 'duration';
    duration.setAttribute('aria-label', `Длительность сцены ${index + 1} в секундах`);
    const seconds = document.createElement('span');
    seconds.className = 'cut-seconds';
    seconds.textContent = 'сек';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'cut-remove';
    remove.dataset.action = 'remove';
    remove.setAttribute('aria-label', `Удалить сцену ${index + 1}`);
    remove.disabled = appState.project.cuts.length === 1;
    remove.textContent = '×';
    controls.append(duration, seconds, remove);
    top.append(label, controls);
    const preview = document.createElement('p');
    preview.className = 'cut-preview';
    preview.textContent = shotSummary(cut.prompt) || 'Без описания';
    const state = document.createElement('span');
    state.className = 'cut-state';
    state.textContent = cut.prompt?.trim() ? 'Описание готово' : 'Черновик';
    body.append(state);
    body.append(top, preview);
    card.append(marker, body);
    timeline.append(card);
  });
  $('#sceneCount').textContent = String(appState.project.cuts.length);
  $('#activeShotLabel').textContent = `· кадр ${String(appState.project.cuts.indexOf(activeCut()) + 1).padStart(2, '0')}`;
  renderBrief();
}
