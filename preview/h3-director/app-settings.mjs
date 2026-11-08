// Create: output settings — format, duration, seed, quality, steps, cache, sampling, render experiments.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { bindAspectSelect } from './aspects.mjs?v=20261106-aspects-1';
import { bindCacheControls } from './cache-controls.mjs?v=20261009-1';
import { installRenderExperiments } from './render-experiments.mjs?v=20261021-2';
import { bindSwapLora } from './swap-lora.mjs?v=20261104-swap15-1';
import { bindSamplingProfile } from './sampling-profile.mjs?v=20261104-refbeta-1';
import { openLongScene } from './long-scene.mjs?v=20261006-auto-2';
import { renderVideoWorkflow } from './video-workflow.mjs?v=20261022-swap-recipe';
import { renumberReferences } from './model.mjs?v=20261106-refmatch';
import { activeCut } from './app-direction.mjs?v=20261106-refmatch';
import { persistDraft, scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { renderBrief, renderCuts, renderEffectiveSize, renderReferences, videoWorkflowState } from './app-references.mjs?v=20261108-clean';
import { $, apiJson, showToast } from './app-shared.mjs?v=20261106-refmatch';
import { renderStage } from './app-stage.mjs?v=20261106-refmatch';

export let renderExperiments;
export let refreshCacheControls;

export function installProjectSettings() {
  // Formats from the GPU's /api/capabilities, not a fixed pair.
  bindAspectSelect($('#aspect'), () => appState.project.aspect);
  $('#aspect').addEventListener('change', (event) => { appState.project.aspect = event.target.value; renderBrief(); renderStage(); scheduleDraft(); });
  $('#duration').addEventListener('input', (event) => {
    const seconds = Number(event.target.value);
    if (Number.isInteger(seconds) && seconds > 15 && seconds <= 180) {
      $('#executionSummary').textContent = `${seconds} сек: после ввода Studio откроет план сцены из шотов до 15 сек.`;
      return;
    }
    if (!Number.isInteger(seconds) || seconds < 4 || seconds > 15) {
      $('#executionSummary').textContent = 'Длина ролика: выберите целое число от 4 до 15 секунд.';
      return;
    }
    activeCut().duration = seconds;
    renderReferences();
    renderVideoWorkflow($('#videoWorkflow'), videoWorkflowState(appState.project.references, appState.project.mode, seconds));
    const timelineDuration = $('#timeline .cut.selected .cut-duration');
    if (timelineDuration) timelineDuration.value = seconds;
    renderStage();
    renderBrief();
    scheduleDraft();
  });
  $('#duration').addEventListener('change', async (event) => {
    try {
      await openLongScene({ seconds: Number(event.target.value), project: appState.project, prompt: $('#mainPrompt').value,
        save: persistDraft, navigate: (url) => { location.href = url; } });
    } catch (error) { showToast(`Не удалось сохранить сцену: ${error.message}`); }
  });
  for (const type of ['input', 'change']) $('#seed').addEventListener(type, (event) => { appState.project.seed = event.target.value; scheduleDraft(); });
  $('#quality').addEventListener('input', (event) => {
    appState.project.quality = 'custom';
    appState.project.megapixels = Number(event.target.value);
    const presets = [0.4, 0.6, 0.7, 0.8]; // user 06.11: 0.8 is the ceiling, upscale afterwards
    const match = presets.find((value) => Math.abs(value - appState.project.megapixels) < 0.005);
    $('#qualityPreset').value = match == null ? '' : String(match.toFixed(2));
    $('#megapixelValue').textContent = `${appState.project.megapixels.toFixed(2).replace('.', ',')} Мп`;
    // The output size does not decide reference detail: the user's choice in that select stays.
    renderBrief();
    scheduleDraft();
  });
  $('#qualityPreset').addEventListener('change', (event) => {
    const value = Number(event.target.value);
    if (!event.target.value || !Number.isFinite(value)) return;
    $('#quality').value = String(value);
    $('#quality').dispatchEvent(new Event('input', { bubbles: true }));
  });
  $('#steps').addEventListener('input', (event) => { appState.project.steps = Number(event.target.value); scheduleDraft(); });
  $('#refImageSize').addEventListener('change', (event) => { appState.project.refImageSize = event.target.value; scheduleDraft(); });
  $('#cacheMode').addEventListener('change', (event) => { appState.project.cacheMode = event.target.value; renderEffectiveSize(); scheduleDraft(); });
  refreshCacheControls = bindCacheControls($('#cacheMode'), () => appState.project, scheduleDraft);
  bindSamplingProfile($('#samplingProfile'), () => appState.project, scheduleDraft);
  bindSwapLora($('#swapLora'), () => appState.project, scheduleDraft);
  renderExperiments = installRenderExperiments({ getProject: () => appState.project,
    onChange: () => { renderEffectiveSize(); scheduleDraft(); }, request: apiJson });
  $('#useVideoAudio').addEventListener('change', (event) => {
    const wasEnabled = appState.project.useVideoAudio;
    appState.project.useVideoAudio = event.target.checked;
    appState.project = renumberReferences(appState.project, appState.project.references, wasEnabled);
    $('#mainPrompt').value = appState.project.cuts[0].prompt;
    renderReferences(); renderCuts(); scheduleDraft();
  });
  $('#copyBrief').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#brief').textContent); showToast('Задание скопировано'); }
    catch { showToast('Не удалось скопировать. Выдели текст задания вручную.'); }
  });
}
