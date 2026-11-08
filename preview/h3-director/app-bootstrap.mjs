// Startup: hydrate the saved draft and library before enabling actions.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { selectAspect } from './aspects.mjs?v=20261106-aspects-1';
import { startStudioLoad } from './studio-startup.mjs?v=20261024-1';
import { loadFranchiseMemberships } from './library-collections.mjs?v=20261030-library-roles-1';
import { activeSideJobs } from './job-feed.mjs?v=20261106-aspects-1';
import { applyVideoHealth } from './video-backend.mjs?v=20261108-api-1';
import { startStudioPolling } from './studio-polling.mjs?v=20261008-audit-fixes-1';
import { assetsWorkspace } from './assets-workspace.mjs?v=20261102-talemondo-1';
import { createProject, renumberReferences } from './model.mjs?v=20261106-refmatch';
import { normalizeSceneProject } from './scene-project-model.mjs?v=20261031-render-state-1';
import { actingControl, officialControls } from './app-controls.mjs?v=20261106-refmatch';
import { renderDirectorStrip, syncPromptDirection } from './app-direction.mjs?v=20261106-refmatch';
import { renderLibrary, renderLibraryTypes, showLibraryTab } from './app-library.mjs?v=20261108-clean';
import { gpuActivity, knownJobs, knownSideJobs, knownUpscales, productionMode, refreshJobs } from './app-queue.mjs?v=20261106-refmatch';
import { renderCuts, renderReferences } from './app-references.mjs?v=20261108-clean';
import { refreshCacheControls, renderExperiments } from './app-settings.mjs?v=20261106-refmatch';
import { $, allAssets, apiJson, files, showToast } from './app-shared.mjs?v=20261106-refmatch';

export let apiReady = false;
export let hydrating = true;
export let jobsPolling = null;
export let enabledStyleAdapters = [];
export let franchiseMemberships = new Map();

export async function bootstrap() {
  // Keep the page under the boot cover (page-boot.js) until the draft and the library are on screen;
  // jobs and GPU health come later (health may wait 4 s for a dead tunnel).
  let hydrated = () => {};
  window.studioBoot?.hold(new Promise((resolve) => { hydrated = resolve; }));
  renderReferences(); renderCuts(); renderDirectorStrip(); renderLibraryTypes();
  try {
    const loading = startStudioLoad(apiJson);
    void loading.health.then(health => {
      enabledStyleAdapters = health.gpu_reachable ? (health.style_adapters || []) : [];
      applyVideoHealth(health);
    });
    const [assets, draft] = await loading.workspace;
    const { setDraftRevision } = await import('./app-draft.mjs?v=20261106-refmatch');
    setDraftRevision(draft.revision);
    void renderExperiments.refresh();
    void officialControls.refreshCapabilities();
    apiReady = true;
    $('#enqueue').disabled = false;
    hydrating = true;
    assets.forEach((asset) => allAssets.set(asset.asset_id, asset));
    try { franchiseMemberships = await loadFranchiseMemberships(apiJson, allAssets); }
    catch (error) { console.warn('Картотека франшиз недоступна:', error); }
    if (draft.project) {
      appState.project = normalizeSceneProject({ ...createProject(), ...draft.project });
      if (!Object.keys(appState.project.director || {}).length && appState.project.direction) {
        const legacy = appState.project.direction;
        const mappings = { camera: { static: 'movement.static', push: 'movement.push', pull: 'movement.pull', orbit: 'movement.orbit', handheld: 'movement.handheld' },
          lighting: { natural: 'lighting.natural', window: 'lighting.window', practical: 'lighting.practical', silhouette: 'lighting.silhouette', lowkey: 'lighting.lowkey' },
          palette: { neutral: 'color.neutral', warm: 'color.warm', cool: 'color.cool', noir: 'color.desaturated', vivid: 'color.vivid' },
          style: { cinematic: 'style.realism', documentary: 'style.documentary', film: 'style.film', animation: 'style.animation3d', illustration: 'style.animation2d' } };
        appState.project.director = {};
        for (const [key, values] of Object.entries(mappings)) {
          if (values[legacy[key]]) appState.project.director[key === 'palette' ? 'color' : key === 'camera' ? 'movement' : key] = values[legacy[key]];
        }
      }
      renderDirectorStrip();
      if (appState.project.quality !== 'custom') {
        appState.project.megapixels = ({ compact: 0.5, quick: 0.8, balanced: 0.9, max: 0.98 }[appState.project.quality] || appState.project.megapixels || 0.8);
        appState.project.quality = 'custom';
      }
      // Never drop a reference silently: a library answer fetched while Studio was still starting
      // removed «image.png» from a scene on 2026-10-08. Missing files are kept and named instead;
      // the Mac queue rejects a job whose file really is gone with a clear error.
      const missingRefs = (appState.project.references || []).filter((ref) => !allAssets.has(ref.id));
      if (missingRefs.length) showToast(`Не найдены в библиотеке: ${missingRefs.map((ref) => ref.name || ref.id.slice(0, 8)).join(', ')}. Проверь референсы перед запуском.`);
      appState.project.references = (appState.project.references || [])
        .map((ref) => ({ ...ref, soundtrack_number: allAssets.get(ref.id)?.soundtrack_number ?? ref.soundtrack_number }));
      appState.project = renumberReferences(appState.project);
      appState.project.references.forEach((ref) => files.set(ref.id, { url: `/api/assets/${ref.id}/file` }));
      $('#mainPrompt').value = appState.project.cuts?.[0]?.prompt || '';
      syncPromptDirection();
      renderDirectorStrip();
      $('#modeSelect').value = appState.project.mode;
      selectAspect($('#aspect'), appState.project.aspect);
      $('#quality').value = appState.project.megapixels;
      $('#qualityPreset').value = [0.4, 0.6, 0.7, 0.8].find((value) => Math.abs(value - appState.project.megapixels) < 0.005)?.toFixed(2) || '';
      $('#megapixelValue').textContent = `${Number(appState.project.megapixels).toFixed(2).replace('.', ',')} Мп`;
      $('#duration').value = appState.project.cuts?.[0]?.duration ?? 5;
      $('#steps').value = appState.project.steps;
      $('#refImageSize').value = appState.project.refImageSize;
      $('#cacheMode').value = appState.project.cacheMode;
      refreshCacheControls();
      productionMode.refresh();
      $('#samplingProfile').value = appState.project.samplingProfile || 'standard';
      $('#swapLora').value = appState.project.swapLora || 'v1';
      $('#seed').value = appState.project.seed;
      $('#useVideoAudio').checked = appState.project.useVideoAudio;
    }
    hydrating = false;
    actingControl.refreshReferences();
    actingControl.sync({ mode: appState.project.actingMode, owner: appState.project.actingOwner, voice: appState.project.actingVoice, timbre: appState.project.actingTimbre,
      texture: appState.project.actingTexture, accent: appState.project.actingAccent, emotion: appState.project.actingEmotion, pace: appState.project.actingPace });
    renderReferences(); renderCuts(); renderLibrary();
    if (assetsWorkspace) showLibraryTab('existing');
    hydrated();
    await refreshJobs({ healthRequest: loading.health });
    if (assetsWorkspace) showLibraryTab('existing');
    jobsPolling = startStudioPolling({ refresh: refreshJobs, isActive: () => gpuActivity.running.length > 0 || activeSideJobs(knownSideJobs).length > 0 || [...knownJobs, ...knownUpscales]
      .some((job) => !['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)) });
  } catch {
    hydrated();
    hydrating = true;
    $('.preview-badge').textContent = 'Только предпросмотр · сервис не запущен';
    $('#composerNote').textContent = 'Это статический предпросмотр. Для рабочей очереди запусти локальный сервис H3 Studio.';
    if (assetsWorkspace) $('#libraryGrid').textContent = 'Локальная Studio недоступна. Запусти сервис и обнови страницу, чтобы открыть файлы.';
  }
}
