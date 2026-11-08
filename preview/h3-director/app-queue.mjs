// Queue: batch dispatch, polling, job feed actions and the queue view.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { selectAspect } from './aspects.mjs?v=20261106-aspects-1';
import { bindProductionMode, installProductionMonitor, productionPayload, submitProduction } from './production-flow.mjs?v=20261103-no-qwen-1';
import { restoreRenderSettings } from './render-experiments.mjs?v=20261021-2';
import { sceneRequests } from './scene-queue.mjs?v=20261031-timecode-1';
import { activeSideJobs, manifestNumbers, renderJobFeed } from './job-feed.mjs?v=20261106-aspects-1';
import { applyVideoHealth, isCloudBackend } from './video-backend.mjs?v=20261108-api-1';
import { getLocale } from './locale.mjs?v=20261108-i18n-1';
import { bindCreateBatch, updateCreateAvailability } from './create-workspace.mjs?v=20261102-cloud-queue-1';
import { sendJobToStory } from './story-handoff.mjs?v=20261013-link';
import { validateSourceWindow } from './long-scene.mjs?v=20261006-auto-2';
import { renumberReferences } from './model.mjs?v=20261106-refmatch';
import { apiReady, jobsPolling } from './app-bootstrap.mjs?v=20261106-refmatch';
import { activeCut } from './app-direction.mjs?v=20261106-refmatch';
import { persistDraft, scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { enqueue, settingsSnapshot } from './app-generation.mjs?v=20261108-api-1';
import { refreshLibraryResults } from './app-library.mjs?v=20261108-clean';
import { renderCuts, renderReferences } from './app-references.mjs?v=20261108-clean';
import { refreshCacheControls } from './app-settings.mjs?v=20261106-refmatch';
import { $, allAssets, apiJson, expandedJobDetails, files, resolvedProjectStyle, showToast } from './app-shared.mjs?v=20261106-refmatch';
import { previewRequest, renderStage } from './app-stage.mjs?v=20261106-refmatch';

export let knownJobs = [];
const CREATE_BATCH_KEY = 'studio.create.latestBatch.v1';
let latestCreateJobIds = readCreateBatch();
export let knownUpscales = [];
let queuePaused = false;
let jobsRefresh = null;
export let knownSideJobs = [];
export let gpuActivity = { running: [], pending_count: 0 };
let queueView = false;
export let productionMode;


function readCreateBatch() {
  try {
    const ids = JSON.parse(localStorage.getItem(CREATE_BATCH_KEY) || '[]');
    return Array.isArray(ids) ? ids.filter((id) => typeof id === 'string').slice(0, 4) : [];
  } catch { return []; }
}

function rememberCreateBatch(ids) {
  latestCreateJobIds = ids;
  try { localStorage.setItem(CREATE_BATCH_KEY, JSON.stringify(ids)); } catch { /* Queue data stays authoritative. */ }
  renderJobs();
}

export function installQueue() {
  productionMode = bindProductionMode({before:$('#createDispatchStatus'),getProject:()=>appState.project,save:scheduleDraft});
  installProductionMonitor($('#jobQueue'));
  bindCreateBatch({
    button: $('#enqueue'), countSelect: $('#takeCount'),
    prepare: () => sceneRequests([activeCut()], settingsSnapshot())[0],
    referenceIds: () => appState.project.references.map((ref) => ref.id),
    enqueue: async (prompt, ids, settings, productionSpec) => {
      if (!productionMode.enabled()) return enqueue(prompt, ids, settings, undefined, true, productionSpec);
      if (productionSpec) throw new Error('Для ShotSpec используй обычную отправку H3; производственный режим ещё не поддерживает этот контракт.');
      validateSourceWindow(appState.project.cuts, activeCut());
      const snapshot = await window.h3Studio.contextSnapshot();
      snapshot.brief = prompt;
      return submitProduction(productionPayload(snapshot, settings, activeCut().referenceWindow));
    },
    onSubmitted: (job, index) => {
      if (!job?.job_id) return;
      rememberCreateBatch(index === 0 ? [job.job_id] : [...latestCreateJobIds, job.job_id]);
    },
    saveDraft: persistDraft, isReady: () => apiReady, toast: showToast,
  });
  $('#pauseQueue').addEventListener('click', async () => {
    const button = $('#pauseQueue');
    button.disabled = true;
    try {
      const state = await apiJson('/api/queue', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused: !queuePaused }) });
      queuePaused = state.paused;
      renderJobs();
      showToast(queuePaused ? 'Очередь приостановлена. Новые задания ждут.' : 'Очередь возобновлена.');
    } catch (error) { showToast(error.message); button.disabled = false; }
  });
  $('#clearAllJobs').addEventListener('click', async () => {
    const finishedSide = knownSideJobs.filter((job) => !activeSideJobs([job]).length).length;
    if (!knownJobs.length && !finishedSide) return;
    const remote = knownJobs.some((job) => !['queued_local', 'completed', 'failed', 'cancelled', 'interrupted'].includes(job.state));
    // Active / uncertain jobs are kept visible by the server; everything else is hidden.
    const waiting = knownJobs.filter((job) => job.state === 'queued_local').length;
    const completed = knownJobs.filter((job) => job.state === 'completed').length;
    const message = `Удалить все ${knownJobs.length + finishedSide} заданий из списка?` +
      (waiting ? ` ${waiting} ожидающих локально будут отменены.` : '') +
      (completed ? ` ${completed} готовых MP4 будут перенесены в восстанавливаемую корзину Studio.` : '') +
      (finishedSide ? ` Завершённые апскейлы, правки, кадры и звук (${finishedSide}) уйдут из блока «Другие задания GPU».` : '') +
      (remote ? ' Задания на GPU или требующие сверки останутся в списке.' : '') +
      ' Записи и исходные референсы сохранятся.';
    if (!window.confirm(message)) return;
    const button = $('#clearAllJobs'); button.disabled = true;
    try {
      const result = await apiJson('/api/history', { method: 'DELETE' });
      appState.selectedResult = null;
      await refreshJobs();
      showToast(`Убрано из списка: ${result.hidden}. MP4 в корзине: ${result.videos_in_trash}.` + (result.kept_active ? ` Оставлено активных: ${result.kept_active}.` : ''));
    } catch (error) {
      const outdated = /Method Not Allowed|Not Found/.test(error.message);
      showToast(outdated ? 'Сервер Studio устарел: дважды кликни «Перезапустить Studio.command» в папке проекта и обнови страницу.' : `Не удалось очистить список: ${error.message}`);
      button.disabled = false;
    }
  });
  $('#navCreate').addEventListener('click', () => showQueueView(false));
  $('#navQueue').addEventListener('click', () => {
    // Other workspaces keep their sections hidden: navigate to Create's queue instead.
    if (!new URLSearchParams(location.search).get('view')) showQueueView(true);
  });
  document.querySelector('.studio-rail nav a[href="#jobQueue"]')?.addEventListener('click', (event) => {
    event.preventDefault();
    showQueueView(true);
  });
  $('#queueShowAll').addEventListener('click', () => showQueueView(true));
  // «Убрать» on a finished side job (upscale / keyframe / edit / sound): only the card goes.
  $('#jobFeed').addEventListener('click', async (event) => {
    const button = event.target.closest('button.side-job-hide');
    if (!button) return;
    button.disabled = true;
    try {
      await apiJson(`/api/side-jobs/${encodeURIComponent(button.dataset.sideKind)}/${encodeURIComponent(button.dataset.sideId)}`, { method: 'DELETE' });
      await refreshJobs();
    } catch (error) { showToast(error.message); button.disabled = false; }
  });
  $('#jobFeed').addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const job = knownJobs.find((item) => item.job_id === button.dataset.id);
    if (!job) return;
    try {
      if (button.dataset.action === 'story') {
        await sendJobToStory(job, window.h3Studio);
        window.location.assign('/?view=scenes#scene-board');
        return;
      }
      if (button.dataset.action === 'view') {
        appState.selectedResult = job;
        renderStage();
        $('.result-section').scrollIntoView({ behavior: 'smooth' });
      } else if (button.dataset.action === 'upscale-cancel') {
        await apiJson(`/api/upscale/jobs/${encodeURIComponent(button.dataset.upscaleId)}/cancel`, { method: 'POST' });
        await refreshJobs();
        showToast('Улучшение отменено. Исходный MP4 не тронут.');
      } else if (button.dataset.action === 'upscale') {
        const mode = button.dataset.mode || 'upscale';
        await apiJson(`/api/upscale/jobs/${encodeURIComponent(job.job_id)}?mode=${encodeURIComponent(mode)}`, { method: 'POST' });
        await refreshJobs();
        showToast({ upscale: 'Апскейл SeedVR2 ×2', smooth: 'Плавность RIFE 48 fps', upscale_smooth: 'Апскейл ×2 + плавность 48 fps' }[mode]
          + ' — в очереди. Исходный MP4 сохранён.');
      } else if (button.dataset.action === 'edit') {
        const manifest = JSON.parse(job.manifest_json);
        const ids = [...manifest.images, ...manifest.videos, ...manifest.audios].map((entry) => entry.asset_id);
        appState.project.references = ids.map((id) => allAssets.get(id)).filter(Boolean).map((asset) => ({
          id: asset.asset_id, kind: asset.kind,
          role: [...manifest.images, ...manifest.videos, ...manifest.audios].find((entry) => entry.asset_id === asset.asset_id)?.role || asset.role,
          owner: [...manifest.images].find((entry) => entry.asset_id === asset.asset_id)?.owner || '',
          name: asset.original_name, ...(asset.kind === 'video' ? { retention: 'partially_preserved' } : {}),
          // Pairs travel with the video in the manifest; without them a replacement edit was refused (review 05.11).
          ...(() => {
            const entry = manifest.videos.find((item) => item.asset_id === asset.asset_id);
            return entry?.performers ? { performers: { pairs: entry.performers, keep_others: Boolean(entry.keep_others) } } : {};
          })(),
        }));
        for (const ref of appState.project.references) files.set(ref.id, { url: `/api/assets/${ref.id}/file` });
        const settings = JSON.parse(job.settings_json);
        const sourceVideo = appState.project.references.find((ref) => ref.kind === 'video' && ref.role === 'Исходное видео');
        if (settings.swap_mask && sourceVideo) sourceVideo.swapMask = settings.swap_mask;
        if (settings.backend === 'minimax_api') {
          settings.cloud_model = 'MiniMax-H3';
          settings.cloud_resolution = '768P';
        }
        restoreRenderSettings(appState.project, settings);
        const restoredMegapixels = settings.megapixels || ({ compact: 0.5, quick: 0.8, balanced: 0.9, max: 0.98 }[settings.quality] || 0.8);
        Object.assign(appState.project, { aspect: settings.aspect_ratio, mode: settings.mode || 'replace', quality: 'custom',
          megapixels: restoredMegapixels, steps: settings.steps,
          refImageSize: settings.ref_image_size, styleAdapter: settings.style_adapter || 'none', cacheMode: settings.cache_mode || 'safe', cacheConfig: settings.cache_config || null, samplingProfile: settings.sampling_profile || 'standard', swapLora: settings.swap_lora || 'v1', seed: String(settings.seed),
          useVideoAudio: settings.use_video_audio });
        appState.project.cuts = [{ id: 'cut-1', duration: settings.duration, prompt: job.original_prompt }];
        appState.project = renumberReferences(appState.project);
        appState.project.styleAdapter = resolvedProjectStyle();
        $('#mainPrompt').value = appState.project.cuts[0].prompt;
        $('#modeSelect').value = appState.project.mode;
        selectAspect($('#aspect'), appState.project.aspect);
        $('#quality').value = appState.project.megapixels;
        $('#qualityPreset').value = [0.4, 0.6, 0.7, 0.8].find((value) => Math.abs(value - appState.project.megapixels) < 0.005)?.toFixed(2) || '';
        $('#megapixelValue').textContent = `${Number(appState.project.megapixels).toFixed(2).replace('.', ',')} Мп`;
        $('#duration').value = settings.duration;
        $('#steps').value = appState.project.steps;
        $('#refImageSize').value = appState.project.refImageSize;
        $('#cacheMode').value = appState.project.cacheMode;
        refreshCacheControls();
        productionMode.refresh();
        $('#samplingProfile').value = appState.project.samplingProfile || 'standard';
        $('#swapLora').value = appState.project.swapLora || 'v1';
        $('#seed').value = appState.project.seed;
        $('#useVideoAudio').checked = appState.project.useVideoAudio;
        renderReferences(); renderCuts(); scheduleDraft();
        $('#composer').scrollIntoView({ behavior: 'smooth' });
      } else if (button.dataset.action === 'repeat') {
        const evidence = await apiJson(`/api/jobs/${encodeURIComponent(job.job_id)}/reproducibility`);
        if (!evidence.references_verified || evidence.blockers.length) {
          showToast(`Повтор заблокирован: референсы или результат задания изменились (${evidence.blockers.join(', ')}).`);
          return;
        }
        const manifest = JSON.parse(job.manifest_json);
        const ids = [...manifest.images, ...manifest.videos, ...manifest.audios].map((entry) => entry.asset_id);
        const settings = JSON.parse(job.settings_json);
        await enqueue(job.original_prompt, ids, settings, manifestNumbers(manifest, settings), false,
          job.production_spec_json ? JSON.parse(job.production_spec_json) : null);
      } else if (button.dataset.action === 'cancel') {
      if (job.state === 'running' && !window.confirm('Прервать текущую генерацию на GPU? Уже вычисленные шаги будут потеряны.')) return;
        await apiJson(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, { method: 'POST' });
        await refreshJobs();
      } else if (button.dataset.action === 'delete') {
        const failed = job.state === 'failed';
        const message = failed
          ? 'Скрыть эту ошибку из списка? Запись задания и текст ошибки останутся сохранены.'
          : 'Убрать ролик из ленты? MP4 будет перенесён в восстанавливаемую корзину Studio.';
      // A cancelled job has no video: removing its card needs no confirmation.
        if (job.state !== 'cancelled' && !window.confirm(message)) return;
        await apiJson(`/api/jobs/${encodeURIComponent(job.job_id)}`, { method: 'DELETE' });
        if (appState.selectedResult?.job_id === job.job_id) { appState.selectedResult = null; renderStage(); }
        await refreshJobs();
      }
    } catch (error) { showToast(error.message); }
  });
}

export function renderJobs() {
  updateCreateAvailability({ button: $('#enqueue'), note: $('#createDispatchStatus'),
    ready: apiReady, paused: queuePaused, gpuReachable: appState.gpuReachable,
    cloudConfigured: isCloudBackend() && appState.cloudApiConfigured, en: getLocale() === 'en' });
  renderJobFeed({ jobs: knownJobs, upscales: knownUpscales, sideJobs: knownSideJobs, gpuActivity, paused: queuePaused, queueView, gpuReachable: appState.gpuReachable,
    // The last submitted batch is shown only while its jobs are still in the list; once they are
    // removed (or never existed here) the gallery shows every result instead of an empty box.
    highlightIds: latestCreateJobIds.filter((id) => knownJobs.some((job) => job.job_id === id)),
    expanded: expandedJobDetails, locale: getLocale() });
}

export function refreshJobs({ healthRequest } = {}) {
  // Manual refresh and the polling timer share one request instead of racing each other.
  if (!jobsRefresh) jobsRefresh = loadJobs(healthRequest).finally(() => { jobsRefresh = null; jobsPolling?.reschedule(); });
  return jobsRefresh;
}

async function loadJobs(healthRequest) {
  if (!apiReady) return;
  try {
    const [jobs, upscales, queue, health, sideJobs, activity] = await Promise.all([apiJson('/api/jobs'), apiJson('/api/upscale/jobs'), apiJson('/api/queue'), healthRequest || apiJson('/api/health'),
      apiJson('/api/side-jobs').catch(() => []), apiJson('/api/gpu/activity').catch(() => ({ running: [], pending_count: 0 }))]);
    knownSideJobs = sideJobs;
    gpuActivity = activity;
    knownJobs = jobs;
    refreshLibraryResults();
    knownUpscales = upscales;
    appState.gpuReachable = Boolean(health.gpu_reachable);
    applyVideoHealth(health);
    // No preview requests while the GPU is away; an in-flight one would only wait for its timeout.
    if (!appState.gpuReachable && previewRequest) previewRequest.abort();
    queuePaused = queue.paused;
    if (appState.selectedResult) appState.selectedResult = knownJobs.find((job) => job.job_id === appState.selectedResult.job_id) || null;
    renderJobs();
    // QC gate (store.review_hold): finished takes wait for a look; «Продолжить» = «посмотрел».
    if (queue.review_hold) $('#queueOverviewDetail').textContent += ` ${queue.review_hold.reason}`;
    renderStage();
  } catch {
    $('#queueSummary').textContent = 'Локальный сервис временно недоступен';
    updateCreateAvailability({ button: $('#enqueue'), note: $('#createDispatchStatus'),
      ready: false, paused: queuePaused, gpuReachable: appState.gpuReachable, en: getLocale() === 'en' });
    $('#queueOverviewTitle').textContent = 'Очередь недоступна';
    $('#queueOverviewDetail').textContent = 'Проверьте локальный сервис Studio.';
  }
}

function showQueueView(open) {
  queueView = open;
  $('#navCreate').classList.toggle('active', !open);
  $('#navQueue').classList.toggle('active', open);
  window.studioRail?.setActive(open ? 'queue' : window.studioRail.current() === 'queue' ? 'create' : window.studioRail.current());
  renderJobs();
  if (open) $('#jobQueue').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
