// What the result stage says about the live job. Pure function: tested without a browser.
// Progress comes from the GPU sampler (sampling_value / sampling_max) via the Mac worker;
// when the GPU is unreachable or the numbers stop updating, the stage says so instead of
// pretending a render is running.

const STALE_SECONDS = 120;

/** "2026-10-03 13:39:36" (SQLite UTC) → ms since epoch. */
export function parseUtc(value) {
  if (!value) return NaN;
  return Date.parse(String(value).replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? '' : 'Z'));
}

export function formatAge(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} с`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} ч`;
  return `${Math.round(seconds / 86400)} дн`;
}

export function stageStatus(job, { gpuReachable = true, now = Date.now() } = {}) {
  const max = Number(job?.sampling_max) || 0;
  const value = Math.min(max, Number(job?.sampling_value) || 0);
  const percent = max > 0 ? Math.floor((100 * value) / max) : null;
  const age = (now - parseUtc(job?.updated_at)) / 1000;
  const stale = Number.isFinite(age) && age > STALE_SECONDS;
  const steps = max > 0 ? `шаг ${value} из ${max} · ${percent}%` : 'сэмплинг ещё не начался';
  const updated = Number.isFinite(age) ? `обновлено ${formatAge(age)} назад` : '';

  if (job?.state === 'downloading') {
    return { title: 'Сохраняем готовый MP4', help: 'GPU закончил генерацию, файл скачивается на Mac.', caption: 'Скачивание MP4…', percent: 100, stale: false };
  }
  if (!gpuReachable) {
    return {
      title: job?.state === 'stopping' ? 'Отмена ждёт GPU' : 'GPU не подключён',
      help: `Связи с сервером нет. Последние данные: ${steps}${updated ? `, ${updated}` : ''}. Прогресс обновится, когда сервер вернётся.`,
      caption: `Нет связи с GPU · ${steps}`, percent, stale: true,
    };
  }
  if (job?.state === 'stopping') {
    return { title: 'Генерация отменяется', help: 'Ждём подтверждения отмены от GPU.', caption: `Отмена · ${steps}`, percent, stale };
  }
  return {
    title: 'Генерация идёт',
    help: stale ? `GPU давно не присылал шагов (${updated}). Возможно, идёт загрузка модели или декодирование.`
      : max > 0 ? 'Черновой кадр обновляется примерно раз в 6 секунд.' : 'Готовим модель и референсы на GPU…',
    caption: `Генерация · ${steps}${updated ? ` · ${updated}` : ''}`, percent, stale,
  };
}

export const PREVIEW_INTERVAL_MS = 6000;

/** Poll /api/jobs/ID/preview only while the GPU answers; polling resumes when /api/health says it is back. */
export function shouldPollPreview({ gpuReachable, hidden = false, inFlight = false, lastRequest = 0, now = Date.now() }) {
  return Boolean(gpuReachable) && !hidden && !inFlight && now - lastRequest >= PREVIEW_INTERVAL_MS;
}
