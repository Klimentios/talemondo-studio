// Create: the stage (selected result, live preview) and reference thumbnails.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { shouldPollPreview, stageStatus } from './stage-status.mjs?v=20261004-contract-1';
import { createShotPlaceholder } from './workspace-context.mjs?v=20261005-1';
import { effectiveResolution, referenceDisplayTag } from './model.mjs?v=20261106-refmatch';
import { selectedReference } from './app-composer.mjs?v=20261106-refmatch';
import { activeCut } from './app-direction.mjs?v=20261106-refmatch';
import { knownJobs } from './app-queue.mjs?v=20261106-refmatch';
import { $, files } from './app-shared.mjs?v=20261106-refmatch';

let stageKey = '';
let liveJobId = null;
export let previewRequest = null;
let previewUrl = null;
let previewLastRequest = 0;

export function renderStage() {
  const stage = $('#stage');
  const active = knownJobs.find((job) => ['running', 'stopping', 'downloading'].includes(job.state));
  // Keep the just-rendered job selected when its MP4 arrives.
  const previousLive = knownJobs.find((job) => job.job_id === liveJobId);
  if (!active && previousLive?.state === 'completed') appState.selectedResult = previousLive;
  const job = appState.selectedResult?.state === 'completed' ? appState.selectedResult : active;
  document.body.classList.toggle('has-preview', Boolean(job));
  const settings = job ? JSON.parse(job.settings_json) : effectiveResolution(appState.project.aspect, appState.project.quality, appState.project.megapixels);
  const ratio = settings.width / settings.height;
  stage.style.aspectRatio = `${settings.width} / ${settings.height}`;
  stage.style.width = '100%';
  const live = job && job.state !== 'completed';
  const key = job ? `${live ? 'live' : 'video'}:${job.job_id}` : 'empty';
  if (key !== stageKey) {
    previewRequest?.abort(); previewRequest = null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null; previewLastRequest = 0;
    stageKey = key;
    stage.replaceChildren();
    liveJobId = live ? job.job_id : null;
    if (job?.state === 'completed') {
      const video = document.createElement('video');
      video.className = 'stage-media';
      video.src = `/api/jobs/${encodeURIComponent(job.job_id)}/video`;
      video.controls = true; video.playsInline = true; video.preload = 'metadata';
      stage.append(video);
    } else {
      if (live) {
        const empty = document.createElement('div');
        empty.className = 'stage-empty';
        const title = document.createElement('strong');
        title.textContent = 'Генерация идёт';
        const help = document.createElement('p');
        help.textContent = 'Ожидаем первый черновой кадр с GPU…';
        empty.append(title, help); stage.append(empty);
      } else {
        stage.append(createShotPlaceholder(activeCut(), appState.project.cuts.indexOf(activeCut()), appState.project.references,
          () => { $('#mainPrompt').focus(); $('#mainPrompt').scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
      }
      if (live) {
        const img = document.createElement('img');
        img.className = 'stage-media live-preview'; img.hidden = true;
        img.alt = 'Черновой кадр текущей генерации';
        const caption = document.createElement('div'); caption.className = 'stage-caption';
        const progress = document.createElement('div'); progress.className = 'stage-progress';
        progress.append(document.createElement('i'));
        stage.append(img, caption, progress);
      }
    }
  }
  if (!job) {
    stage.replaceChildren(createShotPlaceholder(activeCut(), appState.project.cuts.indexOf(activeCut()), appState.project.references,
      () => { $('#mainPrompt').focus(); $('#mainPrompt').scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
  }
  $('.result-state').textContent = live ? 'Генерация · черновой кадр' : job ? 'Готовый MP4' : 'Черновик';
  $('.result-hint').textContent = live ? 'Кадр обновляется примерно раз в 6 секунд. Цвета и детали предварительные.' : 'Пропорции соответствуют ролику';
  if (live) {
    // Real GPU sampler progress; an unreachable GPU or stale data is shown as such.
    const status = stageStatus(job, { gpuReachable: appState.gpuReachable });
    const title = stage.querySelector('.stage-empty strong'), help = stage.querySelector('.stage-empty p');
    if (title) title.textContent = status.title;
    if (help) help.textContent = status.help;
    stage.querySelector('.stage-caption').textContent = status.caption;
    const bar = stage.querySelector('.stage-progress');
    bar.hidden = status.percent === null;
    bar.classList.toggle('stale', status.stale);
    bar.firstChild.style.width = `${status.percent ?? 0}%`;
    $('.result-state').textContent = status.title;
    refreshStagePreview(job.job_id);
  }
}

async function refreshStagePreview(id) {
  if (!shouldPollPreview({ gpuReachable: appState.gpuReachable, hidden: document.hidden, inFlight: Boolean(previewRequest), lastRequest: previewLastRequest })) return;
  previewLastRequest = Date.now();
  const controller = new AbortController(); previewRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(id)}/preview`, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) return; // Keep the last successful frame during temporary gaps.
    const blob = await response.blob();
    if (liveJobId !== id || controller.signal.aborted) return;
    const nextUrl = URL.createObjectURL(blob);
    const img = $('#stage .live-preview');
    if (!img) { URL.revokeObjectURL(nextUrl); return; }
    const oldUrl = previewUrl; previewUrl = nextUrl;
    img.onload = () => {
      img.hidden = false;
      const placeholder = $('#stage .stage-empty'); if (placeholder) placeholder.hidden = true;
      if (oldUrl) URL.revokeObjectURL(oldUrl);
    };
    img.src = nextUrl;
  } catch { /* Retry on the next regular queue refresh. */ }
  finally { clearTimeout(timeout); if (previewRequest === controller) previewRequest = null; }
}

export function renderReferencePreview() {
  const preview = $('#referencePreview');
  const content = $('#referencePreviewContent');
  content.replaceChildren();
  const ref = appState.project.references.find((item) => item.id === selectedReference);
  preview.hidden = !ref;
  if (!ref) return;
  const media = document.createElement(ref.kind === 'image' ? 'img' : ref.kind === 'video' ? 'video' : 'audio');
  media.className = 'reference-preview-media';
  media.src = ref.preparedExcerptId ? `/api/assets/${encodeURIComponent(ref.preparedExcerptId)}/file` : files.get(ref.id).url;
  if (ref.kind === 'video' || ref.kind === 'audio') {
    media.controls = true;
    if (ref.kind === 'video') media.playsInline = true;
    media.preload = 'metadata';
  } else {
    media.alt = `Предпросмотр ${ref.name}`;
  }
  const caption = document.createElement('div');
  caption.textContent = `${referenceDisplayTag(ref, appState.project.references)} · ${ref.name}`;
  content.append(media, caption);
}
// Small JPEG for picture previews (the full file only if the thumbnail is unavailable).
export function setThumb(img, id) {
  const own = files.get(id)?.url;
  if (own?.startsWith('blob:')) { img.src = own; return; }
  // thumbnail → full file → retry both a few times: while Studio restarts every request fails,
  // and a broken <img> never asks again on its own.
  let attempt = 0;
  img.onerror = () => {
    attempt += 1;
    if (attempt === 1) { img.src = `/api/assets/${encodeURIComponent(id)}/file`; return; }
    if (attempt > 6) { img.onerror = null; return; }
    setTimeout(() => { img.src = `/api/assets/${encodeURIComponent(id)}/thumbnail?retry=${attempt}`; }, 3000);
  };
  img.src = `/api/assets/${encodeURIComponent(id)}/thumbnail`;
}
