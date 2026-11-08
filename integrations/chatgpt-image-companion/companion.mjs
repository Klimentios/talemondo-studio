// MCP Apps host bridge. No DOM automation, private ChatGPT endpoints or model API keys.
export function workerPrompt(row) {
  if (!/^[a-f0-9]{32}$/.test(row.id)) throw new Error('Некорректный ID задания');
  return `[@STUDIO Image Companion](plugin://h3-image-companion@studio-local) Это задание обычному ChatGPT со встроенным ImageGen: ${row.id}. ` +
    'Если ты работаешь как Codex или встроенного ImageGen нет, остановись и объясни это; не запускай CLI или API. ' +
    `Вызови image_request_claim с request_id=${row.id} и уникальным claim_id для этой беседы. ` +
    'Прочитай весь context: сюжет, целевой шот, паспорт франшизы, масштаб, внешность, костюм, стиль и мир фантазии. ' +
    'Посмотри все references через view_image, проверь одобрение сюжета. ' +
    'Создай ОДНУ картинку своим встроенным ImageGen. Верни полученный файл через image_request_complete ' +
    'с request_id, claim_token и image. Если файл нельзя передать, используй image_request_fail; ' +
    'после неоднозначного прерывания генерацию не повторяй. Видео не запускай.';
}

export function createHostBridge({ parentWindow, onNotification = () => {}, timeoutMs = 15000 }) {
  let nextId = 0;
  const pending = new Map();
  function receive(event) {
    if (event.source !== parentWindow || event.data?.jsonrpc !== '2.0') return;
    const message = event.data;
    if (message.method) { onNotification(message); return; }
    const item = pending.get(message.id);
    if (!item) return;
    clearTimeout(item.timer); pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message || 'Ошибка клиента ChatGPT'));
    else item.resolve(message.result);
  }
  function call(method, params) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error('Клиент не подтвердил запрос. Он мог быть выполнен; не отправляй повторно автоматически.'));
      }, timeoutMs);
      pending.set(id, { resolve, reject, timer });
      parentWindow.postMessage({ jsonrpc: '2.0', id, method, params }, '*');
    });
  }
  return { receive, call,
    notify: (method, params = {}) => parentWindow.postMessage({ jsonrpc: '2.0', method, params }, '*') };
}

export function mountCompanion(win, doc) {
  const el = (id) => doc.getElementById(id);
  let row = null, requestId = '', refreshTimer = 0, stopped = false;
  const text = (value) => { el('status').textContent = value; };
  const bridge = createHostBridge({ parentWindow: win.parent, onNotification: (msg) => {
    if (msg.method === 'ui/notifications/host-context-changed') void selectContext(msg.params);
    if (msg.method === 'ui/notifications/resource-teardown') { stopped = true; clearTimeout(refreshTimer); }
  } });
  win.addEventListener('message', bridge.receive);
  const tool = async (name, args = {}) => {
    const result = await bridge.call('tools/call', { name, arguments: args });
    const content = (result.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n');
    if (result.isError) throw new Error(content || 'Ошибка Studio');
    return JSON.parse(content);
  };
  async function refresh() {
    if (stopped) return;
    clearTimeout(refreshTimer);
    try {
      if (!requestId) {
        const rows = await tool('image_requests');
        const waiting = rows.filter((r) => r.state === 'waiting_chatgpt');
        el('requests').replaceChildren(...waiting.map((r) => {
          const option = doc.createElement('option'); option.value = r.id;
          option.textContent = `${r.purpose === 'fantasy_world' ? 'Мир фантазии' : 'Кадр'} · ${r.id.slice(0, 8)}`;
          return option;
        }));
        requestId = waiting[0]?.id || '';
      }
      row = requestId ? await tool('image_request_status', { request_id: requestId }) : null;
      el('request').textContent = row ? `Задание ${row.id} · ${row.purpose}` : 'В очереди нет ожидающих заданий';
      el('send').disabled = row?.state !== 'waiting_chatgpt';
      el('draft').disabled = el('send').disabled;
      el('active').disabled = el('send').disabled;
      text(row ? `Статус Studio: ${row.state}` : 'Создай запрос кнопкой «Картинка · ChatGPT» в Studio.');
    } catch (error) { text(error.message); el('send').disabled = true; el('draft').disabled = true; el('active').disabled = true; }
    if (!stopped) refreshTimer = setTimeout(refresh, 5000);
  }
  async function selectContext(context) {
    const path = context?.['openai/deepLink']?.url;
    if (path) {
      const url = new URL(path, 'https://companion.invalid');
      const id = url.searchParams.get('request_id');
      if (id && /^[a-f0-9]{32}$/.test(id)) requestId = id;
    }
    await refresh();
  }
  async function send(sendImmediately, target = 'new') {
    if (!row || row.state !== 'waiting_chatgpt') return;
    const key = `studio-image-message:${row.id}`;
    // Record before dispatch. A timeout/teardown must not cause a duplicate message on reload.
    el('send').disabled = true; el('draft').disabled = true; el('active').disabled = true;
    try {
      if (win.localStorage.getItem(key)) { text('Для этого задания уже была попытка отправки. Проверь беседу и статус Studio.'); return; }
      const current = await tool('image_request_status', { request_id: row.id });
      if (current.state !== 'waiting_chatgpt') throw new Error('Задание уже взято или устарело');
      win.localStorage.setItem(key, JSON.stringify({ at: Date.now(), send: sendImmediately, target }));
      await bridge.call('ui/message', { role: 'user', content: [{ type: 'text', text: workerPrompt(current) }],
        _meta: { 'openai/message': { target, send: sendImmediately } } });
      text(sendImmediately ? 'Клиент подтвердил отправку сообщения в новую беседу. Ожидаем claim и файл в Studio.' :
        target === 'active' ? 'Задание добавлено в черновик текущего чата. Проверь режим ChatGPT и отправь сообщение.' :
        'Клиент подтвердил создание черновика беседы. Выбери обычный ChatGPT и отправь сообщение.');
    } catch (error) { text(error.message); }
  }
  el('send').addEventListener('click', () => void send(true));
  el('draft').addEventListener('click', () => void send(false));
  el('active').addEventListener('click', () => void send(false, 'active'));
  el('requests').addEventListener('change', () => { requestId = el('requests').value; void refresh(); });
  el('refresh').addEventListener('click', () => { requestId = ''; void refresh(); });
  void bridge.call('ui/initialize', { appInfo: { name: 'STUDIO Image Companion', version: '0.1.0' },
    appCapabilities: {}, protocolVersion: '2026-01-26' }).then((result) => {
    bridge.notify('ui/notifications/initialized'); return selectContext(result.hostContext);
  }).catch((error) => text(`Панель должна быть открыта внутри ChatGPT с поддержкой MCP Apps. ${error.message}`));
  return () => { stopped = true; clearTimeout(refreshTimer); win.removeEventListener('message', bridge.receive); };
}
