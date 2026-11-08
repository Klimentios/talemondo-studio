// «Узлы и модели» (comfy-nodes.html): the GPU's node packs and model files, the user's ComfyUI manifest
// (pinned packs, SHA-256 models) and «Применить на GPU». Server: local_studio/comfy_manager.py.
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const STATE_RU = { installed: '✓ на GPU', pending: 'ждёт установки', unknown: 'GPU не отвечает' };
let inventory = null;

async function api(path, options = {}) {
  const response = await fetch(path, { headers: options.body ? { 'Content-Type': 'application/json' } : {}, ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(Array.isArray(data?.detail) ? data.detail.map((d) => d.msg.replace(/^Value error, /, '')).join('; ') : data?.detail || `HTTP ${response.status}`);
  return data;
}

function status(text, tone = '') {
  $('wfStatus').textContent = text;
  $('wfStatus').dataset.tone = tone;
}

function renderManifest(manifest) {
  const rows = [
    ...manifest.nodes.map((n) => ({ kind: 'nodes', id: n.id, title: n.dir, sub: `${n.repo} @ ${n.commit.slice(0, 12)}${n.pip.length ? ` · pip: ${n.pip.join(' ')}` : ''}`, state: n.state, note: n.note })),
    ...manifest.models.map((m) => ({ kind: 'models', id: m.id, title: `${m.folder}/${m.filename}`, sub: `sha256 ${m.sha256.slice(0, 16)}… · ${m.url}`, state: m.state, note: m.note })),
  ];
  $('cnManifest').innerHTML = rows.length ? rows.map((row) => `<div class="wf-inbox-row">
    <span><b>${esc(row.title)}</b> <span class="cn-state" data-state="${row.state}">${STATE_RU[row.state]}</span><br><small class="wf-hint">${esc(row.sub)}${row.note ? ` · ${esc(row.note)}` : ''}</small></span>
    <button type="button" class="k-btn k-btn--ghost" data-remove="${row.kind}/${row.id}">Убрать</button></div>`).join('')
    : '<p class="wf-hint">Пусто. Добавьте пакет узлов или модель ниже — они встанут на GPU кнопкой «Применить на GPU» и на каждый новый сервер.</p>';
  $('cnApply').disabled = !rows.length;
  $('cnManifest').querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('Убрать из манифеста? С уже установленного сервера ничего не удаляется.')) return;
    try { renderManifest(await api(`/api/comfy/manifest/${button.dataset.remove}`, { method: 'DELETE' })); status('Убрано из манифеста', 'good'); }
    catch (error) { status(error.message, 'bad'); }
  }));
}

function renderInventory() {
  const box = $('cnInventory');
  if (!inventory.reachable) { box.innerHTML = `<p class="wf-hint">${esc(inventory.error)}. Поднимите сервер в «GPU и модели».</p>`; return; }
  const query = $('cnFilter').value.trim().toLowerCase();
  const match = (text) => !query || text.toLowerCase().includes(query);
  const packs = inventory.packs.map((pack) => ({ ...pack, shown: match(pack.name) ? pack.classes : pack.classes.filter(match) })).filter((pack) => pack.shown.length);
  const folders = Object.entries(inventory.models).map(([folder, files]) => [folder, match(folder) ? files : files.filter(match)]).filter(([, files]) => files.length);
  box.innerHTML = `<p class="wf-hint">${inventory.classes} узлов в ${inventory.packs.length} пакетах · моделей: ${Object.values(inventory.models).reduce((n, f) => n + f.length, 0)}</p>
    <div class="cn-columns"><div><h3>Пакеты узлов</h3>${packs.map((pack) => `<details class="cn-pack"><summary>${esc(pack.name)} <small>${pack.shown.length}${pack.core ? ' · встроенные' : ''}</small></summary>
      <p class="cn-classes">${pack.shown.map(esc).join(', ')}</p></details>`).join('') || '<p class="wf-hint">Ничего не найдено.</p>'}</div>
    <div><h3>Модели</h3>${folders.map(([folder, files]) => `<details class="cn-pack"><summary>${esc(folder)} <small>${files.length}</small></summary>
      <p class="cn-classes">${files.map(esc).join('<br>')}</p></details>`).join('') || '<p class="wf-hint">Ничего не найдено.</p>'}</div></div>`;
  $('cnFolders').innerHTML = Object.keys(inventory.models).map((folder) => `<option value="${esc(folder)}">`).join('');
}

async function load(fresh = false) {
  const [manifest, inv] = await Promise.all([api('/api/comfy/manifest'), api(`/api/comfy/inventory${fresh ? '?fresh=true' : ''}`)]);
  inventory = inv;
  renderManifest(manifest);
  renderInventory();
}

async function poll(operationId) {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const op = await api(`/api/compute/operations/${operationId}`).catch(() => null);
    if (!op) return;
    const last = (op.log_tail || '').trim().split('\n').slice(-1)[0] || '';
    if (op.state === 'running' || op.state === 'starting') { status(`Устанавливаю на GPU… ${last}`); continue; }
    status(op.state === 'succeeded' || op.exit_code === 0 ? 'Установлено на GPU' : `Установка: ${op.state}. ${last}`, op.exit_code === 0 ? 'good' : 'bad');
    await load(true);
    return;
  }
}

$('cnApply').addEventListener('click', async () => {
  if (!confirm('Установить манифест на текущий GPU-сервер? Если что-то поставится, ComfyUI перезапустится (только при пустой очереди).')) return;
  try { const op = await api('/api/comfy/manifest/apply', { method: 'POST' }); status('Установка запущена…'); poll(op.operation_id); }
  catch (error) { status(error.message, 'bad'); }
});
$('cnNodeForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    renderManifest(await api('/api/comfy/manifest/nodes', { method: 'POST', body: JSON.stringify({
      repo: form.repo.value, commit: form.commit.value, pip: form.pip.value.split(/\s+/).filter(Boolean), note: form.note.value }) }));
    form.reset();
    status('Пакет узлов добавлен в манифест', 'good');
  } catch (error) { status(error.message, 'bad'); }
});
$('cnModelForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    renderManifest(await api('/api/comfy/manifest/models', { method: 'POST', body: JSON.stringify({
      folder: form.folder.value, filename: form.filename.value, url: form.url.value, sha256: form.sha256.value, note: form.note.value }) }));
    form.reset();
    status('Модель добавлена в манифест', 'good');
  } catch (error) { status(error.message, 'bad'); }
});
$('cnFilter').addEventListener('input', () => inventory && renderInventory());
$('cnRefresh').addEventListener('click', () => load(true).catch((error) => status(error.message, 'bad')));
window.studioBoot?.hold(load().catch((error) => status(error.message, 'bad')));
