// «Задачи агента»: pure helpers for tasks.mjs. Server: local_studio/orchestrator.py.

export const RUN_STATUS = {
  running: ['Агент работает', 'is-running'],
  waiting_user: ['Ждёт твоего ответа', 'is-asking'],
  waiting_jobs: ['Ждёт рендер', 'is-waiting'],
  idle: ['Агент остановился', 'is-idle'],
  done: ['Готово', 'is-done'],
  failed: ['Не получилось', 'is-failed'],
  stopped: ['Остановлена', 'is-idle'],
};

export const STEP_MARK = { pending: '○', running: '◐', waiting: '◔', done: '●', failed: '✕', skipped: '–' };

export const statusOf = (run) => RUN_STATUS[run?.status] || [run?.status || '—', ''];

/** Questions the user still has to answer, newest first. */
export function openAsks(run) {
  return (run?.asks || []).filter((ask) => !ask.answer).reverse();
}

/** Links for a step's artifacts: what the Studio can open. */
export function artifactLinks(artifacts = {}) {
  const links = [];
  const ids = (value) => (Array.isArray(value) ? value : value ? [value] : []).map(String);
  if (artifacts.scene || artifacts.scene_id) links.push({ label: 'Сцена на холсте', href: '/?view=scenes' });
  for (const id of ids(artifacts.job_ids || artifacts.job_id)) links.push({ label: `Рендер ${id.slice(0, 8)}`, href: '/#jobQueue' });
  for (const id of ids(artifacts.render_id)) links.push({ label: `Монтаж ${id.slice(0, 8)}`, href: `/api/edl/renders/${encodeURIComponent(id)}/file` });
  for (const id of ids(artifacts.export_id)) links.push({ label: `Экспорт ${id.slice(0, 8)}`, href: `/api/edl/exports/${encodeURIComponent(id)}/zip` });
  if (typeof artifacts.url === 'string' && artifacts.url.startsWith('/')) links.push({ label: 'Открыть', href: artifacts.url });
  return links;
}

export function progress(run) {
  const steps = run?.steps || [];
  return { done: steps.filter((step) => step.status === 'done').length, total: steps.length };
}

/** What the run waits for, as one line. */
export function waitLine(wait) {
  if (!wait) return '';
  const parts = [[wait.job_ids, 'рендеров'], [wait.keyframe_ids, 'кадров'], [wait.render_ids, 'сборок']]
    .filter(([list]) => list?.length).map(([list, label]) => `${list.length} ${label}`);
  return parts.length ? `Ждёт: ${parts.join(', ')}${wait.note ? ` — ${wait.note}` : ''}` : '';
}
