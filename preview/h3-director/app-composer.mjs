// Create: prompt input, mentions menu, drop zones and the reference list actions.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { installPromptDirectionHelp } from './prompt-direction.mjs?v=20261008-auto-camera-1';
import { shotSummary } from './workspace-context.mjs?v=20261005-1';
import { promptSettingsPackage } from './prompt-settings-package.mjs?v=20261008-1';
import { referenceTag, removeReference } from './model.mjs?v=20261106-refmatch';
import { apiReady } from './app-bootstrap.mjs?v=20261106-refmatch';
import { activeCut, activeDirector, directorPresets, renderDirectorStrip, syncPromptDirection } from './app-direction.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { addFiles } from './app-library.mjs?v=20261108-clean';
import { editVideoMask, editVideoPairing, matchingMentions, refreshReferenceOwners, renderBrief, renderCuts, renderMentionMenu, renderReferences, renderSelectedMentions, selectMention, syncVisiblePictureBindings, updateMentionFromPrompt } from './app-references.mjs?v=20261108-clean';
import { $, files, showToast } from './app-shared.mjs?v=20261106-refmatch';
import { renderStage } from './app-stage.mjs?v=20261106-refmatch';

export let selectedReference = null;
export let promptDirectionHelp;

export function installComposer() {
  $('#moreSettings').addEventListener('click', () => {
    $('#createControls').open = true;
    const details = $('.output-details');
    details.open = true;
    details.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
  $('#editAgain').addEventListener('click', () => { $('#composer').scrollIntoView({ behavior: 'smooth', block: 'center' }); $('#mainPrompt').focus(); });
  $('#mainPrompt').addEventListener('input', (event) => {
    syncPromptDirection();
    syncVisiblePictureBindings();
    refreshReferenceOwners();
    try {
      const pack = promptSettingsPackage(event.target.value);
      const values = pack.overrides;
      for (const [key, property, id] of [
        ['aspect_ratio','aspect','aspect'], ['mode','mode','modeSelect'], ['steps','steps','steps'],
        ['ref_image_size','refImageSize','refImageSize'], ['cache_mode','cacheMode','cacheMode'],
        ['sampling_profile','samplingProfile','samplingProfile'], ['seed','seed','seed']]) {
        if (values[key] !== undefined) { appState.project[property] = values[key]; if ($('#'+id)) $('#'+id).value = values[key]; }
      }
      if (Number.isInteger(values.duration) && values.duration >= 4 && values.duration <= 15) {
        activeCut().duration = values.duration; $('#duration').value = values.duration;
      }
      if (typeof values.megapixels === 'number' && values.megapixels >= .4 && values.megapixels <= .98) {
        appState.project.quality = 'custom'; appState.project.megapixels = values.megapixels; $('#quality').value = values.megapixels;
        $('#qualityPreset').value = [0.4, 0.6, 0.7, 0.8].find(v => Math.abs(v - values.megapixels) < 0.005)?.toFixed(2) || '';
        $('#megapixelValue').textContent = `${values.megapixels.toFixed(2).replace('.', ',')} Мп`;
      }
      if (values.use_video_audio !== undefined) { appState.project.useVideoAudio = values.use_video_audio; $('#useVideoAudio').checked = values.use_video_audio; }
      $('#promptSettingsReport').textContent = Object.keys(pack.overrides).length ? 'Из промпта применятся: ' + Object.entries(pack.overrides).filter(([,value]) => value !== null).map(([key,value]) => `${({duration:'Длительность',aspect_ratio:'Формат',mode:'Режим',steps:'Шаги',megapixels:'Разрешение, Мп',style_adapter:'LoRA',cache_mode:'Кэш',sampling_profile:'Сэмплер',seed:'Seed',ref_image_size:'Размер референсов',quality:'Качество',use_video_audio:'Звук видео'})[key] || 'Режиссура'}: ${typeof value === 'object' ? 'из промпта' : ({realistic:'реализм',auto:'авто',none:'без LoRA',off:'выключен',standard:'стандартный',max:'максимум',custom:'свой'})[value] || String(value)}`).join(' · ') : '';
    } catch (error) { $('#promptSettingsReport').textContent = error.message; }
    renderDirectorStrip();
    scheduleDraft();
    const preview = $('#timeline .cut.selected .cut-preview');
    if (preview) preview.textContent = shotSummary(event.target.value) || 'Без описания';
    const state = $('#timeline .cut.selected .cut-state');
    if (state) state.textContent = event.target.value.trim() ? 'Описание готово' : 'Черновик';
    renderStage();
    renderBrief();
    renderSelectedMentions();
    updateMentionFromPrompt();
  });
  $('#selectedMentions').addEventListener('click', (event) => {
    const id = event.target.closest('[data-id]')?.dataset.id;
    if (!id) return;
    selectedReference = id;
    renderReferences();
  });
  $('#closeReferencePreview').addEventListener('click', () => {
    selectedReference = null;
    renderReferences();
  });
  promptDirectionHelp = installPromptDirectionHelp({
    input: $('#mainPrompt'), getState: activeDirector, getCustom: directorPresets,
    changed: () => $('#mainPrompt').dispatchEvent(new Event('input', { bubbles: true })), toast: showToast,
  });
  $('#mainPrompt').addEventListener('click', updateMentionFromPrompt);
  $('#mainPrompt').addEventListener('keydown', (event) => {
    if (!appState.mentionState) return;
    const matches = matchingMentions();
    if (event.key === 'Escape') { event.preventDefault(); appState.mentionState = null; renderMentionMenu(); return; }
    if (!matches.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      appState.mentionState.active = (appState.mentionState.active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
      renderMentionMenu();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      selectMention(matches[appState.mentionState.active]);
    }
  });
  $('#mentionTrigger').addEventListener('click', () => {
    const prompt = $('#mainPrompt');
    const start = prompt.selectionStart;
    const end = prompt.selectionEnd;
    prompt.focus();
    appState.mentionState = { start, end, query: '', active: 0, direct: true };
    renderMentionMenu();
  });
  $('#mentionMenu').addEventListener('pointerdown', (event) => event.preventDefault());
  $('#mentionMenu').addEventListener('click', (event) => {
    const id = event.target.closest('[data-id]')?.dataset.id;
    const ref = appState.project.references.find((item) => item.id === id);
    if (ref) selectMention(ref);
  });
  const dropzone = $('#dropzone');
  dropzone.addEventListener('click', () => $('#mediaInput').click());
  dropzone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('#mediaInput').click(); } });
  $('#mediaInput').addEventListener('change', async (event) => { await addFiles([...event.target.files]); event.target.value = ''; });
  for (const [zoneId, inputId, role] of [
    ['faceDropzone', 'faceInput', 'Лицо героя'],
    ['costumeDropzone', 'costumeInput', 'Костюм'],
  ]) {
    const zone = $(`#${zoneId}`);
    const input = $(`#${inputId}`);
    zone.addEventListener('click', () => input.click());
    zone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.click(); } });
    input.addEventListener('change', async (event) => { await addFiles([...event.target.files], role); event.target.value = ''; });
    for (const name of ['dragenter', 'dragover']) zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add('dragging'); });
    for (const name of ['dragleave', 'drop']) zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove('dragging'); });
    zone.addEventListener('drop', (event) => { void addFiles([...event.dataTransfer.files], role); });
  }
  const sceneDropzone = $('#sceneDropzone');
  sceneDropzone.addEventListener('click', () => $('#sceneInput').click());
  sceneDropzone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('#sceneInput').click(); } });
  $('#sceneInput').addEventListener('change', async (event) => { await addFiles([...event.target.files], 'Окружение'); event.target.value = ''; });
  for (const name of ['dragenter', 'dragover']) sceneDropzone.addEventListener(name, (event) => { event.preventDefault(); sceneDropzone.classList.add('dragging'); });
  for (const name of ['dragleave', 'drop']) sceneDropzone.addEventListener(name, (event) => { event.preventDefault(); sceneDropzone.classList.remove('dragging'); });
  sceneDropzone.addEventListener('drop', (event) => { void addFiles([...event.dataTransfer.files], 'Окружение'); });
  for (const name of ['dragenter', 'dragover']) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add('dragging'); });
  for (const name of ['dragleave', 'drop']) dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); });
  dropzone.addEventListener('drop', (event) => { void addFiles([...event.dataTransfer.files]); });
  document.addEventListener('paste', (event) => {
    const pasted = [...(event.clipboardData?.files || [])];
    if (pasted.length) { event.preventDefault(); void addFiles(pasted); }
  });
  $('#referenceList').addEventListener('click', (event) => {
    const action = event.target.closest('[data-action]');
    const id = event.target.closest('[data-id]')?.dataset.id;
    if (!action || !id) return;
    // Editing controls must keep their DOM node and focus. Rebuilding the list on
    // a click used to replace the input before the user could type a character.
    if (action.dataset.action === 'owner' || action.dataset.action === 'role') return;
    if (action.dataset.action === 'swap-mask') {
      const ref = appState.project.references.find(item => item.id === id);
      if (ref) editVideoMask(ref);
      return;
    }
    if (action.dataset.action === 'pairing') {
      const ref = appState.project.references.find(item => item.id === id);
      if (ref) editVideoPairing(ref, activeCut()?.prompt || '');
      return;
    }
    if (action.dataset.action === 'preview') selectedReference = id;
    if (action.dataset.action === 'remove') {
      if (!apiReady) URL.revokeObjectURL(files.get(id).url);
      files.delete(id);
      const referenced = appState.project.cuts.some((cut) => cut.prompt.includes(referenceTag(appState.project.references.find((ref) => ref.id === id))));
      appState.project = removeReference(appState.project, id);
      if (referenced) showToast('Упоминание удалённого файла помечено в промпте. Исправьте его перед отправкой.');
      $('#mainPrompt').value = activeCut().prompt;
      renderCuts();
      if (selectedReference === id) selectedReference = null;
    }
    renderReferences();
    if (action.dataset.action === 'remove') scheduleDraft();
  });
  $('#referenceList').addEventListener('change', (event) => {
    const id = event.target.closest('[data-id]')?.dataset.id;
    const ref = appState.project.references.find((item) => item.id === id);
    if (!ref) return;
    if (event.target.dataset.action === 'role') {
      ref.role = event.target.value;
      if (!['Движение', 'Движение и камера', 'Исходное видео'].includes(ref.role)) delete ref.performers;
      if (!['Лицо героя', 'Персонаж целиком', 'Костюм', 'Актёрская игра', 'Движение', 'Движение и камера', 'Голос'].includes(ref.role)) ref.owner = '';
      syncVisiblePictureBindings();
      renderReferences();
      scheduleDraft();
      return;
    }
    if (event.target.dataset.action === 'owner') { ref.owner = event.target.value.trim(); ref.manualOwner = true; }
    syncVisiblePictureBindings();
    renderBrief();
    scheduleDraft();
  });
  $('#referenceList').addEventListener('input', (event) => {
    if (event.target.dataset.action !== 'owner') return;
    const id = event.target.closest('[data-id]')?.dataset.id;
    const ref = appState.project.references.find((item) => item.id === id);
    if (ref) { ref.owner = event.target.value.trim(); ref.manualOwner = true; }
  });
}
