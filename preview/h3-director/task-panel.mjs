// The task side of an agent chat (agent-chat.mjs): plan, steps, questions with «Да/Нет» gates, waits for renders,
// helpers and the log of a chat that runs as a task (local_studio/orchestrator.py). Polls while it is shown and
// redraws only what changed, so a click on an answer is never swallowed by a redraw.
import { productionIds, jobStatus } from './chat-production-model.mjs?v=20261103-no-qwen-1';
import { STEP_MARK, artifactLinks, openAsks, progress, statusOf, waitLine } from './tasks-model.mjs?v=20261028-1';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (['className', 'value', 'disabled', 'hidden', 'title'].includes(key)) node[key] = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false && child !== ''));
  return node;
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
  return payload;
}
const post = (url, body) => json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function mountTaskPanel(box, { say = () => {}, onChange = () => {} } = {}) {
  let run = null;
  let drawn = '';
  let timer = null;
  let activity = [];
  let polling = false;

  async function readActivity(current) {
    const ids = productionIds(current);
    const get = async (kind, id, url) => {
      try { return { kind, id, data: await json(url) }; }
      catch { return { kind, id, data: { state: 'missing' } }; }
    };
    const scenes = await Promise.all(ids.scene.map((id) => get('scene', id, `/api/scene-runs/${encodeURIComponent(id)}`)));
    for (const scene of scenes) for (const cut of scene.data.cuts || []) {
      if (cut.shotJobId && !ids.job.includes(cut.shotJobId)) ids.job.push(cut.shotJobId);
    }
    return [...scenes, ...await Promise.all([
      ...ids.job.map((id) => get('job', id, `/api/jobs/${encodeURIComponent(id)}`)),
      ...ids.render.map((id) => get('render', id, `/api/edl/renders/${encodeURIComponent(id)}`)),
    ])];
  }
  function activityCard(item) {
    const { kind, id, data } = item;
    const label = { scene: 'Сцена', job: 'Видео', render: 'Монтаж' }[kind];
    const status = kind === 'scene' && data.message ? data.message : jobStatus(data);
    const href = kind === 'job' && data.state === 'completed' ? `/api/jobs/${encodeURIComponent(id)}/video` : '';
    return el('li', {}, el('strong', {}, `${label} · ${id.slice(0, 8)}`), el('span', {}, status),
      data.error ? el('small', {}, data.error) : null,
      href ? el('a', { href, target: '_blank', rel: 'noopener' }, 'Открыть результат') : null);
  }

  function askCard(ask) {
    const free = el('input', { className: 'k-field', placeholder: 'Свой ответ' });
    const reply = async (answer) => {
      if (!answer.trim()) return;
      try { run = await post(`/api/runs/${run.id}/answer`, { ask_id: ask.id, answer }); render(); onChange(run); } catch (error) { say(error.message); }
    };
    return el('section', { className: `tk-ask${ask.gate ? ' is-gate' : ''}` },
      el('strong', {}, ask.gate === 'gpu' ? 'Нужно согласие на GPU' : ask.gate === 'story' ? 'Нужно одобрение сюжета' : 'Вопрос агента'),
      el('p', {}, ask.question),
      ask.gate === 'gpu' ? el('small', {}, `Разрешение на ${ask.gpu_uses || 1} ${ask.gpu_uses === 1 ? 'действие' : 'действий'} агента с GPU`) : null,
      el('div', { className: 'tk-row' }, ...ask.options.map((option, index) => el('button', { type: 'button',
        className: `k-btn k-btn--sm ${index ? '' : 'k-btn--primary'}`, onclick: () => void reply(option) }, option)),
      ask.gate ? null : free, ask.gate ? null : el('button', { type: 'button', className: 'k-btn k-btn--sm', onclick: () => void reply(free.value) }, 'Ответить')));
  }

  function render() {
    drawn = JSON.stringify([run, activity]);
    box.hidden = !run;
    if (!run) { box.replaceChildren(); return; }
    const [label, tone] = statusOf(run);
    const { done, total } = progress(run);
    box.replaceChildren(...[
      el('header', { className: 'tk-head' }, el('strong', {}, 'Задача'), el('span', { className: `tk-badge ${tone}` }, run.busy ? 'Агент работает' : label)),
      typeof run.gates?.gpu === 'object' ? el('p', { className: 'tk-wait' }, `Разрешённых действий GPU осталось: ${run.gates.gpu.remaining || 0}`) : null,
      el('div', { className: 'tk-progress' }, el('span', { style: `width:${total ? (done / total) * 100 : 0}%` })),
      ...openAsks(run).map(askCard),
      run.wait ? el('p', { className: 'tk-wait' }, waitLine(run.wait), el('small', {}, ' Studio разбудит агента, когда всё будет готово.')) : null,
      el('p', { className: 'tk-wait' }, 'Для постановки и сборки сцены пока требуется открытая вкладка ', el('a', { href: '/?view=scenes', target: '_blank', rel: 'noopener' }, '«Видео студия»'), '.'),
      activity.length ? el('section', { className: 'tk-activity', 'aria-label': 'Ход генерации' }, el('strong', {}, 'Ход генерации'), el('ul', {}, ...activity.map(activityCard))) : null,
      run.summary ? el('section', { className: 'tk-summary' }, el('strong', {}, 'Итог'), el('p', {}, run.summary)) : null,
      (run.steps || []).length ? el('ol', { className: 'tk-steps' }, ...run.steps.map((step) => el('li', { className: `is-${step.status}` },
        el('span', { className: 'tk-mark' }, STEP_MARK[step.status] || '○'),
        el('div', {}, el('strong', {}, step.title), step.detail ? el('p', {}, step.detail) : null,
          ...artifactLinks(step.artifacts).map((link) => el('a', { href: link.href, target: '_blank', rel: 'noopener' }, link.label))))))
        : el('p', { className: 'tk-wait' }, 'Агент составляет план…'),
      (run.helpers || []).length ? el('section', { className: 'tk-helpers' }, el('strong', {}, 'Помощники'),
        ...run.helpers.map((helper) => el('details', {}, el('summary', {}, `${helper.role} · ${helper.status === 'done' ? 'ответил' : helper.status === 'failed' ? 'ошибка' : 'работает'}`),
          el('p', {}, helper.task), helper.answer ? el('p', {}, helper.answer) : null,
          helper.report?.verdict ? el('p', {}, `Вывод: ${{ pass: 'проверка пройдена', fail: 'есть замечания', uncertain: 'недостаточно данных' }[helper.report.verdict] || helper.report.verdict}`) : null,
          ...(helper.report?.findings || []).map((finding) => el('p', {}, finding)),
          ...(helper.report?.uncertainties || []).map((item) => el('p', {}, `Неизвестно: ${item}`)),
          helper.report?.suggested_fix ? el('p', {}, `Предложение: ${helper.report.suggested_fix}`) : null,
          helper.error ? el('p', {}, `Ошибка: ${helper.error}`) : null,
          helper.chat_id ? el('a', { href: `/agent-chat.html?chat=${encodeURIComponent(helper.chat_id)}` }, 'Разговор помощника') : null))) : null,
      el('div', { className: 'tk-row' },
        el('button', { type: 'button', className: 'k-btn k-btn--sm', disabled: run.busy || run.status === 'done', onclick: async () => {
          try { run = await post(`/api/runs/${run.id}/continue`, {}); render(); onChange(run); } catch (error) { say(error.message); }
        } }, 'Продолжить'),
        el('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--danger', disabled: ['done', 'stopped'].includes(run.status), onclick: async () => {
          if (!window.confirm('Остановить задачу? Агент и его помощники будут остановлены.')) return;
          try { run = await post(`/api/runs/${run.id}/stop`, {}); render(); onChange(run); } catch (error) { say(error.message); }
        } }, 'Стоп')),
      el('details', { className: 'tk-log' }, el('summary', {}, 'Журнал'),
        el('ul', {}, ...(run.log || []).slice().reverse().map((entry) => el('li', {}, el('small', {}, entry.at), ' ', entry.text))))]
      .filter(Boolean));
  }

  async function poll() {
    if (!run || polling) return;
    const id = run.id;
    polling = true;
    try {
      const fresh = await json(`/api/runs/${id}`);
      const nextActivity = await readActivity(fresh);
      if (!run || run.id !== id) return;
      const wasBusy = run.busy;
      run = fresh;
      activity = nextActivity;
      const changed = JSON.stringify([run, activity]) !== drawn;
      if (changed && !box.contains(document.activeElement)) render();
      if (changed && (wasBusy !== fresh.busy)) onChange(run);
    } catch { /* Retain the last known state; a later poll retries. */ }
    finally { polling = false; }
  }

  return {
    async show(runId) {
      clearInterval(timer);
      run = runId ? await json(`/api/runs/${runId}`).catch(() => null) : null;
      activity = [];
      render();
      if (run) { void poll(); timer = setInterval(() => void poll(), 3000); }
    },
    get run() { return run; },
  };
}
