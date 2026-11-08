// Job feed (queue + finished videos). Pure view: state comes in as arguments.
import { appendComfyActions } from './comfy-edits.mjs?v=20261108-graph-ops-1';
import { appendSeedHuntActions, seedHuntNote } from './seed-hunt.mjs?v=20261102-seed-hunt';
import { appendUpscaleActions } from './upscale-actions.mjs?v=20261103-history-only';
import { jobErrorText } from './api-error.mjs?v=20261008-1';
import { jobTitle } from './job-title.mjs?v=20261008-1';
const $ = (selector) => document.querySelector(selector);

function studioDate(value, locale = 'ru') {
  if (!value) return null;
  // SQLite CURRENT_TIMESTAMP is UTC without a suffix. Date.parse needs it explicitly.
  const date = new Date(String(value).replace(' ', 'T') + (/[zZ]|[+-]\d\d:\d\d$/.test(String(value)) ? '' : 'Z'));
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function jobTimeLine(job, locale = 'ru', now = Date.now()) {
  const created = studioDate(job.created_at, locale);
  if (!created) return '';
  const updated = studioDate(job.updated_at, locale);
  const createdMs = Date.parse(String(job.created_at).replace(' ', 'T') + 'Z');
  const elapsed = Math.max(0, Math.floor((now - createdMs) / 60000));
  const active = !['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state);
  const duration = active ? ` · ${locale === 'en' ? 'since added' : 'с постановки'} ${Math.floor(elapsed / 60)} ${locale === 'en' ? 'h' : 'ч'} ${elapsed % 60} ${locale === 'en' ? 'min' : 'мин'}` : '';
  return locale === 'en'
    ? `Added ${created}${duration}${updated ? ` · Last sync ${updated}` : ''}`
    : `Поставлено ${created}${duration}${updated ? ` · Синхронизация ${updated}` : ''}`;
}

export function jobButton(label, action, id, disabled = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.dataset.action = action;
  button.dataset.id = id;
  button.disabled = disabled;
  return button;
}

export function manifestNumbers(manifest, settings) {
  const numbers = {};
  for (const [kind, entries] of [['images', manifest.images], ['videos', manifest.videos], ['audios', manifest.audios]]) {
    entries.forEach((entry, index) => {
      numbers[entry.asset_id] = index + 1 + (kind === 'audios' && settings.use_video_audio ? manifest.videos.length : 0);
    });
  }
  return numbers;
}

// Cache the graph really used. The worker stores it with cache_mode_requested when a
// video-reference job rendered Fast as Safe (app.graph.effective_cache_mode).
export function cacheSummary(director, en = false) {
  if (director?.cache_execution) {
    const report = director.cache_execution;
    if (!report.enabled) return en ? 'cache off' : 'кеш off';
    const passes = report.runtime_counters?.passes;
    const runtime = Array.isArray(passes) && passes.length
      ? ` · ${en ? 'actual reuse' : 'фактически повторно'} ${passes.reduce((n,p)=>n+p.reused_forward_calls,0)}/${passes.reduce((n,p)=>n+p.reused_forward_calls+p.full_forward_calls,0)} ${en ? 'model calls' : 'вызовов модели'}` : '';
    const c = report.effective;
    if (c) return `${en ? 'cache' : 'кеш'} ${director.cache_mode} · ${c.threshold} · ${c.start_percent}–${c.end_percent} · max ${c.max_consecutive_hits} · guard ${c.temporal_guard ? 'on' : 'off'}${runtime}`;
  }
  if (!director?.cache_mode) return '';
  const label = `${en ? 'cache' : 'кеш'} ${director.cache_mode}`;
  if (!director.cache_mode_requested || director.cache_mode_requested === director.cache_mode) return label;
  return en ? `${label} (requested ${director.cache_mode_requested}: video reference)`
    : `${label} (запрошен ${director.cache_mode_requested}: видео-референс)`;
}

// Qwen planning failed on the GPU and the job was rendered straight from the Studio prompt.
export function directorFallbackNote(director, en = false) {
  if (!director?.director_fallback) return '';
  const reason = String(director.director_fallback).slice(0, 200);
  return en ? `Qwen Director did not produce a plan; rendered from the Studio prompt (${reason})`
    : `Qwen не составил план — ролик собран по промпту Studio (${reason})`;
}

export function visibleCreateJobs(knownJobs, active, highlightIds = [], queueView = false) {
  if (queueView) return knownJobs;
  if (highlightIds.length) {
    const byId = new Map(knownJobs.map((job) => [job.job_id, job]));
    const batch = highlightIds.map((id) => byId.get(id)).filter(Boolean);
    const shown = new Set(batch.map((job) => job.job_id));
    // Jobs launched by agents or the API must remain visible even when this
    // browser remembers a different local batch from a previous session.
    const recent = knownJobs.filter((job) => job.state === 'completed').slice(0, 4);
    return [...batch, ...active, ...recent].filter((job, index, all) =>
      all.findIndex((item) => item.job_id === job.job_id) === index);
  }
  return [...active, ...knownJobs.filter((job) => job.state === 'completed').slice(0, 4)];
}

export function createResultLabel(job, highlightIds = [], en = false) {
  const index = highlightIds.indexOf(job.job_id);
  return index >= 0 ? `${en ? 'TAKE' : 'ВАРИАНТ'} ${String(index + 1).padStart(2, '0')}` : jobTitle(job, 64);
}

// Side jobs (GET /api/side-jobs: upscale, keyframe, «Правка видео», sound) share the GPU and the Mac
// dispatcher with H3: an H3 job waits behind them, so the queue page lists them too.
const SIDE_ACTIVE = new Set(['queued_local', 'uploading', 'queued_gpu', 'running', 'downloading', 'needs_reconciliation']);
const SIDE_STATES = {
  ru: { queued_local: 'ждёт очереди', uploading: 'передача на GPU', queued_gpu: 'в очереди GPU', running: 'обработка на GPU',
    downloading: 'сохранение результата', needs_reconciliation: 'нужна сверка', completed: 'готово', failed: 'ошибка', cancelled: 'отменено' },
  en: { queued_local: 'waiting', uploading: 'sending to GPU', queued_gpu: 'queued on GPU', running: 'processing on GPU',
    downloading: 'saving result', needs_reconciliation: 'reconciliation needed', completed: 'done', failed: 'error', cancelled: 'cancelled' },
};
const SIDE_TITLES_EN = { motion: 'Motion Director scene', upscale: 'Video enhancement', keyframe: 'Keyframe', video_edit: 'Video edit', audio: 'Sound and voice' };
export const activeSideJobs = (sideJobs) => sideJobs.filter((job) => SIDE_ACTIVE.has(job.state));
export function sideJobLine(job, en = false, paused = false) {
  const title = en ? SIDE_TITLES_EN[job.kind] || job.kind : job.title;
  let state = SIDE_STATES[en ? 'en' : 'ru'][job.state] || job.state;
  if (job.state === 'queued_local' && paused) state = en ? 'queue paused' : 'пауза очереди';
  const source = job.scene_id ? ` · ${en ? 'scene' : 'сцена'} #${job.scene_id.slice(0, 8)} · ${en ? 'revision' : 'версия'} ${job.revision}`
    : job.source_job_id ? ` · ${en ? 'video' : 'ролик'} #${job.source_job_id.slice(0, 8)}` : '';
  const error = ['failed', 'needs_reconciliation'].includes(job.state) ? ` — ${job.error || (en ? 'the GPU gave no reason' : 'GPU не сообщил причину')}` : '';
  return { text: `${title}: ${state}${source} · #${String(job.id).slice(0, 8)}${error}`, active: SIDE_ACTIVE.has(job.state), href: job.href };
}

function sideJobsSection(sideJobs, en, paused) {
  const section = document.createElement('section');
  section.className = 'side-jobs';
  const heading = document.createElement('strong');
  heading.textContent = en ? 'Other GPU jobs' : 'Другие задания GPU';
  const list = document.createElement('ul');
  for (const job of sideJobs) {
    const line = sideJobLine(job, en, paused);
    const item = document.createElement('li');
    item.className = `side-job state-${job.state}`;
    const link = document.createElement('a');
    link.href = line.href;
    link.textContent = line.text;
    item.append(link);
    if (job.kind === 'motion' && job.state === 'completed' && job.file_url) {
      const result = document.createElement('a');
      result.href = job.file_url;
      result.target = '_blank';
      result.rel = 'noopener';
      result.textContent = en ? 'Open video · QC required' : 'Открыть видео · требуется QC';
      item.append(result);
    }
    const detail = document.createElement('small');
    const started = Date.parse(String(job.created_at || '').replace(' ', 'T') + 'Z');
    const end = line.active ? Date.now() : Date.parse(String(job.updated_at || '').replace(' ', 'T') + 'Z');
    const seconds = Math.max(0, Math.floor((end - started) / 1000));
    const elapsed = Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : '—';
    const progress = job.sampling_progress;
    const hasSteps = progress?.max > 0 && Number.isFinite(progress.value);
    detail.textContent = `${en ? 'Elapsed' : 'Прошло'}: ${elapsed} · ` + (job.waiting_for_frame
      ? (en ? 'Waiting for the edited first frame' : 'Ждёт подготовки первого кадра')
      : hasSteps ? `${en ? 'Sampling' : 'Сэмплинг'}: ${Math.floor(100 * progress.value / progress.max)}% · ${progress.value}/${progress.max} ${en ? 'steps' : 'шагов'}`
      : job.state === 'running' ? (en ? 'Loading / processing; waiting for sampler steps' : 'Загрузка / обработка; ожидаем шаги сэмплера') : '');
    item.append(detail);
    const timing = document.createElement('small');
    timing.className = 'job-timing';
    timing.textContent = jobTimeLine(job, en ? 'en' : 'ru');
    if (timing.textContent) item.append(timing);
    if (hasSteps && line.active) {
      const bar = document.createElement('progress');
      bar.max = progress.max;
      bar.value = progress.value;
      bar.setAttribute('aria-label', en ? 'Sampling progress' : 'Ход сэмплинга');
      item.append(bar);
    }
    if (!line.active) {
      const hide = document.createElement('button');
      hide.type = 'button';
      hide.className = 'side-job-hide';
      hide.dataset.sideKind = job.kind;
      hide.dataset.sideId = job.id;
      hide.textContent = en ? 'Remove' : 'Убрать';
      hide.setAttribute('aria-label', `${en ? 'Remove' : 'Убрать'}: ${line.text}`);
      item.append(hide);
    }
    list.append(item);
  }
  section.append(heading, list);
  return section;
}

// A MiniMax API job is cloud from creation (settings.backend), not only once the API returned its task ID:
// before that the card used to say «Ждёт включения GPU-сервера · 768×1344 · 24 шагов» for an API job.
export function isCloudJob(job) {
  if (job.remote_job_id?.startsWith('minimax:')) return true;
  try { return JSON.parse(job.settings_json || '{}').backend === 'minimax_api'; } catch { return false; }
}

export function renderJobFeed({ jobs: knownJobs, upscales = [], sideJobs = [], gpuActivity = {}, paused: queuePaused, queueView, highlightIds = [], expanded: expandedJobDetails, locale, gpuReachable = true }) {
  const en = locale === 'en';
  const feed = $('#jobFeed');
  feed.querySelectorAll('.job-details').forEach((details) => {
    if (details.open) expandedJobDetails.add(details.dataset.jobId);
    else expandedJobDetails.delete(details.dataset.jobId);
  });
  feed.querySelectorAll('.job-more-actions').forEach((details) => {
    const key = `actions:${details.dataset.jobId}`;
    if (details.open) expandedJobDetails.add(key);
    else expandedJobDetails.delete(key);
  });
  // Keep finished videos across refreshes (every 3 s): recreating them reloaded the clip, showed a
  // black frame and stopped playback.
  const keptVideos = new Map([...feed.querySelectorAll('video.job-result-media[data-job-id]')].map((video) => [video.dataset.jobId, video]));
  const playing = [...keptVideos.values()].filter((video) => !video.paused && !video.ended);
  // Detaching a video pauses it: resume the clips the user was watching once the cards are back.
  if (playing.length) queueMicrotask(() => playing.forEach((video) => { if (video.isConnected) void video.play().catch(() => {}); }));
  feed.replaceChildren();
  const active = knownJobs.filter((job) => !['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state));
  const externalRunning = (gpuActivity.running || []).filter((item) =>
    !knownJobs.some((job) => job.remote_job_id === item.prompt_id));
  $('#queueSummary').textContent = knownJobs.length ? en ? `${active.length} running/waiting · ${knownJobs.length} total` : `${active.length} в работе/ожидании · ${knownJobs.length} всего` : en ? 'No jobs yet' : 'Пока нет заданий';
  $('#pauseQueue').disabled = false;
  $('#clearAllJobs').disabled = knownJobs.length === 0 && sideJobs.every((job) => SIDE_ACTIVE.has(job.state));
  $('#pauseQueue').textContent = queuePaused ? en ? '▶ Resume queue' : '▶ Возобновить очередь' : en ? '⏸ Pause queue' : '⏸ Приостановить очередь';
  const labels = en ? { queued_local: queuePaused ? 'Queue paused · waiting to start' : 'Waiting to start on GPU', uploading: 'Sending references and preparing direction', queued_gpu: 'Queued on GPU',
    running: 'Generating on GPU', stopping: 'Stopping on GPU', downloading: 'Downloading and checking MP4', completed: 'Done',
    failed: 'Error', needs_reconciliation: 'Reconciliation needed: automatic retry disabled', interrupted: 'Server lost the job', cancelled: 'Cancelled' } :
    { queued_local: queuePaused ? 'Пауза очереди · ждёт запуска' : 'Ждёт запуска на GPU', uploading: 'Передача и подготовка режиссуры', queued_gpu: 'В очереди GPU',
    running: 'Генерация на GPU', stopping: 'Останавливается на GPU', downloading: 'Скачивание и проверка MP4', completed: 'Готово',
    failed: 'Ошибка', needs_reconciliation: 'Нужна сверка: автоповтор запрещён',
    interrupted: 'Сервер потерял задание', cancelled: 'Отменено' };
  // GPU off: jobs already sent wait safely on the Mac; never show a frozen «Генерация 37%».
  if (!gpuReachable) {
    const waiting = en ? 'GPU is off · the job waits, nothing is lost' : 'GPU выключен · задание ждёт, ничего не потеряно';
    for (const state of ['uploading', 'queued_gpu', 'running', 'stopping', 'downloading']) labels[state] = waiting;
    if (!queuePaused) labels.queued_local = en ? 'Waiting for the GPU server' : 'Ждёт включения GPU-сервера';
  }
  const cloudLabels = en ? { queued_local: 'Waiting to be sent to MiniMax API', uploading: 'Sending to MiniMax API',
    queued_gpu: 'MiniMax API queue', running: 'MiniMax API generating', downloading: 'Saving API video' }
    : { queued_local: 'Ждёт отправки в MiniMax API', uploading: 'Отправка в MiniMax API', queued_gpu: 'Очередь MiniMax API',
      running: 'Генерация MiniMax API', downloading: 'Сохранение видео API' };
  const current = active.find((job) => isCloudJob(job) && job.state === 'running')
    || active.find((job) => job.state === 'running') || active[0];
  const sideActive = activeSideJobs(sideJobs);
  const sideOnGpu = sideActive.find((job) => job.state !== 'queued_local' && job.state !== 'needs_reconciliation');
  const queuedCount = active.length + sideActive.length;
  $('#queueOverviewTitle').textContent = !current && sideActive.length
    ? `${sideJobLine(sideOnGpu || sideActive[0], en, queuePaused).text} · ${queuedCount} ${en ? 'queued/running' : 'в очереди/работе'}`
    : current
    ? `${isCloudJob(current) ? cloudLabels[current.state] || labels[current.state] : labels[current.state] || current.state} · ${active.length} ${en ? 'queued/running' : 'в очереди/работе'}`
    : knownJobs.length ? en ? `Queue empty · ${knownJobs.length} jobs in the library` : `Очередь пуста · ${knownJobs.length} заданий в библиотеке` : en ? 'Queue empty' : 'Очередь пуста';
  $('#queueOverviewDetail').textContent = current
    ? isCloudJob(current) && current.state !== 'running'
      ? (en ? 'The job goes to MiniMax API on its own; it does not wait for the GPU server.'
        : 'Задание само уйдёт в MiniMax API; GPU-сервер ему не нужен.')
      : isCloudJob(current)
      ? (en ? 'MiniMax API is generating. The API reports queued/running/completed, without a percentage; the MP4 is saved here automatically.'
        : 'MiniMax API генерирует. API сообщает этап без процента; готовый MP4 сохранится здесь автоматически.')
      : gpuReachable && current.state === 'running' && current.sampling_max > 0
      ? en ? `Sampling: ${Math.floor(100 * current.sampling_value / current.sampling_max)}% (${current.sampling_value}/${current.sampling_max} steps). Then decoding and saving MP4.` : `Сэмплинг: ${Math.floor(100 * current.sampling_value / current.sampling_max)}% (${current.sampling_value}/${current.sampling_max} шагов). Затем декодирование и сохранение MP4.`
      : en ? `Stage: ${labels[current.state] || current.state}. ${current.state === 'running' ? 'Waiting for step data from the GPU.' : 'Progress will appear during sampling.'}` : `Этап: ${labels[current.state] || current.state}. ${current.state === 'running' ? 'Ожидаем данные о шагах с GPU.' : 'Процент появится во время сэмплинга.'}`
    : knownJobs.length ? en ? 'The next run and its stages will appear here.' : 'Здесь будет показан следующий запуск и его этапы.' : en ? 'Jobs will appear here after submission.' : 'Задания появятся здесь после отправки.';
  if (sideOnGpu && current?.state === 'queued_local') {
    $('#queueOverviewDetail').textContent += en ? ` The GPU is busy: ${SIDE_TITLES_EN[sideOnGpu.kind] || sideOnGpu.kind}.` : ` GPU занят: ${sideOnGpu.title}.`;
  }
  if (externalRunning.length && current?.state !== 'running') {
    const progress = externalRunning[0].sampling_progress;
    const steps = progress?.max > 0 ? ` · ${progress.value}/${progress.max} ${en ? 'steps' : 'шагов'}` : '';
    $('#queueOverviewTitle').textContent = `${en ? 'GPU is generating an external job' : 'GPU генерирует внешнее задание'}${steps}`;
    $('#queueOverviewDetail').textContent = en
      ? `Submitted directly to the GPU; it is not in Studio's local job history. ${active.length} local jobs are waiting.`
      : `Отправлено напрямую на GPU, поэтому его нет в локальной истории Studio. Локальных заданий в ожидании: ${active.length}.`;
  }
  if (queuePaused) $('#queueOverviewDetail').textContent += en ? ' Queue paused: new jobs are not sent to the GPU.' : ' Очередь приостановлена: новые задания не отправляются на GPU.';
  const cloudDone = knownJobs.filter((job) => isCloudJob(job) && job.state === 'completed').length;
  if (cloudDone) $('#queueOverviewDetail').textContent += ` · MiniMax API: ${cloudDone} ${en ? 'videos saved to Studio' : 'видео сохранено в Studio'}.`;
  $('#queueTitle').textContent = queueView ? en ? 'Queue and finished videos' : 'Очередь и готовые ролики'
    : highlightIds.length ? en ? 'Last generation takes' : 'Варианты последнего запуска'
      : active.length ? en ? 'Jobs and recent videos' : 'Задания и последние видео' : en ? 'Recent videos' : 'Последние видео';
  const completedCount = knownJobs.filter((job) => job.state === 'completed').length;
  const batchJobs = highlightIds.map((id) => knownJobs.find((job) => job.job_id === id)).filter(Boolean);
  const batchCompleted = batchJobs.filter((job) => job.state === 'completed').length;
  $('#queueSummary').textContent = queueView
    ? (en ? `${queuedCount} running/waiting · ${knownJobs.length} total` : `${queuedCount} в работе/ожидании · ${knownJobs.length} всего`)
    : highlightIds.length
      ? (en ? `${batchCompleted} of ${highlightIds.length} takes ready` : `${batchCompleted} из ${highlightIds.length} вариантов готово`)
      : (en ? `Recent ${Math.min(4, completedCount)} · ${completedCount} finished in history · ${active.length} in queue`
        : `Последние ${Math.min(4, completedCount)} · ${completedCount} готово в истории · ${active.length} в очереди`);
  $('#queueShowAll').hidden = queueView;
  document.body.classList.toggle('queue-view', queueView);
  const visibleJobs = visibleCreateJobs(knownJobs, active, highlightIds, queueView);
  for (const item of externalRunning) {
    const card = document.createElement('article');
    card.className = 'gpu-external-job';
    const title = document.createElement('strong');
    title.textContent = en ? 'GPU generation outside Studio' : 'Генерация на GPU вне очереди Studio';
    const detail = document.createElement('small');
    const progress = item.sampling_progress;
    detail.textContent = `${en ? 'Job' : 'Задание'} ${item.prompt_id.slice(0, 8)} · ` +
      (progress?.max > 0 ? `${en ? 'Sampling' : 'Сэмплинг'} ${progress.value}/${progress.max}` :
        (en ? 'Running; waiting for sampler steps' : 'Выполняется; ожидаем шаги сэмплера'));
    card.append(title, detail);
    if (progress?.max > 0) {
      const bar = document.createElement('progress');
      bar.max = progress.max;
      bar.value = progress.value;
      bar.setAttribute('aria-label', en ? 'External GPU sampling progress' : 'Ход внешней генерации на GPU');
      card.append(bar);
    }
    feed.append(card);
  }
  if (queueView && sideJobs.length) feed.append(sideJobsSection(sideJobs, en, queuePaused));
  if (!visibleJobs.length) {
    const empty = document.createElement('p');
    empty.id = 'queueFeedEmpty';
    empty.textContent = highlightIds.length
      ? (en ? 'These takes are no longer in the job history.' : 'Эти варианты больше не найдены в истории заданий.')
      : (en ? 'Generated videos will appear here.' : 'Готовые ролики появятся здесь после генерации.');
    feed.append(empty);
  }
  visibleJobs.forEach((job) => {
    const card = document.createElement('article');
    card.className = `job-card state-${job.state}`;
    if (job.state === 'completed' && !queueView) {
      const kept = keptVideos.get(job.job_id);
      const media = kept || document.createElement('video');
      media.className = 'job-result-media';
      media.dataset.jobId = job.job_id;
      // #t=0.1 shows the first frame instead of a black box before playback.
      if (!kept) {
        media.poster = `/api/jobs/${encodeURIComponent(job.job_id)}/poster`;
        media.src = `/api/jobs/${encodeURIComponent(job.job_id)}/video#t=0.1`;
      }
      media.controls = true;
      media.preload = 'metadata';
      media.playsInline = true;
      media.setAttribute('aria-label', createResultLabel(job, highlightIds, en));
      card.append(media);
    }
    const info = document.createElement('div');
    const status = document.createElement('strong');
    const director = job.director_json ? JSON.parse(job.director_json) : null;
    const position = active.findIndex((item) => item.job_id === job.job_id);
    const cloud = isCloudJob(job);
    const stateLabel = cloud && !['completed', 'failed', 'cancelled'].includes(job.state)
      ? ({ queued_local: en ? 'Waiting to be sent to MiniMax API' : 'Ждёт отправки в MiniMax API',
           uploading: en ? 'Sending to MiniMax API' : 'Отправка в MiniMax API',
           queued_gpu: en ? 'MiniMax API queue' : 'Очередь MiniMax API',
           running: en ? 'MiniMax API generating' : 'Генерация MiniMax API',
           downloading: en ? 'Saving API video' : 'Сохранение видео API' }[job.state] || labels[job.state])
      : labels[job.state];
    status.textContent = job.state === 'completed' && job.qc_verdict === 'rejected'
      ? `${en ? 'Visual QC rejected' : 'Брак визуальной проверки'} · #${job.job_id.slice(0, 8)}`
      : job.state === 'completed' && !queueView
      ? createResultLabel(job, highlightIds, en)
      : `${job.state === "queued_gpu" && director?.server_queue ? (en ? "Saved on server · Mac can go offline" : "На сервере · Mac можно отключить") : stateLabel || job.state}${position >= 0 && job.state === 'queued_local' ? ` · ${en ? 'queue' : 'очередь'} ${position + 1}` : ''} · #${job.job_id.slice(0, 8)}`;
    const settings = JSON.parse(job.settings_json);
    const meta = document.createElement('small');
    const styleNames = en ? { realism: 'realism', animation_2d: '2D animation', animation_3d: '3D animation', illustration: 'illustration' } : { realism: 'реализм', animation_2d: '2D-анимация', animation_3d: '3D-анимация', illustration: 'иллюстрация' };
    const audioNames = en ? { native_stereo_effects_music: 'native stereo audio', preserve_reference_audio: 'audio reference', silent: 'no audio' } : { native_stereo_effects_music: 'родное стерео-аудио', preserve_reference_audio: 'аудиореференс', silent: 'без аудио' };
    const controlSummary = director?.style ? ` · ${styleNames[director.style] || director.style} · ${cacheSummary(director, en)}${director.camera_guide ? en ? ' · camera path' : ' · траектория камеры' : ''}${director.guide_frames ? ` · ${en ? 'anchors' : 'якоря'} ${director.guide_frames}` : ''} · ${audioNames[director.audio_intent] || director.audio_intent}` : '';
    const adapter = director?.style_adapter && director.style_adapter !== 'none' ? ` · LoRA ${director.style_adapter}` : '';
    const experiment = director?.render_profile;
    const renderMeta = experiment ? ` · ${experiment.profile} · ${experiment.model}${experiment.options?.adapter ? ' · '+experiment.options.adapter : ''}${experiment.options?.attention ? ' · '+experiment.options.attention : ''} · качество не подтверждено` : '';
    const budgetMeta = jobBudgetText(job, en);
    const fullMeta = cloud ? `${settings.cloud_model || 'MiniMax H3'} · ${settings.cloud_resolution || '480P'} · ${settings.aspect_ratio || '9:16'} · ${settings.duration} ${en ? 'sec' : 'сек'}`
      : `${settings.width}×${settings.height} · ${settings.duration} ${en ? 'sec' : 'сек'} · ${settings.length} ${en ? 'frames' : 'кадров'} · ${director?.steps || settings.steps} ${en ? 'steps' : 'шагов'}${controlSummary}${adapter}${renderMeta}${budgetMeta} · seed ${settings.seed}`;
    meta.textContent = queueView ? fullMeta
      : `${settings.width}×${settings.height} · ${settings.duration} ${en ? 'sec' : 'сек'} · ${String(job.created_at || '').slice(0, 16)}`;
    info.append(status, meta);
    if (settings.imported_external) {
      const origin = document.createElement('small');
      origin.className = 'job-note';
      origin.textContent = en ? 'Recovered from the GPU. Original reference files are not linked in Studio.'
        : 'Восстановлено с GPU. Исходные референсы не привязаны к Studio.';
      info.append(origin);
    }
    const timing = document.createElement('small');
    timing.className = 'job-timing';
    timing.textContent = jobTimeLine(job, locale);
    if (timing.textContent) info.append(timing);
    if (!queueView) {
      const params = document.createElement('details');
      params.className = 'job-parameters';
      const key = `parameters:${job.job_id}`;
      params.open = expandedJobDetails.has(key);
      const summary = document.createElement('summary');
      summary.textContent = en ? 'Generation settings' : 'Параметры генерации';
      const detail = document.createElement('p');
      detail.textContent = fullMeta;
      params.append(summary, detail);
      params.addEventListener('toggle', () => {
        if (!params.isConnected) return;
        if (params.open) expandedJobDetails.add(key);
        else expandedJobDetails.delete(key);
      });
      info.append(params);
    }
    const fallback = directorFallbackNote(director, en);
    if (fallback) {
      const note = document.createElement('small');
      note.className = 'job-note';
      note.textContent = fallback;
      info.append(note);
    }
    if (queueView) {
      const details = document.createElement('details');
      details.className = 'job-details';
      details.dataset.jobId = job.job_id;
      details.open = expandedJobDetails.has(job.job_id);
      const summary = document.createElement('summary');
      summary.textContent = en ? 'Job text and reference analysis' : 'Текст задания и анализ референсов';
      const prompt = document.createElement('pre');
      if (details.open) prompt.textContent = job.original_prompt;
      details.append(summary, prompt);
      if (director?.visual_evidence && Object.keys(director.visual_evidence).length) {
        const referenceRead = document.createElement('small');
        referenceRead.className = 'job-reference-read';
        referenceRead.textContent = Object.entries(director.visual_evidence)
          .map(([tag, evidence]) => `${tag}: ${evidence.join('; ')}`).join(' · ');
        details.append(referenceRead);
      }
      details.addEventListener('toggle', () => {
        if (!details.isConnected) return;
        if (details.open) {
          expandedJobDetails.add(job.job_id);
          prompt.textContent = job.original_prompt;
        } else {
          expandedJobDetails.delete(job.job_id);
          prompt.textContent = '';
        }
      });
      info.append(details);
    }
    if (job.state === 'running') {
      const progress = document.createElement('div');
      progress.className = 'job-progress';
      const value = document.createElement('span');
      value.textContent = isCloudJob(job)
        ? (en ? 'MiniMax API is generating; this API does not report a percentage.' : 'MiniMax API генерирует; процент выполнения API не сообщает.')
        : gpuReachable && job.sampling_max > 0
        ? en ? `Sampling ${Math.floor(100 * job.sampling_value / job.sampling_max)}% · ${job.sampling_value}/${job.sampling_max} steps` : `Сэмплинг ${Math.floor(100 * job.sampling_value / job.sampling_max)}% · ${job.sampling_value}/${job.sampling_max} шагов`
        : !gpuReachable ? (en ? 'GPU is off · the job waits, nothing is lost' : 'GPU выключен · задание ждёт, ничего не потеряно')
        : en ? 'Sampling: waiting for step data from the GPU' : 'Сэмплинг: ожидаем данные о шагах с GPU';
      progress.append(value);
      if (job.sampling_max > 0) {
        const bar = document.createElement('progress');
        bar.max = job.sampling_max;
        bar.value = job.sampling_value;
        progress.append(bar);
      }
      info.append(progress);

    }
    if (director?.refinement_report?.missing_frames) {
      const missing = Object.entries(director.refinement_report.missing_frames).filter(([,frames]) => frames.length);
      if (missing.length) {
        const warning = document.createElement('small'); warning.className = 'job-error';
        warning.textContent = (en ? 'Original pixels retained where tracking was lost: ' : 'При потере маски сохранены исходные кадры: ') + missing.map(([name,frames]) => `${name}: ${frames.length}`).join('; ');
        info.append(warning);
      }
    }
    if (settings.seed_hunt) {
      const note = document.createElement('small');
      note.textContent = seedHuntNote(settings, en);
      info.append(note);
    }
    if (settings.comfyui_edit) {
      const edit = settings.comfyui_edit;
      const count = (edit.changed?.length || 0) + (edit.added?.length || 0) + (edit.removed?.length || 0);
      const note = document.createElement('small');
      note.textContent = en ? `Graph edited in ComfyUI · ${count} changes` : `Граф правлен в ComfyUI · изменений: ${count}`;
      info.append(note);
    }
    if (job.error) {
      const error = document.createElement('small');
      error.className = 'job-error';
      error.textContent = jobErrorText(job.error);
      error.title = job.error;
      info.append(error);
    }
    const actions = document.createElement('div');
    actions.className = 'job-actions';
    if (job.state === 'completed') {
      const download = document.createElement('a');
      download.href = `/api/jobs/${encodeURIComponent(job.job_id)}/video?download=true`;
      download.download = `h3-${job.job_id}.mp4`;
      download.className = 'job-download';
      download.textContent = en ? '↓ Download MP4' : '↓ Скачать MP4';
      actions.append(download, jobButton(en ? '▶ Watch' : '▶ Смотреть', 'view', job.job_id));
      actions.append(jobButton(en ? 'Add to Story' : 'Добавить в сюжет', 'story', job.job_id));
      appendSeedHuntActions(actions, job, settings, en);

      const more = document.createElement('details');
      more.className = 'job-more-actions';
      more.dataset.jobId = job.job_id;
      more.open = expandedJobDetails.has(`actions:${job.job_id}`);
      more.addEventListener('toggle', () => {
        if (!more.isConnected) return;
        const key = `actions:${job.job_id}`;
        if (more.open) expandedJobDetails.add(key);
        else expandedJobDetails.delete(key);
      });
      const moreTitle = document.createElement('summary');
      moreTitle.textContent = en ? 'More actions' : 'Ещё действия';
      const secondary = document.createElement('div');
      secondary.className = 'job-more-content';
      appendUpscaleActions(secondary, job, upscales, en);
      const audio = document.createElement('a');
      audio.href = `/audio-studio.html?source=job:${encodeURIComponent(job.job_id)}`;
      audio.textContent = en ? '♫ Sound and voice' : '♫ Звук и речь';
      secondary.append(audio);
      appendComfyActions(secondary, job, en);
      if (!settings.imported_external) secondary.append(jobButton('✎ Править', 'edit', job.job_id),
        jobButton('↻ Повторить', 'repeat', job.job_id));
      secondary.append(jobButton('Удалить', 'delete', job.job_id));
      more.append(moreTitle, secondary);
      actions.append(more);
    } else {
      actions.append(jobButton('✎ Править', 'edit', job.job_id));
      actions.append(jobButton('↻ Повторить', 'repeat', job.job_id,
        !['failed', 'interrupted'].includes(job.state)));
      if (['queued_local', 'uploading', 'queued_gpu', 'running', 'downloading', 'needs_reconciliation'].includes(job.state)
          && !(isCloudJob(job) && job.state === 'running'))
        actions.append(jobButton(job.state === 'running' ? '■ Прервать рендер' : 'Отменить', 'cancel', job.job_id));
      if (job.state === 'failed') actions.append(jobButton('Убрать ошибку', 'delete', job.job_id));
      if (job.state === 'cancelled') actions.append(jobButton('Убрать', 'delete', job.job_id));
      appendComfyActions(actions, job, en);
    }
    card.append(info, actions);
    feed.append(card);
  });
}

// Packed H3 sequence estimate from local_studio/token_preflight.py (docs/reference/H3_ENGINE_INTERNALS.md).
export function jobBudgetText(job, en = false) {
  let budget = null;
  try { budget = job.budget_json ? JSON.parse(job.budget_json) : null; } catch { budget = null; }
  if (!budget || !budget.total_rows) return '';
  const k = (n) => `${Math.round(n / 1000)}k`;
  const source = budget.video_rows ? ` · ${en ? 'source video' : 'исходник'} ${k(budget.video_rows)}` : '';
  return ` · ≈${k(budget.total_rows)} ${en ? 'tokens' : 'токенов'}${source}`;
}

