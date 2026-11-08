// «Версии» of a user workflow (workflows.html): every saved revision with what changed, the diff of a past one to
// the current graph, and «Вернуть», which saves that graph as the next revision (history is never rewritten).
// Server: GET /api/workflows/{id}/versions[/{rev}], POST …/restore (local_studio/user_workflows.py).
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const show = (v) => (typeof v === 'string' ? v : JSON.stringify(v) ?? '—').slice(0, 60);

export async function mountVersions(root, { workflow, api, status, onRestored }) {
  root.innerHTML = '<p class="wf-hint">Читаю историю…</p>';
  try {
    const { versions } = await api(`/api/workflows/${encodeURIComponent(workflow.id)}/versions`);
    root.innerHTML = `<table class="wf-params wfv-table">${versions.map((v) => `<tr>
      <td><b>r${v.revision}</b>${v.current ? ' · текущая' : ''}</td><td>${esc(v.note || '—')}</td>
      <td><small>${esc(v.updated_at || '')} · ${v.nodes} узлов</small></td>
      <td>${v.current ? '' : `<button type="button" class="k-btn k-btn--ghost" data-diff="${v.revision}">Отличия</button>
        <button type="button" class="k-btn" data-restore="${v.revision}">Вернуть</button>`}</td></tr>
      <tr hidden data-diff-row="${v.revision}"><td colspan="4"></td></tr>`).join('')}</table>`;
  } catch (error) {
    root.innerHTML = `<p class="wf-error">${esc(error.message)}</p>`;
    return;
  }
  root.querySelectorAll('[data-diff]').forEach((button) => button.addEventListener('click', async () => {
    const row = root.querySelector(`[data-diff-row="${button.dataset.diff}"]`);
    if (!row.hidden) { row.hidden = true; return; }
    try {
      const old = await api(`/api/workflows/${encodeURIComponent(workflow.id)}/versions/${button.dataset.diff}`);
      const c = old.changes_to_current;
      const lines = [...c.added.map((x) => `＋ ${x.class_type} #${x.node}`), ...c.removed.map((x) => `− ${x.class_type} #${x.node}`),
        ...c.changed.map((x) => `${x.class_type} #${x.node} · ${x.input}: ${show(x.before)} → ${show(x.after)}`)];
      row.firstElementChild.innerHTML = `<p class="wf-hint">От r${old.revision} к текущей:</p><ul>${lines.map((l) => `<li>${esc(l)}</li>`).join('') || '<li>одинаковые графы</li>'}</ul>`;
      row.hidden = false;
    } catch (error) { status(error.message, 'bad'); }
  }));
  root.querySelectorAll('[data-restore]').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm(`Сделать граф ревизии ${button.dataset.restore} текущим? Он сохранится как ревизия ${workflow.revision + 1}.`)) return;
    try {
      const saved = await api(`/api/workflows/${encodeURIComponent(workflow.id)}/restore`, { method: 'POST',
        body: JSON.stringify({ expected_revision: workflow.revision, revision: Number(button.dataset.restore) }) });
      status(`Возвращён граф ревизии ${button.dataset.restore}: теперь ревизия ${saved.revision}`, 'good');
      await onRestored(saved);
    } catch (error) { status(error.message, 'bad'); }
  }));
}
