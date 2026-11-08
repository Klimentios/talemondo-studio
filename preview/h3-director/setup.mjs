// «Первый запуск» (setup.html): readiness checks with one fix each, GPU/ComfyUI addresses, agent hookup.
// Server: local_studio/setup_status.py.
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, { headers: options.body ? { 'Content-Type': 'application/json' } : {}, ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join('; ') : data?.detail || `HTTP ${response.status}`);
  return data;
}

function status(text, tone = '') {
  $('wfStatus').textContent = text;
  $('wfStatus').dataset.tone = tone;
}

async function load() {
  const data = await api('/api/setup/status');
  $('suChecks').innerHTML = data.checks.map((check) => `<div class="su-check" data-ok="${check.ok}">
    <span class="su-mark" aria-hidden="true">${check.ok ? '✓' : '!'}</span>
    <span><b>${esc(check.label)}</b><small>${esc(check.detail)}</small></span>
    ${check.fix ? `<code>${esc(check.fix)}</code>` : '<span></span>'}</div>`).join('');
  status(data.ready ? 'Всё готово: можно генерировать.' : `Не готово: ${data.checks.filter((c) => !c.ok).length} пункт(а) ниже.`, data.ready ? 'good' : '');
  const form = $('suSettings');
  form.gpu_url.value = data.settings.gpu_url || '';
  form.comfy_url.value = data.settings.comfy_url || '';
  const env = Object.entries(data.settings.from_launch_agent).filter(([, set]) => set).map(([key]) => key === 'gpu_url' ? 'GPU API' : 'ComfyUI');
  $('suEnv').textContent = env.length ? `${env.join(' и ')} заданы в LaunchAgent: они важнее сохранённых здесь.` : '';
  $('suAgents').innerHTML = ['claude', 'codex'].map((agent) => `<div class="su-agent"><b>${agent === 'claude' ? 'Claude Code' : 'Codex'}</b>
    ${data.agents.registered[agent] ? '<span class="su-saved">✓ подключён</span>'
      : `<button type="button" class="k-btn k-btn--accent" data-agent="${agent}">Подключить</button>`}
    <details><summary>Команда вручную</summary><pre>${esc(data.agents[agent])}</pre></details></div>`).join('');
  const keys = data.keys || {};
  for (const form of document.querySelectorAll('.su-secret')) {
    const saved = { vast: keys.vast, minimax_api: keys.minimax_api }[form.dataset.kind];
    form.querySelector('.su-saved').hidden = !saved;
  }
  $('suSshPublic').hidden = !keys.ssh_public;
  $('suSshPublic').textContent = keys.ssh_public || '';
  $('suSshCopy').hidden = !keys.ssh_public;
  $('suSshCreate').hidden = Boolean(keys.ssh_public);
}

const refresh = () => load().catch((error) => status(error.message, 'bad'));

for (const form of document.querySelectorAll('.su-secret')) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = form.querySelector('input[name="value"]');
    try {
      await api('/api/setup/secret', { method: 'PUT', body: JSON.stringify({ kind: form.dataset.kind, value: input.value }) });
      input.value = '';
      status('Сохранено.', 'good');
      await refresh();
    } catch (error) { status(error.message, 'bad'); }
  });
}
$('suSshCreate').addEventListener('click', async () => {
  try { await api('/api/setup/ssh-key', { method: 'POST' }); status('SSH-ключ создан: скопируйте открытый ключ в аккаунт провайдера GPU.', 'good'); await refresh(); }
  catch (error) { status(error.message, 'bad'); }
});
$('suSshCopy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('suSshPublic').textContent); status('Открытый ключ скопирован.', 'good'); }
  catch { status('Не удалось скопировать: выделите ключ вручную.', 'bad'); }
});
$('suAgents').addEventListener('click', async (event) => {
  const agent = event.target.closest('[data-agent]')?.dataset.agent;
  if (!agent) return;
  try { await api(`/api/setup/agents/${agent}`, { method: 'POST' }); status('Агент подключён. Перезапустите его сессию, чтобы он увидел Studio.', 'good'); await refresh(); }
  catch (error) { status(error.message, 'bad'); }
});

$('suSettings').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    const saved = await api('/api/setup/settings', { method: 'PUT', body: JSON.stringify({ gpu_url: form.gpu_url.value, comfy_url: form.comfy_url.value }) });
    status(saved.note, 'good');
  } catch (error) { status(error.message, 'bad'); }
});
$('suRefresh').addEventListener('click', refresh);
window.studioBoot?.hold(load().catch((error) => status(error.message, 'bad')));
