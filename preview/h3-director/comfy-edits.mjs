// Studio ↔ ComfyUI on a job card: «⧉ Открыть в ComfyUI» opens the job's exact graph in the editor,
// «⇩ Правки из ComfyUI» lists graphs sent back with «⇪ Отправить в Studio», shows what changed and
// renders one (POST …/render: the GPU API rechecks it; a new job card appears with the next poll).
// The feed is rebuilt on every poll, so the open state and loaded edits live here, per job.
const panels = new Map();

const shortValue = (value) => {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text === undefined ? '—' : (text.length > 60 ? `${text.slice(0, 57)}…` : text);
};

export function changeLines(changes, en = false) {
  const lines = [
    ...changes.changed.map((c) => `${c.class_type} #${c.node} · ${c.input}: ${shortValue(c.before)} → ${shortValue(c.after)}`),
    ...changes.added.map((c) => `${en ? 'added' : 'добавлен'} ${c.class_type} #${c.node}`),
    ...changes.removed.map((c) => `${en ? 'removed' : 'удалён'} ${c.class_type} #${c.node}`),
  ];
  return lines.length ? lines : [en ? 'No changes' : 'Без изменений'];
}

function render(body, job, en) {
  const state = panels.get(job.job_id);
  body.replaceChildren();
  if (state.error) {
    const error = document.createElement('small');
    error.className = 'job-error';
    error.textContent = state.error;
    body.append(error);
  }
  if (!state.edits) {
    if (!state.error) body.append(en ? 'Loading…' : 'Загрузка…');
    return;
  }
  if (!state.edits.length) {
    const hint = document.createElement('small');
    hint.textContent = en ? 'Nothing yet: edit the graph in ComfyUI and press «⇪ Send to Studio».'
      : 'Пока пусто: поправь граф в ComfyUI и нажми «⇪ Отправить в Studio».';
    body.append(hint);
  }
  for (const edit of state.edits) {
    const item = document.createElement('div');
    item.className = 'comfy-edit';
    const title = document.createElement('strong');
    title.textContent = new Date(edit.saved_at * 1000).toLocaleString(en ? 'en-GB' : 'ru-RU');
    const list = document.createElement('ul');
    for (const line of changeLines(edit.changes, en).slice(0, 12)) {
      const li = document.createElement('li');
      li.textContent = line;
      list.append(li);
    }
    item.append(title, list);
    for (const problem of edit.problems) {
      const error = document.createElement('small');
      error.className = 'job-error';
      error.textContent = problem;
      item.append(error);
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = state.sent === edit.edit_id ? (en ? '✓ Queued' : '✓ Поставлено') : (en ? '▶ Render edit' : '▶ Рендер правки');
    button.disabled = Boolean(edit.problems.length) || state.busy || state.sent === edit.edit_id;
    button.addEventListener('click', () => renderEdit(body, job, en, edit.edit_id));
    item.append(button);
    body.append(item);
  }
}

async function errorText(response) {
  const data = await response.json().catch(() => null);
  return data?.detail || `HTTP ${response.status}`;
}

async function load(body, job, en) {
  const state = panels.get(job.job_id);
  state.error = '';
  render(body, job, en);
  const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/comfyui-edits`);
  if (response.ok) state.edits = (await response.json()).edits;
  else state.error = await errorText(response);
  if (body.isConnected) render(body, job, en);
}

async function renderEdit(body, job, en, editId) {
  const state = panels.get(job.job_id);
  state.busy = true;
  state.error = '';
  render(body, job, en);
  const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/comfyui-edits/${encodeURIComponent(editId)}/render`,
    { method: 'POST' });
  state.busy = false;
  if (response.ok) state.sent = editId;
  else state.error = await errorText(response);
  if (body.isConnected) render(body, job, en);
}

export function appendComfyActions(parent, job, en) {
  if (!job.remote_job_id) return;
  const link = document.createElement('a');
  link.href = `/api/jobs/${encodeURIComponent(job.job_id)}/comfyui`;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = en ? '⧉ Open in ComfyUI' : '⧉ Открыть в ComfyUI';
  const details = document.createElement('details');
  details.className = 'job-comfy-edits';
  const summary = document.createElement('summary');
  summary.textContent = en ? '⇩ Edits from ComfyUI' : '⇩ Правки из ComfyUI';
  const body = document.createElement('div');
  details.append(summary, body);
  if (!panels.has(job.job_id)) panels.set(job.job_id, { open: false, edits: null, error: '', busy: false, sent: '' });
  const state = panels.get(job.job_id);
  details.open = state.open;
  if (state.open) render(body, job, en);
  details.addEventListener('toggle', () => {
    if (!details.isConnected) return;
    state.open = details.open;
    if (details.open) load(body, job, en);
  });
  // The job's graph as an editable «Свой воркфлоу» (nodes, LoRAs, references); the job itself stays as it was.
  const fork = document.createElement('a');
  fork.href = `/workflows.html?job=${encodeURIComponent(job.job_id)}`;
  fork.textContent = en ? '⎇ Edit graph in Studio' : '⎇ Граф в свои воркфлоу';
  parent.append(link, fork, details);
}
