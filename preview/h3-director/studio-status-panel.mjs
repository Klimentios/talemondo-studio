// Mirrors the existing queue view; it never polls the GPU or mutates a job.
export function statusSummary({ reachable, title = '', detail = '', english = false }) {
  if (reachable !== true) return { summary: english ? 'GPU offline' : 'GPU недоступен', progress: null };
  const match = detail.match(/(?:Сэмплинг|Sampling):?\s*(\d{1,3})%/i);
  const value = match ? Number(match[1]) : null;
  const progress = value !== null && value >= 0 && value <= 100 ? value : null;
  return { summary: progress === null ? (english ? 'Server and queue' : 'Сервер и очередь') : `${english ? 'Sampling' : 'Сэмплинг'} ${progress}%`, progress };
}

function install() {
  const panel = document.getElementById('studioStatusPanel');
  const badge = document.querySelector('.gpu-badge');
  const title = document.getElementById('queueOverviewTitle');
  const detail = document.getElementById('queueOverviewDetail');
  if (!panel) return;
  document.addEventListener('pointerdown', (event) => { if (panel.open && !panel.contains(event.target)) panel.open = false; });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && panel.open) { panel.open = false; panel.querySelector('summary').focus(); } });
  if (!badge || !title || !detail) { installChatStatus(panel); return; }
  const update = () => {
    const english = document.documentElement.lang === 'en';
    const reachable = badge.classList.contains('is-ready');
    const known = badge.classList.contains('is-ready') || badge.classList.contains('is-offline');
    const status = statusSummary({ reachable, title: title.textContent, detail: detail.textContent, english });
    panel.querySelector('[data-status-summary]').textContent = known ? status.summary : (english ? 'Checking GPU…' : 'Проверяем GPU…');
    panel.querySelector('[data-status-server]').textContent = !known ? (english ? 'Checking…' : 'Проверяем…') : reachable ? (english ? 'Connected' : 'Подключён') : (english ? 'Unavailable' : 'Недоступен');
    panel.querySelector('[data-status-queue]').textContent = title.textContent || (english ? 'Checking queue…' : 'Проверяем очередь…');
    panel.querySelector('[data-status-detail]').textContent = detail.textContent || '';
    panel.querySelector('[data-status-load]').textContent = english ? 'GPU utilisation: no data' : 'Загрузка GPU: нет данных';
    panel.classList.toggle('is-online', known && reachable);
    const bar = panel.querySelector('progress');
    bar.hidden = status.progress === null;
    if (status.progress !== null) bar.value = status.progress;
  };
  const observer = new MutationObserver(update);
  for (const item of [badge, title, detail]) observer.observe(item, { attributes: true, childList: true, characterData: true, subtree: true });
  addEventListener('studio:localechange', update);
  update();
}
export function chatQueueView(health, queue, jobs, english = false) {
  if (!health || !Array.isArray(jobs) || !queue) return { known: false, reachable: false, title: english ? 'Studio unavailable' : 'Studio недоступна', detail: '' };
  const reachable = health.gpu_reachable === true;
  const active = jobs.filter((job) => ['queued_local', 'uploading', 'queued_gpu', 'running', 'stopping', 'downloading', 'needs_reconciliation'].includes(job.state));
  const current = active.find((job) => job.state === 'running') || active[0];
  let detail = '';
  if (reachable && current?.state === 'running' && Number.isFinite(current.sampling_value) && Number.isFinite(current.sampling_max) && current.sampling_max > 0 && current.sampling_value >= 0 && current.sampling_value <= current.sampling_max) {
    const percent = Math.floor(100 * current.sampling_value / current.sampling_max);
    detail = `${english ? 'Sampling' : 'Сэмплинг'}: ${percent}% (${current.sampling_value}/${current.sampling_max})`;
  } else if (reachable && current?.state === 'downloading') detail = english ? 'Downloading result to Mac' : 'Скачивание результата на Mac';
  else if (reachable && current?.state === 'uploading') detail = english ? 'Uploading references to GPU' : 'Передача референсов на GPU';
  else if (!reachable && active.length) detail = english ? 'Jobs are waiting for GPU' : 'Задания ожидают GPU';
  const title = queue.paused ? (english ? `Video queue paused · ${active.length} active` : `Видеоочередь на паузе · активных: ${active.length}`)
    : (english ? `Video jobs queued or running: ${active.length}` : `Видеозаданий в очереди и работе: ${active.length}`);
  return { known: true, reachable, title, detail, activeCount: active.length };
}

function installChatStatus(panel) {
  let timer;
  let running = false;
  async function update() {
    if (running || document.hidden) return;
    running = true;
    const english = document.documentElement.lang === 'en';
    let data;
    try {
      const responses = await Promise.all(['/api/health', '/api/queue', '/api/jobs'].map((url) => fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(8000) })));
      if (responses.some((response) => !response.ok)) throw new Error('unavailable');
      const [health, queue, jobs] = await Promise.all(responses.map((response) => response.json()));
      data = chatQueueView(health, queue, jobs, english);
    } catch { data = chatQueueView(null, null, null, english); }
    const status = statusSummary({ reachable: data.reachable, detail: data.detail, english });
    panel.querySelector('[data-status-summary]').textContent = data.known ? `${english ? 'Jobs' : 'Задач'}: ${data.activeCount} · ${status.summary}` : (english ? 'Studio unavailable' : 'Studio недоступна');
    panel.querySelector('[data-status-server]').textContent = !data.known ? (english ? 'Unknown' : 'Неизвестно') : data.reachable ? (english ? 'Connected' : 'Подключён') : (english ? 'Unavailable' : 'Недоступен');
    panel.querySelector('[data-status-queue]').textContent = data.title;
    panel.querySelector('[data-status-detail]').textContent = data.detail;
    panel.querySelector('[data-status-load]').textContent = english ? 'GPU utilisation: no data' : 'Загрузка GPU: нет данных';
    panel.classList.toggle('is-online', data.known && data.reachable);
    const bar = panel.querySelector('progress');
    bar.hidden = status.progress === null;
    if (status.progress !== null) bar.value = status.progress;
    running = false;
  }
  const schedule = () => { clearInterval(timer); if (!document.hidden) { void update(); timer = setInterval(update, 15000); } };
  document.addEventListener('visibilitychange', schedule);
  addEventListener('studio:localechange', () => void update());
  schedule();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
}
