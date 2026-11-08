// Create: scenario (mode) switch, video workflow cards and the character-replacement guide.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { motionReferenceTemplate } from './motion-camera-template.mjs?v=20261029-scene-run-2';
import { getPreset } from './director-presets.mjs?v=20261031-lighting-15-1';
import { openLongScene } from './long-scene.mjs?v=20261006-auto-2';
import { renderVideoWorkflow } from './video-workflow.mjs?v=20261022-swap-recipe';
import { installReplacementWorkspace } from './replacement-workspace.mjs?v=20261108-no-bundled-recipes';
import { defaultStyleAdapterForMode, modes } from './model.mjs?v=20261106-refmatch';
import { activeCut, activeDirector, directorPresets } from './app-direction.mjs?v=20261106-refmatch';
import { persistDraft, scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { editVideoPairing, renderBrief, renderReferences, videoWorkflowState } from './app-references.mjs?v=20261108-clean';
import { $, apiJson, modeDescriptions, showToast } from './app-shared.mjs?v=20261106-refmatch';

export let replacementWorkspace;

export function installVideoWorkflow() {
  apiJson('/api/recipes/accepted').then(recipes => {
    const list = $('#replacementAcceptedCatalog');
    list.replaceChildren(...recipes.map(recipe => {
      const item = document.createElement('li');
      item.textContent = `${recipe.title} · ${recipe.compiled_profile?.profile || 'профиль неизвестен'}, ${recipe.compiled_profile?.steps || '?'} шага, seed ${recipe.compiled_profile?.seed ?? '?'} · job ${recipe.job_id.slice(0, 8)}. ${recipe.scope || ''}`;
      return item;
    }));
    if (!recipes.length) list.textContent = 'Принятых рецептов пока нет.';
  }).catch(() => { $('#replacementAcceptedCatalog').textContent = 'Реестр недоступен; проверь Studio.'; });
  $('#modeSelect').addEventListener('change', (event) => {
    appState.project.mode = event.target.value;
    appState.project.styleAdapter = defaultStyleAdapterForMode(appState.project.mode);
    $('#modeTitle').textContent = modes[appState.project.mode];
    $('#modeDescription').textContent = modeDescriptions[appState.project.mode];
    renderVideoWorkflow($('#videoWorkflow'), videoWorkflowState(appState.project.references, appState.project.mode, activeCut()?.duration));
    renderBrief();
    scheduleDraft();
  });
  $('#videoWorkflow').addEventListener('click', async (event) => {
    const action = event.target.closest('[data-video-action]')?.dataset.videoAction;
    if (!action) return;
    const video = appState.project.references.find(ref => ref.kind === 'video' && ref.role !== 'Продолжение');
    if (!video) return;
    if (action === 'long-scene') {
      try {
        await openLongScene({ seconds: Math.floor(video.sourceDuration), project: appState.project, prompt: $('#mainPrompt').value,
          save: persistDraft, navigate: url => { location.href = url; } });
      } catch (error) { showToast(`Не удалось сохранить сцену: ${error.message}`); }
      return;
    }
    if (action === 'motion-template-restore') {
      const cut = activeCut();
      const backup = cut.motionTemplateBackup;
      if (!backup) return;
      if (backup.role) video.role = backup.role;
      if (backup.retention !== undefined) video.retention = backup.retention;
      if (backup.mode) { appState.project.mode = backup.mode; $('#modeSelect').value = backup.mode; }
      if (backup.styleAdapter !== undefined) appState.project.styleAdapter = backup.styleAdapter;
      $('#mainPrompt').value = backup.prompt;
      delete cut.motionTemplateBackup;
      $('#mainPrompt').dispatchEvent(new Event('input', { bubbles: true }));
      renderReferences(); scheduleDraft();
      showToast('Исходный текст и роль видео восстановлены.');
      return;
    }
    if (action?.startsWith('motion-template')) {
      try {
        const cut = activeCut();
        const variant = action === 'motion-template-only' ? 'motion_only' : action === 'motion-template-scene' ? 'motion_camera_scene' : 'motion_camera';
        const cameraBrief = variant === 'motion_only' ? ['camera', 'lens', 'shot', 'movement', 'focus'].map(key => getPreset(activeDirector()[key], directorPresets())?.prompt).filter(Boolean).join(' ') : '';
        const sceneBrief = variant === 'motion_only' ? [cut.sceneShot?.location, getPreset(activeDirector().light, directorPresets())?.prompt].filter(Boolean).join(' ') : '';
        const template = motionReferenceTemplate(appState.project.references, cut.prompt, { useVideoAudio: !!appState.project.useVideoAudio, variant, cameraBrief, sceneBrief });
        cut.motionTemplateBackup ||= { prompt: cut.prompt, role: video.role, retention: video.retention, mode: appState.project.mode, styleAdapter: appState.project.styleAdapter, at: new Date().toISOString() };
        video.role = variant === 'motion_only' ? 'Движение' : 'Движение и камера';
        video.retention = 'attribute_transfer';
        if (variant === 'motion_only' && appState.project.mode === 'replace') { $('#modeSelect').value = 'realistic'; $('#modeSelect').dispatchEvent(new Event('change', { bubbles: true })); }
        $('#mainPrompt').value = template;
        $('#mainPrompt').dispatchEvent(new Event('input', { bubbles: true }));
        renderReferences();
        scheduleDraft();
        showToast('Шаблон применён. Источники внешности и костюма назначены герою; прежний текст сохранён в сцене.');
      } catch (error) { showToast(error.message); }
      return;
    }
    if (action === 'motion' || action === 'motion-camera' || action === 'source-scene') {
      video.role = { motion: 'Движение', 'motion-camera': 'Движение и камера', 'source-scene': 'Исходное видео' }[action];
      renderReferences();
      scheduleDraft();
    } else if (action === 'realistic') {
      $('#modeSelect').value = 'realistic';
      $('#modeSelect').dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  replacementWorkspace = installReplacementWorkspace({
    panel: $('#replacementGuide'), trigger: $('#showReplacement'),
    getState: () => ({ references: appState.project.references, duration: activeCut()?.duration || 0 }),
    openLibrary: () => $('#openLibrary').click(),
    pair: () => {
      const video = appState.project.references.find(ref => ref.kind === 'video' && ref.role !== 'Продолжение');
      if (video) editVideoPairing(video, activeCut()?.prompt || '');
    },
    applyTemplate: () => {
      const video = appState.project.references.find(ref => ref.kind === 'video' && ref.role !== 'Продолжение');
      if (video && video.role !== 'Движение и камера') { video.role = 'Движение и камера'; renderReferences(); scheduleDraft(); }
      const button = $('#videoWorkflow [data-video-action="motion-template-source"]');
      if (button) button.click();
      else showToast('Укажи видео с ролью «Движение и камера», затем примени шаблон.');
    },
    planLongVideo: () => {
      const button = $('#videoWorkflow [data-video-action="long-scene"]');
      if (button) button.click();
    },
  });
}
