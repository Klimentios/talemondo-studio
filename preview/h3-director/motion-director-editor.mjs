// Edits the saved plan of one captured scene. Opening/saving never renders.
const modes = { t2v: 'По описанию', i2v: 'По первому кадру', fl2v: 'Первый и последний кадр', r2v: 'По референсам', source_video: 'По исходному видео' };
function node(tag, text = '', attrs = {}) {
  const element = document.createElement(tag);
  element.textContent = text;
  Object.assign(element, attrs);
  return element;
}
async function api(path, method = 'GET', body) {
  const response = await fetch(path, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail || `HTTP ${response.status}`));
    error.status = response.status;
    throw error;
  }
  return value;
}
export function editedMotionPlan(original, fields) {
  const plan = structuredClone(original);
  plan.seed = Number(fields.seed); plan.steps = Number(fields.steps);
  plan.segments = (fields.order || plan.segments).map((segment, index) => ({ ...structuredClone(segment), ...fields.segments[index] }));
  return plan;
}
// Composition edits keep each segment's mode and reference bindings; a copy gets a fresh unique ID.
export function reorderedSegments(segments, op, index) {
  const list = segments.map(segment => structuredClone(segment));
  if (op === 'up' && index > 0) [list[index - 1], list[index]] = [list[index], list[index - 1]];
  else if (op === 'down' && index < list.length - 1) [list[index], list[index + 1]] = [list[index + 1], list[index]];
  else if (op === 'remove' && list.length > 1) list.splice(index, 1);
  else if (op === 'copy' && list.length < 60) {
    const ids = new Set(list.map(segment => segment.id));
    let n = 2; while (ids.has(`${list[index].id}-${n}`)) n++;
    list.splice(index + 1, 0, { ...structuredClone(list[index]), id: `${list[index].id}-${n}` });
  }
  if (list.length) { list[0].visual_continuity = false; list[0].audio_continuity = false; }
  return list;
}
export function receiptLine(row) {
  const metrics = row.metrics_json ? JSON.parse(row.metrics_json) : {};
  const parts = [`Версия ${row.revision} · ${row.state}`];
  if (Array.isArray(metrics.run_selection)) parts.push(`перегенерированы: ${metrics.run_selection.join(', ')}`);
  else if (metrics.run_selection === 'all') parts.push('все сегменты');
  if (metrics.gpu_seconds) parts.push(`GPU ${Math.round(metrics.gpu_seconds / 60)} мин (${metrics.gpu_seconds_per_video_second} с на секунду видео)`);
  // ComfyUI failures arrive as a dump of its status messages; the exception text is what matters.
  if (row.error) parts.push(row.error.match(/'exception_message': (['"])((?:(?!\1)[^\\]|\\.)*)\1/)?.[2].replace(/\\n/g, ' ').trim() || row.error);
  return parts.join(' · ');
}
export async function openMotionDirectorEditor(studio) {
  const sceneId = studio.sceneState?.().activeSceneId || studio.project?.().activeSceneId;
  if (!sceneId) throw new Error('Сначала выберите сцену');
  const base = `/api/scene-runs/${encodeURIComponent(sceneId)}/motion-plan`;
  const execution = `/api/studio/motion-director/scenes/${encodeURIComponent(sceneId)}`;
  const dialog = node('dialog', '', { className: 'production-spec-editor' });
  const shell = node('div', '', { className: 'director-dialog-shell' });
  const header = node('header', '', { className: 'director-dialog-header' });
  const close = node('button', 'Закрыть', { type: 'button', className: 'k-btn' });
  close.onclick = () => dialog.close();
  header.append(node('h2', 'Непрерывная сцена · Motion Director'), close);
  const message = node('p', 'Загрузка плана…', { role: 'status' });
  message.setAttribute('aria-live', 'polite');
  const body = node('div', '', { className: 'production-spec-fields' });
  const jobs = node('section');
  shell.append(header, node('p', `Сцена: ${sceneId}. Экспериментальный движок. Результат требует проверки персонажей, движения и звука.`), message, body, jobs);
  dialog.append(shell); document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
  let saved, busy = false;
  const jobsView = async () => {
    const rows = await api(execution + '/jobs');
    if (!dialog.isConnected) return;
    jobs.replaceChildren(node('h3', 'Задания этой сцены'));
    for (const row of rows) {
      const line = node('p', receiptLine(row));
      line.style.overflowWrap = 'anywhere';
      if (row.file_url) line.append(node('a', ' Открыть MP4 · требуется QC', { href: row.file_url, target: '_blank', rel: 'noopener' }));
      if (row.remote_job_id) line.append(node('a', ' ⧉ Граф в ComfyUI', { href: `/api/comfyui/open/${encodeURIComponent(row.remote_job_id)}`, target: '_blank', rel: 'noopener' }));
      if (row.state === 'queued_local') {
        const cancel = node('button', 'Отменить ожидание', { type: 'button', className: 'k-btn' });
        cancel.onclick = () => action(async () => { await api(`${execution}/jobs/${encodeURIComponent(row.motion_id)}/cancel-waiting`, 'POST', {}); await jobsView(); });
        line.append(cancel);
      }
      jobs.append(line);
    }
    if (!rows.length) jobs.append(node('p', 'Генерация этой сцены ещё не запускалась.'));
  };
  const action = async (callback) => {
    if (busy) return;
    busy = true;
    try { await callback(); } catch (error) { message.textContent = error.message; }
    finally { busy = false; }
  };
  try {
    saved = await api(base);
    if (!dialog.isConnected) return;
    message.textContent = `Сохранённая версия: ${saved.revision}`;
    const form = node('form');
    const field = (parent, label, type, value, attrs = {}) => {
      const wrap = node('label', label);
      const input = node(type === 'textarea' ? 'textarea' : 'input', '', { ...(type === 'textarea' ? {} : { type }), className: 'k-field', ...attrs });
      if (type === 'checkbox') input.checked = Boolean(value); else input.value = value;
      wrap.append(input); parent.append(wrap); return input;
    };
    const seed = field(form, 'Seed сцены', 'number', saved.plan.seed, { min: '0', max: String(2 ** 53 - 1), step: '1', required: true });
    const steps = field(form, 'Шаги качества', 'number', saved.plan.steps, { min: '20', max: '25', step: '1', required: true });
    const list = node('div');
    let order = saved.plan.segments, segmentFields = [];
    const current = () => segmentFields.map(f => ({ prompt: f.prompt.value, duration: Number(f.duration.value),
      visual_continuity: f.visual.checked, audio_continuity: f.audio.checked }));
    const renderSegments = () => {
      list.replaceChildren();
      segmentFields = order.map((segment, index) => {
        const group = node('fieldset');
        group.append(node('legend', `${index + 1}. ${segment.id} · ${modes[segment.mode] || segment.mode}`));
        group.append(node('small', `Референсов: ${(segment.pictures || []).length}. Режим и привязки референсов сохраняются; новый сегмент — копия существующего.`));
        const prompt = field(group, 'Действие, камера и звук', 'textarea', segment.prompt, { required: true, maxLength: 20000 });
        const duration = field(group, 'Длительность, с', 'number', segment.duration, { min: '1', max: '15', step: 'any', required: true, disabled: segment.mode === 'source_video' });
        const visual = field(group, 'Продолжить движение предыдущего сегмента', 'checkbox', segment.visual_continuity, { disabled: index === 0 });
        const audio = field(group, 'Продолжить звук предыдущего сегмента', 'checkbox', segment.audio_continuity, { disabled: index === 0 });
        const tools = node('div', '', { className: 'director-dialog-actions' });
        for (const [op, label, off] of [['up', 'Выше', index === 0], ['down', 'Ниже', index === order.length - 1],
          ['copy', 'Дублировать', order.length >= 60], ['remove', 'Удалить', order.length === 1]]) {
          const button = node('button', label, { type: 'button', className: 'k-btn', disabled: off });
          button.onclick = () => {
            const values = current();
            order = reorderedSegments(order.map((item, i) => ({ ...item, ...values[i] })), op, index);
            dirty = true; editVersion++; renderSegments();
          };
          tools.append(button);
        }
        group.append(tools); list.append(group); return { prompt, duration, visual, audio };
      });
    };
    form.append(list);
    const save = node('button', 'Сохранить план', { type: 'submit', className: 'k-btn' });
    const reload = node('button', 'Обновить задания', { type: 'button', className: 'k-btn' });
    reload.onclick = () => action(jobsView);
    const approved = field(form, 'Сюжет этой сцены одобрен для генерации', 'checkbox', false);
    const fullRun = field(form, 'Перегенерировать все сегменты (иначе — только изменённые и их продолжения)', 'checkbox', false);
    const run = node('button', 'Запустить сохранённую версию', { type: 'button', className: 'k-btn' });
    let dirty = false, editVersion = 0;
    form.addEventListener('input', event => { if (event.target !== approved && event.target !== fullRun) { dirty = true; editVersion++; } });
    renderSegments();
    form.onsubmit = event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      action(async () => {
        const savingVersion = editVersion;
        const plan = editedMotionPlan(saved.plan, { seed: seed.value, steps: steps.value, order, segments: current() });
        saved = await api(base, 'PUT', { plan, expected_revision: saved.revision });
        order = saved.plan.segments;
        dirty = editVersion !== savingVersion;
        message.textContent = `Сохранена версия ${saved.revision}. Изменены сегменты и продолжения: ${saved.invalidated_ids.join(', ') || 'нет'}${saved.removed_ids.length ? '; удалены: ' + saved.removed_ids.join(', ') : ''}. Генерация не запущена.${dirty ? ' Более поздние правки ещё не сохранены.' : ''}`;
      });
    };
    run.onclick = () => action(async () => {
      if (dirty) throw new Error('Сначала сохраните изменения плана');
      if (!approved.checked) throw new Error('Запуск доступен для одобренного сюжета');
      const receipt = await api(execution + '/execute', 'POST', { expected_revision: saved.revision, story_approved: true, full_run: fullRun.checked });
      message.textContent = `Задание версии ${saved.revision}: ${receipt.state}. Повторный запрос этой версии использует прежнюю квитанцию.`;
      await jobsView();
    });
    form.append(save, reload, run); body.append(form); await jobsView();
  } catch (error) {
    if (dialog.isConnected) message.textContent = error.status === 404
      ? 'План Motion Director ещё не подготовлен. Попросите агента этой сцены сохранить сегменты и привязки референсов через motion_director_plan_save. Обычная сборка сцены доступна отдельно.' : error.message;
  }
}
