// Finished-clip passes (app/upscale_graph.MODES): SeedVR2 2x, RIFE 48 fps, or both.
// Rendering only; network calls stay in app.mjs.
const MODE_LABELS = {
  upscale: { ru: '↑ Апскейл 2×', en: '↑ Upscale 2×', done: { ru: 'апскейл 2×', en: 'upscale 2×' } },
  smooth: { ru: '≋ Плавность 48 fps', en: '≋ Smooth 48 fps', done: { ru: 'плавность 48 fps', en: 'smooth 48 fps' } },
  upscale_smooth: { ru: '↑≋ Апскейл + 48 fps', en: '↑≋ Upscale + 48 fps', done: { ru: 'апскейл + 48 fps', en: 'upscale + 48 fps' } },
};
const STATES = {
  queued_local: 'ждёт очереди', uploading: 'передача на GPU', needs_reconciliation: 'нужна сверка',
  queued_gpu: 'в очереди GPU', running: 'обработка', downloading: 'сохранение MP4',
};

export function appendUpscaleActions(actions, job, upscales, en = false) {
  if (job.state !== 'completed') return;
  const lang = en ? 'en' : 'ru';
  const mine = upscales.filter((item) => item.source_job_id === job.job_id);
  const active = mine.find((item) => !['completed', 'failed', 'cancelled'].includes(item.state));
  for (const item of mine.filter((entry) => entry.state === 'completed')) {
    const label = MODE_LABELS[item.mode || 'upscale'] || MODE_LABELS.upscale;
    const link = document.createElement('a');
    link.href = `/api/upscale/jobs/${encodeURIComponent(item.upscale_id)}/video`;
    link.textContent = `↓ ${en ? 'Download' : 'Скачать'} ${label.done[lang]}`;
    actions.append(link);
  }
  if (active) {
    const status = document.createElement('span');
    const label = (MODE_LABELS[active.mode || 'upscale'] || MODE_LABELS.upscale).done[lang];
    status.textContent = en ? `${label}: ${active.state}` : `${label}: ${STATES[active.state] || active.state}`;
    actions.append(status);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.dataset.action = 'upscale-cancel';
    cancel.dataset.id = job.job_id;
    cancel.dataset.upscaleId = active.upscale_id;
    cancel.textContent = en ? 'Cancel' : 'Отменить улучшение';
    actions.append(cancel);
    return;
  }
  const failed = mine.find((item) => item.state === 'failed');
  if (failed && mine[0] === failed) {
    const error = document.createElement('span');
    error.className = 'job-error';
    error.textContent = failed.error || (en ? 'Enhancement failed' : 'Ошибка улучшения');
    actions.append(error);
  }
}
