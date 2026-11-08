import {referencePreview} from './reference-preview.mjs?v=20261009-1';
import { errorText } from './api-error.mjs?v=20261008-1';
const terminal = new Set(['ready', 'needs_input', 'failed', 'cancelled']);
const names = { queued_local: 'Ожидает свободный GPU', uploading: 'Отправка материалов',
  running: 'Агент анализирует сцену', needs_reconciliation: 'Требуется сверка предыдущей попытки',
  ready: 'Контекст готов', needs_input: 'Нужны исправления', failed: 'Ошибка подготовки', cancelled: 'Подготовка отменена' };
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props); node.append(...children); return node;
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(data, response.status));
  return data;
}
function install() {
  const helpers = document.querySelector('.prompt-helpers');
  if (!helpers) return;
  const open = el('button', { type: 'button', className: 'scenarist-open' }, 'Контекст сцены');
  helpers.append(open);
  const brief = el('textarea', { rows: 5, ariaLabel: 'Замысел сцены' });
  const preserve = el('textarea', { rows: 2, placeholder: 'Например: лицо и костюм героя с фото, окружение и камера исходного видео' });
  const change = el('textarea', { rows: 2, placeholder: 'Например: перенести движения человека справа на героя с фото' });
  const shots = el('input', { type: 'number', min: 1, max: 6, value: 1 });
  const fresh = el('input', { type: 'checkbox' });
  const lines = el('textarea', { rows: 2, placeholder: 'Точные реплики: одна на строку. Язык сохраняется.' });
  const refs = el('div');
  const prepare = el('button', { type: 'button', className: 'scenarist-primary' }, 'Подготовить контекст');
  const cancel = el('button', { type: 'button', disabled: true }, 'Отменить подготовку');
  const render = el('button', { type: 'button', disabled: true }, 'Создать видео по этому контексту');
  const close = el('button', { type: 'button' }, 'Закрыть');
  const status = el('p', { role: 'status' });
  const review = el('div');
  const history = el('select', { ariaLabel: 'Сохранённые контексты' });
  const output = el('textarea', { rows: 12, readOnly: true, ariaLabel: 'Промпт H3' });
  const dialog = el('dialog', { className: 'scenarist-dialog' }, el('h2', {}, 'Контекст сцены · H3'),
    el('p', {}, 'Экспериментальная подготовка Ref2VA: фотографии героев и окружения, видео движения или исходной сцены. Проверь героев и действия перед созданием видео. Камера и оптика передаются в официальном промпте; точность исполнения оценивается по ролику.'),
    el('label', {}, 'Задача с референсами', brief), el('label', {}, 'Сохранить', preserve),
    el('label', {}, 'Изменить или перенести', change), el('label', {}, 'Шотов', shots),
    el('label', {}, 'Обязательные реплики', lines), el('label', { className: 'scenarist-check' }, fresh, ' Новый вариант понимания сцены'), refs,
    el('div', { className: 'scenarist-actions' }, prepare, cancel, render, close),
    status, el('label', {}, 'История подготовки', history), review,
    el('details', {}, el('summary', {}, 'Промпт для H3'), output));
  document.body.append(dialog);
  let snapshot, current, timer, busy = false, variation = '';
  const stop = () => { clearTimeout(timer); timer = null; };
  function show(row) {
    current = row; history.value = row.id;
    const result = row.result;
    if (['failed', 'cancelled', 'needs_input'].includes(row.state)) {
      const saved = JSON.parse(sessionStorage.getItem('h3-context-submit') || 'null');
      if (saved?.id === row.id) sessionStorage.removeItem('h3-context-submit');
    }
    status.textContent = `${names[row.state] || row.state}${result?.cache_hit ? ' · из кэша' : ''}${row.error ? ': ' + row.error : ''}`;
    render.disabled = row.state !== 'ready' || Boolean(result?.execution); cancel.disabled = terminal.has(row.state);
    output.value = result?.prompt || '';
    const nodes = [];
    if (result) {
      nodes.push(el('p', {}, 'Сохранённый контекст: ' + result.context.summary));
      nodes.push(referencePreview(result.bindings||[]));
      for (const [i, beat] of result.context.beats.entries()) nodes.push(el('p', {}, `Шот ${i + 1}: ${beat.initial_state} → ${beat.action} → ${beat.final_state}`));
      for (const problem of result.problems) nodes.push(el('p', { className: 'scenarist-problems' }, problem));
      nodes.push(el('p', {}, 'Создание использует материалы и настройки, сохранённые при подготовке этого контекста.'));
    }
    review.replaceChildren(...nodes);
    stop();
    if (!terminal.has(row.state) && dialog.open) timer = setTimeout(() => poll(row.id), 3000);
  }
  async function poll(id) {
    try { const row = await api('/api/context-ir/jobs/' + id); if (current?.id === id) show(row); }
    catch (e) { status.textContent = e.message; if (dialog.open && current?.id === id) timer = setTimeout(() => poll(id), 10000); }
  }
  async function loadHistory() {
    const rows = (await api('/api/context-ir/jobs')).filter(row => !row.result?.execution);
    history.replaceChildren(el('option', { value: '' }, 'Выбрать сохранённую подготовку'),
      ...rows.map(row => el('option', { value: row.id }, `${row.created_at} · ${names[row.state] || row.state}`)));
    if (current) history.value = current.id;
    return rows;
  }
  open.addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    try {
      snapshot = null; prepare.disabled = true;
      snapshot = await window.h3Studio.contextSnapshot();
      if (!snapshot.references.length || snapshot.references.some(ref => !['image', 'video'].includes(ref.kind))) {
        throw new Error('Прикрепи фото или видео-референсы. Анализ отдельных аудиофайлов пока недоступен.');
      }
      prepare.disabled = false;
      brief.value = snapshot.brief;
      refs.replaceChildren(...snapshot.references.map(ref => {
        const owner = el('input', { value: ref.owner, placeholder: 'Владелец / имя героя', ariaLabel: `Владелец фото ${ref.number}` });
        owner.addEventListener('input', () => { ref.owner = owner.value.trim(); });
        const performer = el('input', { value: ref.performer || '', placeholder: 'Исполнитель в видео: человек слева…', ariaLabel: `Исполнитель видео ${ref.number}` });
        performer.addEventListener('input', () => { ref.performer = performer.value.trim(); });
        return el('label', {}, `${ref.kind === 'image' ? 'Фото' : 'Видео'} ${ref.number}: ${ref.role}`, owner, ...(ref.kind === 'video' ? [performer] : []));
      }));
      refs.prepend(referencePreview(snapshot.references,{title:'Материалы новой подготовки'}));
      dialog.showModal();
      const rows = await loadHistory();
      if (current) show(await api('/api/context-ir/jobs/' + current.id));
      else if (rows.length) show(rows[0]);
    } catch (e) { status.textContent = e.message; if (!dialog.open) dialog.showModal();
      try { await loadHistory(); } catch { /* The original snapshot error remains visible. */ } }
    finally { busy = false; }
  });
  history.addEventListener('change', async () => {
    if (!history.value) return;
    stop(); current = { id: history.value };
    try { const row = await api('/api/context-ir/jobs/' + history.value); if (row.id === current.id) show(row); }
    catch (e) { status.textContent = e.message; }
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close',()=>{stop();for(const media of dialog.querySelectorAll('video,audio'))media.pause();});
  prepare.addEventListener('click', async () => {
    if (!snapshot) return;
    prepare.disabled = true; stop();
    try {
      const payload = { ...snapshot, brief: brief.value.trim(), preserve: preserve.value.trim(), change: change.value.trim(), shots: Number(shots.value),
        required_lines: lines.value.split('\n').map(s => s.trim()).filter(Boolean),
        variation_id: fresh.checked ? (variation = crypto.randomUUID()) : variation };
      fresh.checked = false;
      const encoded = JSON.stringify(payload);
      const previous = JSON.parse(sessionStorage.getItem('h3-context-submit') || 'null');
      const receipt = previous?.encoded === encoded ? previous : { encoded, id: crypto.randomUUID() };
      sessionStorage.setItem('h3-context-submit', JSON.stringify(receipt));
      const row = await api('/api/context-ir/jobs', { ...payload, client_request_id: receipt.id });
      // A terminal rejected/cancelled attempt may be corrected or deliberately prepared again.
      if (['failed', 'cancelled', 'needs_input'].includes(row.state)) sessionStorage.removeItem('h3-context-submit');
      show(row); await loadHistory();
    } catch (e) { status.textContent = e.message; }
    finally { prepare.disabled = false; }
  });
  cancel.addEventListener('click', async () => {
    if (!current) return;
    const id = current.id; cancel.disabled = true;
    try { const row = await api(`/api/context-ir/jobs/${id}/cancel`, {}); if (current?.id === id) show(row); }
    catch (e) { status.textContent = e.message; }
  });
  render.addEventListener('click', async () => {
    if (current?.state !== 'ready') return;
    const id = current.id; render.disabled = true;
    const key = 'h3-context-render-' + id;
    const requestId = sessionStorage.getItem(key) || crypto.randomUUID();
    sessionStorage.setItem(key, requestId);
    try {
      const job = await api(`/api/context-ir/jobs/${id}/render`, { client_request_id: requestId });
      status.textContent = 'Видео добавлено в обычную очередь: ' + job.job_id;
      // Keep receipt for retries/reloads. A separate preparation/variation makes a new take.
    } catch (e) { status.textContent = e.message; if (current?.id === id) render.disabled = false; }
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
