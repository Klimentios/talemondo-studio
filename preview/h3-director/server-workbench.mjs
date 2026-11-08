import { parseSshTarget } from './ssh-target.mjs?v=20261105-1';

const root = document.querySelector('#serverWorkbench');
if (root && new URLSearchParams(location.search).get('view') === 'compute') {
  const $ = selector => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const state = { operationId: null, socket: null, terminal: null, fit: null, plan: null, poll: null };
  const status = (message, kind = '') => { const node = $('#serverWorkbenchState'); node.textContent = message; node.dataset.kind = kind; };
  const target = () => ({ host: $('#serverHost').value.trim(), port: Number($('#serverPort').value) });
  const api = async (path, options = {}) => {
    const response = await fetch(`/api/compute${path}`, { ...options, headers: { ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'Операция не выполнена');
    return data;
  };
  const tab = name => {
    for (const button of root.querySelectorAll('[data-server-tab]')) button.setAttribute('aria-current', button.dataset.serverTab === name ? 'page' : 'false');
    for (const [key, panel] of [['plan','#serverPlanPanel'],['log','#serverLogPanel'],['terminal','#serverTerminalPanel']]) $(panel).hidden = key !== name;
    if (name === 'terminal' && state.fit) state.fit.fit();
  };
  root.querySelectorAll('[data-server-tab]').forEach(button => button.addEventListener('click', () => tab(button.dataset.serverTab)));

  function showPlan(plan) {
    const remote = plan.remote || {};
    const facts = [
      ['SSH', plan.ssh ? 'Доступен' : 'Недоступен'], ['GPU', remote.gpu || 'Не определён'],
      ['CUDA', remote.cuda || 'Не определена'], ['Диск свободен', remote.disk_available ? `${(Number(remote.disk_available) / 1e9).toFixed(0)} ГБ` : 'Не определён'],
      ['H3 на сервере', remote.h3_dir === 'populated' ? `Файлы есть · ${remote.image_stamp || 'нет stamp'}` : remote.h3_dir || 'Неизвестно'],
      ['ComfyUI', remote.comfy === 'unavailable' || !remote.comfy ? 'Не отвечает' : `Очередь ${remote.comfy}`],
      ['Установка', plan.r2_snapshot_complete ? `из своего снимка R2 ${plan.r2_latest}` : 'с нуля: открытые веса с Hugging Face (≈1 ч)'],
    ];
    $('#serverPlan').className = 'server-plan';
    $('#serverPlan').innerHTML = facts.map(([name,value]) => `<article><strong>${esc(name)}</strong><span>${esc(value)}</span></article>`).join('') +
      `<article class="server-plan-full"><strong>План развёртывания</strong><ol>${(plan.plan || []).map(step => `<li>${esc(step)}</li>`).join('')}</ol></article>` +
      (plan.problems?.length ? `<article class="server-plan-full"><strong>Требует внимания</strong><ul>${plan.problems.map(p => `<li class="server-problem">${esc(p)}</li>`).join('')}</ul></article>` : '');
    status(plan.ssh ? 'План готов' : 'Сервер недоступен', plan.ssh ? 'good' : 'bad');
    tab('plan');
  }

  async function refreshPlan() {
    const selected = target();
    state.plan = null;
    if (!selected.host || !Number.isInteger(selected.port) || selected.port < 1 || selected.port > 65535) {
      status('Укажи SSH host и порт 22/tcp', 'bad'); return;
    }
    status('Проверяю сервер: SSH, GPU, диск…');
    try { state.plan = await api('/operator/plan', {method:'POST', body:JSON.stringify(selected)}); showPlan(state.plan); }
    catch (error) { status(error.message, 'bad'); }
  }
  $('#serverTargetForm').addEventListener('submit', event => { event.preventDefault(); refreshPlan(); });
  // One paste of the provider's SSH command fills both fields and checks the server.
  const fillFromCommand = () => {
    const raw = $('#serverHost').value;
    if (!/[\s:@]/.test(raw.trim())) return;
    const parsed = parseSshTarget(raw);
    if (!parsed) return;
    $('#serverHost').value = parsed.host; $('#serverPort').value = parsed.port;
    refreshPlan();
  };
  $('#serverHost').addEventListener('paste', () => setTimeout(fillFromCommand, 0));
  $('#serverHost').addEventListener('change', fillFromCommand);
  $('#serverHost').placeholder = 'IP или вставь SSH-команду Vast целиком';

  async function refreshHistory() {
    try {
      const {operations} = await api('/operator/operations');
      const list = $('#serverHistory');
      list.innerHTML = operations?.length ? operations.map(op => `<button type="button" data-operation="${esc(op.id)}" aria-current="${op.id === state.operationId}"><strong>${esc(op.kind)} · ${esc(op.state)}</strong><small>${esc(op.label || op.instance_id || '')} · ${esc((op.created_at || '').replace('T',' ').slice(0,19))}</small></button>`).join('') : 'Операций пока нет.';
    } catch { $('#serverHistory').textContent = 'Журнал сейчас недоступен.'; }
  }
  $('#serverHistoryRefresh').addEventListener('click', refreshHistory);
  $('#serverHistory').addEventListener('click', event => {
    const id = event.target.closest('[data-operation]')?.dataset.operation;
    if (id) { state.operationId = id; tab('log'); watchOperation(id); refreshHistory(); }
  });

  async function watchOperation(id) {
    if (state.poll) clearTimeout(state.poll);
    try {
      const op = await api(`/operations/${encodeURIComponent(id)}`);
      if (id !== state.operationId) return;
      $('#serverLogTitle').textContent = `${op.kind} · ${op.state} · ${op.label || op.instance_id || ''}`;
      const log = $('#serverLog');
      const wasAtBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      log.textContent = op.log_tail || 'Жду вывод скрипта…';
      if (wasAtBottom) log.scrollTop = log.scrollHeight;
      status(op.state === 'ready' ? 'Операция завершена' : op.state === 'failed' ? 'Операция завершилась ошибкой' : op.state === 'needs_reconciliation' ? 'Нужна сверка результата' : 'Скрипт выполняется…', op.state === 'ready' ? 'good' : op.state === 'failed' ? 'bad' : '');
      if (['running','starting'].includes(op.state)) state.poll = setTimeout(() => watchOperation(id), 2500);
      else refreshHistory();
    } catch (error) { status(error.message, 'bad'); }
  }
  $('#serverLogCopy').addEventListener('click', () => navigator.clipboard.writeText($('#serverLog').textContent));

  root.querySelectorAll('[data-server-action]').forEach(button => button.addEventListener('click', async () => {
    const selected = target(), action = button.dataset.serverAction;
    if (!selected.host || !Number.isInteger(selected.port) || selected.port < 1 || selected.port > 65535) {
      status('Сначала укажи SSH host и порт', 'bad'); return;
    }
    if (['restore','overlay'].includes(action)) {
      await refreshPlan();
      if (!state.plan?.ssh) return;
      const detail = action === 'restore' ? (state.plan.r2_snapshot_complete ? `Восстановить H3 из R2 ${state.plan.r2_latest} на ${selected.host}:${selected.port}? После проверки Studio переключится на этот сервер.` : `Установить H3 с нуля на ${selected.host}:${selected.port}? Около часа: ComfyUI, открытые веса MiniMax H3 (~150 ГБ), узлы Studio. После проверки Studio переключится на этот сервер.`) : `Обновить код и сервисы H3 на ${selected.host}:${selected.port}? Активные задания должны быть завершены.`;
      if (!confirm(detail)) return;
    }
    button.disabled = true;
    try {
      const result = await api('/operator/run', {method:'POST', body:JSON.stringify({...selected, action})});
      state.operationId = result.operation_id;
      tab('log'); await refreshHistory(); await watchOperation(result.operation_id);
    } catch (error) { status(error.message, 'bad'); }
    finally { button.disabled = false; }
  }));

  async function terminalConnect() {
    const selected = target();
    if (!selected.host || !Number.isInteger(selected.port) || selected.port < 1 || selected.port > 65535) { status('Укажи SSH host и порт', 'bad'); return; }
    if (state.socket) state.socket.close();
    if (!state.terminal) {
      const [{Terminal}, {FitAddon}] = await Promise.all([
        import('./vendor/xterm/xterm.mjs?v=6.0.0'), import('./vendor/xterm/addon-fit.mjs?v=0.11.0')]);
      state.terminal = new Terminal({cursorBlink:true, fontFamily:'ui-monospace, Menlo, monospace', fontSize:12,
        theme:{background:getComputedStyle($('#serverTerminal')).backgroundColor, foreground:getComputedStyle(root).color}, scrollback:3000});
      state.fit = new FitAddon(); state.terminal.loadAddon(state.fit); state.terminal.open($('#serverTerminal'));
      new ResizeObserver(() => { if (state.fit) { state.fit.fit(); sendSize(); } }).observe($('#serverTerminal'));
      state.terminal.onData(data => { if (state.socket?.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify({type:'input',data})); });
    }
    state.fit.fit(); state.terminal.writeln(`\r\nПодключение к ${selected.host}:${selected.port}…`);
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${scheme}//${location.host}/api/compute/operator/terminal?host=${encodeURIComponent(selected.host)}&port=${selected.port}`;
    const socket = new WebSocket(url); state.socket = socket;
    socket.onopen = () => { $('#serverTerminalConnect').disabled = true; $('#serverTerminalDisconnect').disabled = false; sendSize(); status('SSH подключён', 'good'); };
    socket.onmessage = event => state.terminal.write(event.data);
    socket.onclose = () => { if (state.socket === socket) { state.socket = null; status('SSH закрыт', 'bad'); } $('#serverTerminalConnect').disabled = false; $('#serverTerminalDisconnect').disabled = true; state.terminal.writeln('\r\n[SSH закрыт]'); };
    socket.onerror = () => status('SSH не подключился', 'bad');
  }
  function sendSize() {
    if (state.socket?.readyState === WebSocket.OPEN && state.terminal)
      state.socket.send(JSON.stringify({type:'resize',cols:state.terminal.cols,rows:state.terminal.rows}));
  }
  $('#serverTerminalConnect').addEventListener('click', terminalConnect);
  $('#serverTerminalDisconnect').addEventListener('click', () => state.socket?.close());
  $('#serverSshCopy').addEventListener('click', () => {
    const selected = target();
    if (selected.host && Number.isInteger(selected.port) && selected.port > 0 && selected.port <= 65535)
      navigator.clipboard.writeText(`ssh -i ~/.ssh/id_ed25519 -p ${selected.port} root@${selected.host}`);
  });
  addEventListener('beforeunload', () => state.socket?.close());

  api('/status').then(statusData => {
    if (statusData.current_server?.configured) {
      $('#serverHost').value = statusData.current_server.host;
      $('#serverPort').value = statusData.current_server.port;
      refreshPlan();
    } else status('Укажи сервер');
  }).catch(() => status('Укажи сервер'));
  refreshHistory();
}
