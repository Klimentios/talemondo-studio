import { formatUsd, instanceControls } from './compute-model.mjs';

const $ = (selector, root = document) => root.querySelector(selector);
const root = $('#computeSection');
if (!root) throw new Error('Compute workspace is missing');

const computeMode = new URLSearchParams(location.search).get('view') === 'compute';
if (computeMode) {
  document.body.classList.add('compute-view');
  root.hidden = false;
  document.title = 'TaleMondo — Вычисления';
  for (const link of document.querySelectorAll('.top-nav a')) {
    const active = link.href.endsWith('?view=compute');
    link.classList.toggle('active', active);
    link.classList.toggle('rail-active', active);
  }
}

const state = { connected: false, instances: [], offers: [], loading: false, deployAvailable: false, deployMessage: '', health: null, endpoint: null, offersCheckedAt: null };
const watchingDeployments = new Set();
$('#computeAgent')?.addEventListener('toggle', (event) => {
  if (event.target.open) {
    const frame = $('#computeAgentFrame');
    if (frame && !frame.src) frame.src = frame.dataset.src;
  }
});
const EN = {
  'Подключён · GPU доступен':'Connected · GPU available','GPU сейчас недоступен':'GPU unavailable',
  'ответ получен':'responding','не отвечает':'not responding','адрес туннеля не найден':'tunnel address not found',
  'Загружаю серверы Vast…':'Loading Vast servers…','В аккаунте пока нет GPU-серверов.':'No GPU servers in this account yet.',
  'статус неизвестен':'status unknown','SSH endpoint появится после запуска':'SSH endpoint appears after startup',
  'Подключение':'Connection','GPU':'GPU','Системная RAM':'System RAM','Диск':'Disk','Состояние H3':'H3 status',
  'H3 готов':'H3 ready','H3 ещё не развёрнут/не подтверждён':'H3 is not deployed or verified',
  'Установка H3 выполняется':'H3 installation is running','GPU не подтвердил готовность после установки':'The GPU did not confirm it is ready after installation',
  'Установка не завершена':'Installation did not complete','Развернуть H3 на этом сервере':'Deploy H3 on this server',
  'Подготовка занимает время и оплачивается Vast. Текущий туннель переключится только после успешной проверки.':'Setup takes time and Vast will charge for it. The current tunnel switches only after verification succeeds.',
  'Для установки требуется SSH-ключ ~/.ssh/id_ed25519 на этом Mac.':'Installation requires the SSH key ~/.ssh/id_ed25519 on this Mac.',
  'Повторить установку':'Retry installation','Установить H3':'Install H3','Остановить сервер':'Stop server',
  'Удалить сервер…':'Delete server…','Этот сервер используется текущим туннелем Studio. Остановка отключит GPU. Удалить его можно после остановки; для перехода сначала разверни другой сервер.':'This server is used by the current Studio tunnel. Stopping it disconnects the GPU. You can delete it after stopping; deploy another server first if you want to switch Studio.',
  'Совместимых предложений пока нет. Убери ограничение цены или попробуй позже.':'No compatible offers are available. Raise the price limit or try later.',
  'Ищу доступные конфигурации…':'Searching available configurations…','Местоположение не указано':'Location not provided',
  'Надёжность':'Reliability','Подключи API key Vast.':'Connect a Vast API key.','Проверяю ключ и соединение с Vast…':'Checking the key and Vast connection…',
  'Vast подключён. Баланс и серверы загружены.':'Vast connected. Balance and servers loaded.',
  'Отключить Vast и удалить API key из macOS Keychain? Арендованные серверы останутся в аккаунте Vast.':'Disconnect Vast and remove the API key from macOS Keychain? Rented servers remain in your Vast account.',
  'Vast отключён. Арендованные серверы не изменены.':'Vast disconnected. Rented servers were not changed.',
  'Арендовать':'Rent','Сервер':'Server','Дополнительно возможна оплата диска и передачи данных. Сервер будет создан в аккаунте Vast.':'Disk and data transfer may cost extra. The server will be created in your Vast account.',
  'GPU освободится, данные сохранятся, но диск продолжит тарифицироваться. Доступность этой GPU при повторном запуске не гарантируется.':'The GPU will be released and data retained, but disk charges continue. The same GPU may not be available when restarted.',
  'Удаление безвозвратно сотрёт данные на сервере':'Deleting permanently erases data on server','Файлы и очередь Studio на Mac сохранятся. Для подтверждения введи ID сервера:':'Studio files and queue on this Mac stay intact. Type the server ID to confirm:',
  'ID не совпал; сервер не удалён.':'ID did not match; server was not deleted.','Операция займёт время; оплата Vast продолжается. Генерация не запускается.':'This takes time and Vast charges continue. No generation job will start.',
  'Установка H3 началась для сервера':'H3 installation started for server','Сервер удалён в Vast.':'Server deleted in Vast.',
  'Установка закончилась, но GPU не подтвердил готовность. Можно повторить установку или остановить сервер.':'Installation finished but the GPU did not confirm it is ready. You can retry the installation or stop the server.',
  'H3 проверен, GPU отвечает через Studio.':'H3 verified; GPU responds through Studio.','Установка завершилась ошибкой. Проверь журнал перед повтором.':'Installation failed. Review the log before retrying.',
  'Установка выполняется…':'Installation in progress…','Адрес Vast — это адрес SSH-подключения к арендованной машине. Studio обращается к GPU через локальный туннель 127.0.0.1:18189.':'Vast provides the SSH endpoint for the rented machine. Studio reaches its GPU through the local tunnel at 127.0.0.1:18189.',
  'H3 готов · Studio подключена':'H3 ready · Studio connected','Туннель Studio':'Studio tunnel','ч':'h',
  'Подключено':'Connected','Ключ будет сохранён только в macOS Keychain.':'The key is stored only in macOS Keychain.',
  'Проверено':'Checked','Надёжность':'Reliability','диск':'disk','сервер':'server',
  'Установка H3 ещё не завершена или требует сверки.':'H3 installation is still running or needs reconciliation.',
  'Запустить сервер':'Start server','Остановить сервер':'Stop server','Удалить сервер…':'Delete server…',
  'Выбрать сервер':'Select server','Найти серверы':'Find servers',
  'Предложения устарели, обнови список перед выбором.':'Offers are stale; refresh before choosing.','Проверено':'Checked',
  'Подключено к ':'Connected to ','Ключ хранится в macOS Keychain.':'The key is stored in macOS Keychain.',
  'Потребуется только SSH-ключ ~/.ssh/id_ed25519 («Первый запуск»). Веса скачиваются с Hugging Face без токена.':'Only the SSH key ~/.ssh/id_ed25519 is needed (first run). Weights download from Hugging Face without a token.',
  'Ключ будет сохранён только в macOS Keychain.':'The key is stored only in macOS Keychain.',
  'Сначала дождись завершения заданий Studio.':'Wait for Studio jobs to finish first.',
  'Сервер удалён в Vast.':'Server deleted in Vast.',
};
const t = text => document.documentElement.lang === 'en' ? (EN[text] || text) : text;
function tunnelDeploymentText() {
  const prerequisites = t(state.deployMessage || 'Потребуется только SSH-ключ ~/.ssh/id_ed25519 («Первый запуск»). Веса скачиваются с Hugging Face без токена.');
  const consequence = t('Подготовка занимает время и оплачивается Vast. Текущий туннель переключится только после успешной проверки.');
  return state.deployAvailable ? `${prerequisites} ${consequence}` : prerequisites;
}
const money = formatUsd;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const api = async (path, options = {}) => {
  const response = await fetch(`/api/compute${path}`, { ...options, headers: { ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers } });
  let data = {};
  try { data = await response.json(); } catch { /* Safe generic response below. */ }
  if (!response.ok) throw new Error(data?.detail?.message || data?.detail || 'Не удалось выполнить запрос.');
  return data;
};

function message(text = '', kind = '') {
  const node = $('#computeMessage');
  node.textContent = t(text);
  node.dataset.kind = kind;
}

function currentServer(health, endpoint) {
  const connected = Boolean(health?.gpu_reachable);
  $('#computeCurrentState').innerHTML = `<i class="${connected ? 'is-online' : ''}"></i> ${t(connected ? 'Подключён · GPU доступен' : 'GPU сейчас недоступен')}`;
  const remote = endpoint?.configured ? `${endpoint.host}:${endpoint.port}` : t('адрес туннеля не найден');
  $('#computeCurrentAddress').textContent = `${t('Туннель Studio')} → ${remote} · GPU API 127.0.0.1:18189 ${t(connected ? 'отвечает' : 'не отвечает')}`;
}

async function refreshStatus() {
  const [status, health] = await Promise.all([
    api('/status').catch(() => ({connected:false})),
    fetch('/api/health').then(response => response.ok ? response.json() : null).catch(() => null),
  ]);
  state.health = health;
  state.endpoint = status.current_server;
  state.connected = status.connected;
  state.deployAvailable = Boolean(status.deployment?.available);
  state.deployMessage = status.deployment?.message || '';
  $('#computeBalance').textContent = status.connected ? money(status.balance) : '—';
  $('#computeAccount').textContent = status.connected ? status.email : 'Подключи API key, чтобы увидеть баланс';
  $('#computeKeyStatus').textContent = status.connected ? `${t('Подключено')} к ${status.email}. ${t('Ключ хранится в macOS Keychain.')}` : t('Ключ будет сохранён только в macOS Keychain.');
  $('#computeKeyRemove').hidden = !status.connected;
  $('#computeKeyForm').hidden = status.connected;
  currentServer(health, status.current_server);
}

function renderInstances() {
  const target = $('#computeInstanceList');
  if (!state.instances.length) {
    target.innerHTML = '<p class="compute-empty">В аккаунте пока нет GPU-серверов.</p>';
    return;
  }
  target.innerHTML = state.instances.map(item => {
    const id = esc(item.id);
    const controls = instanceControls(item);
    const {active, stopped, currentTunnelTarget: tunnelTarget} = controls;
    const host = item.ssh_host ? `${item.ssh_host}${item.ssh_port ? `:${item.ssh_port}` : ''}` : t('SSH endpoint появится после запуска');
    return `<details class="compute-instance" data-instance="${id}">
      <summary><span class="compute-server-icon">▦</span><span class="compute-server-main"><strong>${esc(item.gpu_name || 'GPU instance')} · #${id}</strong><small>${esc(item.actual_status || t('статус неизвестен'))} · ${money(item.total_usd_per_hour)}/${t('ч')}</small></span><span class="compute-instance-chevron">⌄</span></summary>
      <div class="compute-instance-detail"><dl><div><dt>${t('Подключение')}</dt><dd>${esc(host)}</dd></div><div><dt>GPU</dt><dd>${esc(item.gpu_name || '—')} · ${item.gpu_ram_mb ? `${(item.gpu_ram_mb / 1000).toFixed(0)} GB VRAM` : 'VRAM —'}</dd></div><div><dt>${t('Системная RAM')}</dt><dd>${item.cpu_ram_mb ? `${(item.cpu_ram_mb / 1000).toFixed(0)} GB` : '—'}</dd></div><div><dt>${t('Диск')}</dt><dd>${item.disk_space_gb ? `${item.disk_space_gb} GB` : '—'}</dd></div><div><dt>${t('Состояние H3')}</dt><dd>${esc(item.deployment_state === 'ready' ? t('H3 готов') : t('H3 ещё не развёрнут/не подтверждён'))}</dd></div></dl>
      <p class="compute-instance-note">${t('Адрес Vast — это адрес SSH-подключения к арендованной машине. Studio обращается к GPU через локальный туннель 127.0.0.1:18189.')}</p>
      <div class="compute-deploy"><div><strong>${esc(t(item.deployment_state === 'ready' ? 'H3 готов · Studio подключена' : item.deployment_state === 'installing' ? 'Установка H3 выполняется' : item.deployment_state === 'needs_reconciliation' ? 'GPU не подтвердил готовность после установки' : item.deployment_state === 'failed' ? 'Установка не завершена' : 'Развернуть H3 на этом сервере'))}</strong><p>${tunnelTarget ? t('Этот сервер указан в текущем туннеле Studio. Сначала переключи туннель на другой сервер через установку H3.') : tunnelDeploymentText()}</p></div><button type="button" data-deploy="${id}" class="compute-primary" ${!state.deployAvailable || !controls.canDeploy ? 'disabled' : ''}>${t(['failed', 'needs_reconciliation'].includes(item.deployment_state) ? 'Повторить установку' : 'Установить H3')}</button><div class="compute-deploy-progress" id="computeDeploy-${id}" ${item.deployment_operation_id ? '' : 'hidden'}><span></span><pre></pre></div></div>
      ${tunnelTarget ? `<p class="compute-current-warning">${t('Этот сервер используется текущим туннелем Studio. Остановка отключит GPU. Удалить его можно после остановки; для перехода сначала разверни другой сервер.')}</p>` : ''}<div class="compute-instance-actions">${stopped ? `<button type="button" data-start="${id}" class="compute-primary">${t('Запустить сервер')}</button>` : ''}<button type="button" data-stop="${id}" class="compute-secondary" ${controls.canStop ? '' : 'disabled'}>${t('Остановить сервер')}</button><button type="button" data-destroy="${id}" class="compute-danger" ${controls.canDestroy ? '' : `disabled title="${t('Установка H3 ещё не завершена или требует сверки.') }"`}>${t('Удалить сервер…')}</button></div></div></details>`;
  }).join('');
  for (const item of state.instances) if (item.deployment_operation_id && ['running','starting','needs_reconciliation'].includes(item.deployment_state))
    watchDeployment(item.deployment_operation_id, item.id);
}

async function watchDeployment(operationId, instanceId) {
  if (watchingDeployments.has(operationId)) return;
  watchingDeployments.add(operationId);
  const poll = async () => {
    let keepWatching = false;
    try {
      const op = await api(`/operations/${operationId}`);
      const box = $(`#computeDeploy-${instanceId}`);
      if (box) {
        box.hidden = false;
        const status = box.querySelector('span');
        const log = box.querySelector('pre');
        status.textContent = op.state === 'running' || op.state === 'starting' ? 'Установка выполняется…' : op.state === 'needs_reconciliation' ? 'Установка закончилась, но GPU не подтвердил готовность. Можно повторить установку или остановить сервер.' : op.state === 'ready' ? 'H3 проверен, GPU отвечает через Studio.' : 'Установка завершилась ошибкой. Проверь журнал перед повтором.';
        log.textContent = op.log_tail || '';
        log.scrollTop = log.scrollHeight;
      }
      keepWatching = ['running','starting'].includes(op.state);
      if (!keepWatching) await refreshInstances();
    } catch { keepWatching = true; }
    if (keepWatching) setTimeout(poll, 2500);
    else watchingDeployments.delete(operationId);
  };
  await poll();
}

function renderOffers() {
  const target = $('#computeOfferList');
  const checked = state.offersCheckedAt ? new Date(state.offersCheckedAt) : null;
  if (checked && !Number.isNaN(checked.valueOf())) {
    const locale = document.documentElement.lang === 'en' ? 'en' : 'ru';
    const stale = Date.now() - checked.valueOf() > 120_000;
    $('#computeOfferFreshness').textContent = `${stale ? t('Предложения устарели, обнови список перед выбором.') : t('Проверено')}: ${checked.toLocaleTimeString(locale, {hour:'2-digit',minute:'2-digit'})}`;
    $('#computeOfferFreshness').dataset.stale = String(stale);
  }
  if (!state.offers.length) {
    target.innerHTML = '<p class="compute-empty">Совместимых предложений пока нет. Убери ограничение цены или попробуй позже.</p>';
    return;
  }
  target.innerHTML = state.offers.map(item => `<article class="compute-offer"><div class="compute-offer-title"><div><strong>${esc(item.gpu_name || 'NVIDIA GPU')}</strong><small>${esc(item.location || 'Местоположение не указано')} · надёжность ${item.reliability == null ? '—' : `${(item.reliability * 100).toFixed(1)}%`}</small></div><b>${money(item.total_usd_per_hour)}<small>/ч</small></b></div><div class="compute-offer-specs"><span>${item.num_gpus} × GPU</span><span>${(item.gpu_vram_mb / 1000).toFixed(0)} GB VRAM</span><span>${(item.cpu_ram_mb / 1000).toFixed(0)} GB RAM</span><span>${item.disk_space_gb} GB диск</span><span>CUDA ${item.cuda_max_good}</span></div><button type="button" class="compute-primary" data-rent="${esc(item.id)}" data-rate="${esc(item.total_usd_per_hour)}">Выбрать сервер</button></article>`).join('');
}

async function refreshInstances() {
  if (!state.connected) {
    $('#computeInstanceList').innerHTML = '<p class="compute-empty">Сначала подключи Vast, затем обнови список.</p>';
    return;
  }
  $('#computeInstanceList').innerHTML = '<p class="compute-empty">Загружаю серверы Vast…</p>';
  try { state.instances = (await api('/instances')).instances || []; renderInstances(); }
  catch (error) { $('#computeInstanceList').innerHTML = `<p class="compute-error">${esc(error.message)} <button type="button" id="computeRetryInstances" class="compute-text-button">Повторить</button></p>`; }
}

$('#computeKeyForm').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#computeApiKey');
  const key = input.value;
  input.value = '';
  if (!key.trim()) { message('Вставь API key Vast.', 'error'); return; }
  message('Проверяю ключ и соединение с Vast…');
  try {
    await api('/provider-key', {method:'POST', body:JSON.stringify({api_key:key})});
    message('Vast подключён. Баланс и серверы загружены.', 'success');
    await refreshStatus(); await refreshInstances();
  } catch (error) { message(error.message, 'error'); }
});

$('#computeKeyRemove').addEventListener('click', async () => {
  if (!confirm('Отключить Vast и удалить API key из macOS Keychain? Арендованные серверы останутся в аккаунте Vast.')) return;
  try { await api('/provider-key', {method:'DELETE'}); state.instances = []; state.offers = []; renderInstances(); renderOffers(); await refreshStatus(); message('Vast отключён. Арендованные серверы не изменены.', 'success'); }
  catch (error) { message(error.message, 'error'); }
});

$('#computeOfferFilters').addEventListener('submit', async event => {
  event.preventDefault();
  const max = $('#computeMaxPrice').value;
  const query = max ? `?max_hourly_rate=${encodeURIComponent(max)}` : '';
  $('#computeOfferList').innerHTML = '<p class="compute-empty">Ищу доступные конфигурации…</p>';
  try { const result = await api(`/offers${query}`); state.offers = result.offers || []; state.offersCheckedAt = result.checked_at; renderOffers(); }
  catch (error) { $('#computeOfferList').innerHTML = `<p class="compute-error">${esc(error.message)} <button type="button" id="computeRetryOffers" class="compute-text-button">Повторить</button></p>`; }
});

document.addEventListener('click', async event => {
  const retryInstances = event.target.closest('#computeRetryInstances');
  const retryOffers = event.target.closest('#computeRetryOffers');
  const rent = event.target.closest('[data-rent]');
  const stop = event.target.closest('[data-stop]');
  const destroy = event.target.closest('[data-destroy]');
  const deploy = event.target.closest('[data-deploy]');
  const start = event.target.closest('[data-start]');
  if (retryInstances) await refreshInstances();
  if (retryOffers) $('#computeOfferFilters').requestSubmit();
  if (rent) {
    const id = rent.dataset.rent;
    const offer = state.offers.find(item => String(item.id) === id);
    const rate = Number(rent.dataset.rate);
    if (!offer || !confirm(`Арендовать ${offer.gpu_name} за ${money(rate)} в час? Дополнительно возможна оплата диска и передачи данных. Сервер будет создан в аккаунте Vast.`)) return;
    rent.disabled = true;
    try { const result = await api('/rent', {method:'POST', body:JSON.stringify({offer_id:Number(id), max_hourly_rate:rate})}); message(`Сервер #${result.instance_id} создан. Обновляю состояние…`, 'success'); await refreshStatus(); await refreshInstances(); }
    catch (error) { message(error.message, 'error'); rent.disabled = false; }
  }
  if (stop) {
    const id = stop.dataset.stop;
    const tunnelTarget = state.instances.find(item => String(item.id) === id)?.is_current_tunnel_target;
    const message = tunnelTarget ? `Остановить текущий сервер Studio #${id}? GPU станет недоступен и очередь будет ждать сервер. Данные сохранятся, но диск продолжит тарифицироваться. Доступность той же GPU при повторном запуске не гарантируется.` : `Остановить сервер #${id}? GPU освободится, данные сохранятся, но диск продолжит тарифицироваться. Доступность этой GPU при повторном запуске не гарантируется.`;
    if (!confirm(message)) return;
    stop.disabled = true;
    try { await api(`/instances/${id}/stop`, {method:'POST'}); message(`Запрошена остановка сервера #${id}.`, 'success'); await refreshInstances(); }
    catch (error) { message(error.message, 'error'); stop.disabled = false; }
  }
  if (destroy) {
    const id = destroy.dataset.destroy;
    const typed = prompt(`Удаление безвозвратно сотрёт данные на сервере #${id}. Файлы и очередь Studio на Mac сохранятся. Для подтверждения введи ID сервера:`);
    if (typed !== id) { if (typed !== null) message('ID не совпал; сервер не удалён.', 'error'); return; }
    destroy.disabled = true;
    try { await api(`/instances/${id}`, {method:'DELETE', body:JSON.stringify({confirmation_id:Number(typed)})}); message(`Сервер #${id} удалён в Vast.`, 'success'); await refreshInstances(); await refreshStatus(); }
    catch (error) { message(error.message, 'error'); destroy.disabled = false; }
  }
  if (deploy) {
    const id = deploy.dataset.deploy;
    if (!confirm(`Запустить установку H3 на сервер #${id}? Сценарий восстановит программное окружение и веса, проверит GPU и при успехе переведёт туннель Studio на эту машину. Операция займёт время; оплата Vast продолжается. Генерация не запускается.`)) return;
    deploy.disabled = true;
    try {
      const op = await api(`/instances/${id}/deploy`, {method:'POST'});
      message(`Установка H3 началась для сервера #${id}.`, 'success');
      await refreshInstances();
      watchDeployment(op.operation_id, Number(id));
    } catch (error) { message(error.message, 'error'); deploy.disabled = false; }
  }
  if (start) {
    const id = start.dataset.start;
    if (!confirm(`Запустить остановленный сервер #${id}? Тарификация GPU продолжится по цене Vast.`)) return;
    start.disabled = true;
    try { await api(`/instances/${id}/start`, {method:'POST'}); message(`Отправлен запрос запуска сервера #${id}.`, 'success'); await refreshInstances(); }
    catch (error) { message(error.message, 'error'); start.disabled = false; }
  }
});

$('#computeRefresh').addEventListener('click', async () => { await refreshStatus(); await refreshInstances(); });
$('#computeInstancesRefresh').addEventListener('click', refreshInstances);
addEventListener('studio:localechange', () => {
  refreshStatus().then(() => { renderInstances(); renderOffers(); });
});
if (computeMode) { refreshStatus().then(refreshInstances).catch(error => message(error.message, 'error')); }
