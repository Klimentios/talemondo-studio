import { subscriptionTurn } from './subscription-chat.mjs?v=20261102-bridge-2';
import { renderChatHome, syncHomeMode } from './chat-home.mjs?v=20261107-fixed-rail-1';
// «Чат с агентом»: Codex or Claude Code on the user's subscription, working with the Studio through its tools.
import { MODES, TOOL_TEXT, applyEvent, providerNote, renderMarkdown, splitLines } from './agent-chat-model.mjs?v=20261028-2';
import { mountTaskPanel } from './task-panel.mjs?v=20261103-no-qwen-1';
import { statusOf } from './tasks-model.mjs?v=20261028-1';
import { resolveFranchise, suggestedDeliverable } from './chat-production-model.mjs?v=20261103-no-qwen-1';
import { attachmentsView, installAttachments } from './agent-attachments.mjs?v=20261101-chat-production-1';

const API = '/api/agent-chat';
const COMPUTE_MODE = new URLSearchParams(location.search).get('compute') === '1';
const $ = (selector) => document.querySelector(selector);
const state = { providers: {}, chats: [], runs: [], chat: null, live: null, controller: null, provider: 'codex', franchises: [] };
let taskPanel = null;
let startingTask = false;
let deliverableChosen = false;
function suggestDeliverable() { if (!deliverableChosen) $('#acDeliverable').value = suggestedDeliverable($('#acInput').value); }
function resizeComposer() { const input = $('#acInput'); input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; }

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (['className', 'value', 'checked', 'disabled', 'selected', 'hidden', 'innerHTML'].includes(key)) node[key] = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false && child !== ''));
  return node;
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
  return payload;
}
const send = (url, method, body) => json(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function say(text) { $('#acStatus').textContent = text || ''; }
let photos;  // installAttachments on the composer (start of the page)

// --- header: provider, model, mode ----------------------------------------------------------

function renderHeader() {
  const chat = state.chat;
  const provider = chat?.provider || state.provider;
  const info = state.providers[provider];
  $('#acProvider').replaceChildren(...['codex', 'claude'].map((name) => el('button', {
    type: 'button', className: 'k-chip', 'aria-pressed': String(provider === name), disabled: Boolean(state.live),
    title: chat?.messages.length && provider !== name ? `Начать новый чат с ${state.providers[name]?.label || name}` : '',
    // A chat keeps its agent: picking the other one in a chat with messages starts a new chat with it.
    onclick: () => {
      if (provider === name || state.live) return;
      state.provider = name;
      if (chat?.messages.length) void newChat().catch((error) => say(error.message));
      else if (chat) void patch({ provider: name }); else renderHeader();
    },
  }, state.providers[name]?.label || name, el('small', {}, ` · ${providerNote(state.providers[name])}`))));
  const models = info?.models || [];
  const current = chat?.model ?? '';
  const defaultLabel = info?.default ? `По умолчанию (${info.default})` : 'По умолчанию';
  $('#acModel').replaceChildren(el('option', { value: '' }, defaultLabel),
    ...models.map(([value, label]) => el('option', { value, selected: value === current }, label)));
  $('#acModel').value = current;
  const efforts = info?.efforts || [];
  $('#acEffort').replaceChildren(el('option', { value: '' }, `По умолчанию${info?.default_effort ? ` (${info.default_effort})` : ''}`),
    ...efforts.map(([value, label]) => el('option', { value, selected: value === (chat?.effort || '') }, label)));
  $('#acEffort').value = efforts.some(([value]) => value === chat?.effort) ? chat.effort : '';
  $('#acMode').replaceChildren(...MODES.map(([value, label]) => el('button', { type: 'button', className: 'k-chip',
    'aria-pressed': String((chat?.mode || 'read') === value), onclick: () => patch({ mode: value }) }, label)));
  $('#acModeHint').textContent = (chat?.mode || 'read') === 'act'
    ? 'Агент может создавать карточки, собирать монтаж и ставить задания (рендер — только по одобренному сюжету).'
    : 'Агент читает библии, ассеты, очередь и проверяет планы, но ничего не меняет.';
  $('#acLogin').hidden = !info || info.logged_in;
  $('#acLogin').textContent = info && !info.logged_in
    ? `${info.label}: ${info.installed ? `нет входа. В терминале выполни: ${provider === 'claude' ? 'claude auth login' : 'codex login'}` : 'CLI не установлен'}` : '';
  renderTaskToggle();
}

async function patch(fields) {
  if (!state.chat) return;
  if (fields.provider) {  // empty chat: provider is chosen by recreating it
    await json(`${API}/chats/${state.chat.id}`, { method: 'DELETE' });
    state.chat = await send(`${API}/chats`, 'POST', { provider: fields.provider, mode: state.chat.mode });
    await loadList();
  } else {
    try { state.chat = await send(`${API}/chats/${state.chat.id}`, 'PATCH', fields); } catch (error) { say(error.message); }
  }
  renderHeader();
}

// --- messages -------------------------------------------------------------------------------

function toolLine(event) {
  return el('details', { className: `ac-tool is-${event.status || 'started'}` },
    el('summary', {}, el('span', { className: 'ac-tool-dot' }), event.name || 'инструмент',
      el('small', {}, ` · ${TOOL_TEXT[event.status] || event.status || ''}`)),
    event.input ? el('pre', {}, event.input) : null,
    event.output ? el('pre', {}, event.output) : null);
}

function messageNode(message, live = false) {
  if (message.role === 'user') return el('article', { className: 'ac-msg ac-user' }, el('div', { className: 'ac-bubble' }, message.text, attachmentsView(message.attachments, 'ac')));
  const meta = [message.provider === 'claude' ? 'Claude' : message.provider === 'codex' ? 'Codex' : message.provider === 'chatgpt' ? 'ChatGPT · подписка' : '',
    message.model, message.seconds != null ? `${message.seconds} с` : ''].filter(Boolean).join(' · ');
  return el('article', { className: `ac-msg ac-agent${live ? ' is-live' : ''}` },
    message.events?.length ? el('div', { className: 'ac-tools' }, ...message.events.map(toolLine)) : null,
    message.text ? el('div', { className: 'ac-text', innerHTML: renderMarkdown(message.text) }) : null,
    attachmentsView(message.attachments, 'ac'),
    live && !message.text && !message.error ? el('div', { className: 'ac-thinking' }, 'Агент думает…') : null,
    message.error ? el('p', { className: 'ac-error' }, message.error) : null,
    meta ? el('small', { className: 'ac-meta' }, meta) : null);
}

function renderMessages(scroll = true) {
  const box = $('#acMessages');
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  const messages = state.chat?.messages || [];
  syncHomeMode(!messages.length && !state.live);
  box.replaceChildren(...messages.map((message) => messageNode(message)),
    ...(state.live ? [messageNode(state.live, true)] : []));
  if (!messages.length && !state.live) {
    renderChatHome(box);
  }
  if (!messages.length && !state.live) box.scrollTop = 0;
  else if (scroll && (near || state.live)) box.scrollTop = box.scrollHeight;
  const busy = Boolean(state.live);
  $('#acSend').hidden = busy;
  $('#acStop').hidden = !busy;
}

// One list for chats and tasks (a task is a chat the agent runs to the end itself); helpers live inside their task.
function renderList() {
  const runs = new Map(state.runs.map((run) => [run.chat_id, run]));
  $('#acChats').replaceChildren(...state.chats.filter((chat) => !chat.parent_run).map((chat) => {
    const run = runs.get(chat.id);
    const [label, tone] = run ? statusOf(run) : ['', ''];
    return el('button', { type: 'button', className: `ac-chat${chat.id === state.chat?.id ? ' is-current' : ''}`, onclick: () => open(chat.id) },
      el('strong', {}, chat.title || 'Без названия'),
      el('small', {}, run ? el('span', { className: `tk-dot ${tone}` }) : null,
        run ? `Задача · ${run.asking ? 'ждёт ответа' : label.toLowerCase()} · ${run.done}/${run.steps}`
          : `${chat.provider === 'claude' ? 'Claude' : 'Codex'}${chat.model ? ` · ${chat.model}` : ''} · ${chat.mode === 'act' ? 'действует' : 'смотрит'}${chat.running ? ' · отвечает…' : ''}`));
  }));
}

async function loadList() {
  [state.chats, state.runs] = await Promise.all([json(`${API}/chats`), json('/api/runs').catch(() => [])]);
  renderList();
}

function renderTaskToggle() {
  const fresh = !state.chat || !state.chat.messages.length;
  $('#acTaskOptions').hidden = !fresh && !state.chat?.run_id;
  $('#acTaskRow').hidden = $('#acTaskOptions').hidden;
  $('#acTaskToggle').disabled = !fresh;
  if (!fresh) $('#acTaskToggle').checked = Boolean(state.chat?.run_id);
  const taskMode = $('#acTaskToggle').checked && fresh;
  $('#acMode').hidden = taskMode;
  if (taskMode) $('#acModeHint').textContent = 'Задача запускается с инструментами Studio; сюжет и GPU проходят необходимые согласования.';
  else $('#acModeHint').textContent = (state.chat?.mode || 'read') === 'act'
    ? 'Агент может создавать карточки, собирать монтаж и ставить задания (рендер — только по одобренному сюжету).'
    : 'Агент читает библии, ассеты, очередь и проверяет планы, но ничего не меняет.';
  $('#acFranchise').hidden = !taskMode;
  $('#acDeliverable').hidden = $('#acFranchise').hidden;
  $('#acInput').placeholder = $('#acTaskToggle').checked && fresh
    ? 'Опишите идею, сцену или задачу для агента…'
    : 'Напишите сообщение агенту…';
  const agentMode = $('#acInteraction').value === 'agent';
  $('#acProvider').hidden = !agentMode;
  $('#acMode').hidden = !agentMode || taskMode;
  document.querySelector('.ac-config').hidden = !agentMode;
  $('#acTaskToggle').closest('label').hidden = !agentMode;
  $('#acSubscriptionHint').hidden = agentMode;
  if (!agentMode) {
    $('#acTaskOptions').hidden = false; $('#acTaskRow').hidden = false;
    $('#acFranchise').hidden = false; $('#acDeliverable').hidden = true;
    $('#acModeHint').textContent = 'Подписочный ChatGPT через браузерный коннектор';
    $('#acInput').placeholder = $('#acInteraction').value === 'image' ? 'Опишите картинку…' : 'Напишите сообщение…';
  }

}

async function follow(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    let events;
    [events, buffer] = splitLines(buffer + decoder.decode(value, { stream: true }));
    for (const event of events) {
      if (event.kind === 'ping') continue;
      applyEvent(state.live, event);
    }
    renderMessages();
  }
}

async function finish(chatId) {
  json(`${API}/providers`).then((providers) => { state.providers = providers; renderHeader(); }).catch(() => {});
  state.live = null;
  state.controller = null;
  if (state.chat?.id === chatId) state.chat = await json(`${API}/chats/${chatId}`);
  renderMessages();
  await loadList();
  $('#acInput').focus();
}

async function startTask(text, attachments) {
  const named = resolveFranchise(text, state.franchises);
  if (named && $('#acFranchise').value && named.id !== $('#acFranchise').value) throw new Error('Название франшизы в сообщении не совпадает с выбранной. Исправь выбор или текст.');
  const franchiseId = $('#acFranchise').value || named?.id;
  if (!franchiseId) throw new Error('Выбери франшизу в списке или укажи её полное название в сообщении.');
  const empty = state.chat && !state.chat.messages.length ? state.chat : null;
  const run = await send('/api/runs', 'POST', { goal: text, franchise_id: franchiseId, deliverable_type: $('#acDeliverable').value, provider: state.provider,
    model: state.chat?.provider === state.provider ? state.chat.model || '' : '',
    effort: state.chat?.provider === state.provider ? state.chat.effort || '' : '', attachments: attachments.map((file) => file.asset_id) });
  if (empty) await json(`${API}/chats/${empty.id}`, { method: 'DELETE' }).catch(() => {});
  $('#acInput').value = ''; resizeComposer();
  await loadList();
  await open(run.chat_id);
}

async function submit() {
  const input = $('#acInput');
  if (state.live || startingTask || state.chat?.subscription_pending) return;
  if (photos.busy()) { say('Файл ещё загружается'); return; }
  const attachments = photos.take();
  const text = input.value.trim() || (attachments.length ? 'Посмотри прикреплённые файлы и опиши, как их можно использовать в Studio.' : '');
  if (!text) return;
  say('');
  if ($('#acInteraction').value !== 'agent') {
    startingTask = true; $('#acSend').disabled = true;
    try {
      if (!state.chat) await newChat();
      const requestId = crypto.randomUUID().replaceAll('-', '');
      await subscriptionTurn({ chat: state.chat, text, mode: $('#acInteraction').value, attachments,
        franchiseId: $('#acFranchise').value || resolveFranchise(text, state.franchises)?.id, requestId });
      input.value = ''; resizeComposer();
      state.chat = await json(`${API}/chats/${state.chat.id}`);
      renderMessages(); void loadList();
      say('Запрос передан подписочному чату. Ответ появится здесь.');
    } catch (error) { photos.restore(attachments); say(error.message); }
    finally { startingTask = false; $('#acSend').disabled = false; }
    return;
  }
  say('');
  try {
    if ($('#acTaskToggle').checked && (!state.chat || !state.chat.messages.length)) {
      startingTask = true; $('#acSend').disabled = true;
      try { await startTask(text, attachments); } finally { startingTask = false; $('#acSend').disabled = false; }
      return;
    }
    if (state.chat?.run_id) {  // a task: the message goes through the task, queued if the agent is mid-turn
      await send(`/api/runs/${state.chat.run_id}/continue`, 'POST', { text, attachments: attachments.map((file) => file.asset_id) });
      input.value = ''; resizeComposer();
      await open(state.chat.id);
      return;
    }
  } catch (error) { photos.restore(attachments); say(error.message); return; }
  if (!state.chat) await newChat();
  const chatId = state.chat.id;
  say('');
  state.chat.messages.push({ role: 'user', text, attachments });
  state.live = { role: 'assistant', text: '', events: [], provider: state.chat.provider, model: state.chat.model };
  input.value = ''; resizeComposer();
  renderMessages();
  state.controller = new AbortController();
  try {
    const response = await fetch(`${API}/chats/${chatId}/messages`, { method: 'POST', signal: state.controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, attachments: attachments.map((photo) => photo.asset_id) }) });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    void loadList();
    await follow(response);
  } catch (error) {
    if (error.name !== 'AbortError') { say(error.message); state.chat.messages.pop(); input.value = text; photos.restore(attachments); }
  }
  await finish(chatId);
}

async function stop() {
  if (!state.chat) return;
  await send(`${API}/chats/${state.chat.id}/stop`, 'POST', {}).catch(() => {});
  say('Остановлено');
}

async function open(chatId) {
  if (state.live) { say('Дождись ответа или нажми «Стоп»'); return; }
  state.chat = await json(`${API}/chats/${chatId}`);
  state.provider = state.chat.provider;
  history.replaceState(null, '', `${COMPUTE_MODE ? '?compute=1&' : '?'}chat=${chatId}`);
  renderHeader();
  renderList();
  renderTaskToggle();
  document.querySelector('.ac-shell').classList.toggle('has-task', Boolean(state.chat.run_id));
  await taskPanel.show(state.chat.run_id || null);
  if (state.chat.running) {  // reconnect to a run that outlived the page
    state.live = { role: 'assistant', text: '', events: [], provider: state.chat.provider, model: state.chat.model };
    renderMessages();
    try {
      const response = await fetch(`${API}/chats/${chatId}/stream`);
      if (response.ok) await follow(response);
    } finally { await finish(chatId); }
    return;
  }
  renderMessages();
}

async function newChat() {
  $('#acTaskToggle').checked = !COMPUTE_MODE;
  deliverableChosen = false; suggestDeliverable();
  const mode = COMPUTE_MODE ? 'act' : (state.chat?.mode || 'read');
  state.chat = await send(`${API}/chats`, 'POST', { provider: state.provider, mode,
    model: state.chat?.provider === state.provider ? state.chat.model : '',
    effort: state.chat?.provider === state.provider ? state.chat.effort || '' : '' });
  history.replaceState(null, '', `${COMPUTE_MODE ? '?compute=1&' : '?'}chat=${state.chat.id}`);
  await loadList();
  renderHeader();
  renderTaskToggle();
  document.querySelector('.ac-shell').classList.remove('has-task');
  await taskPanel.show(null);
  renderMessages();
}

async function removeChat() {
  if (!state.chat || state.live || !window.confirm(`Удалить чат «${state.chat.title}»?`)) return;
  await json(`${API}/chats/${state.chat.id}`, { method: 'DELETE' });
  state.chat = null;
  await loadList();
  if (state.chats[0]) await open(state.chats[0].id); else { renderHeader(); renderMessages(); }
}

async function removeAll() {
  if (!state.chats.length || !window.confirm(`Удалить все чаты (${state.chats.length})? Это нельзя отменить.`)) return;
  const { deleted, kept } = await json(`${API}/chats`, { method: 'DELETE' });
  say(kept ? `Удалено ${deleted}; ${kept} оставлено — там агент ещё отвечает.` : `Удалено чатов: ${deleted}.`);
  await loadList();
  if (state.chats.some((chat) => chat.id === state.chat?.id)) return;
  state.chat = null;
  history.replaceState(null, '', location.pathname);
  if (state.chats[0]) await open(state.chats[0].id); else { renderHeader(); renderMessages(); }
}

async function start() {
  setInterval(async () => {
    const chat = state.chat;
    if (!chat?.subscription_pending || state.live) return;
    try {
      const fresh = await json(`${API}/chats/${chat.id}`);
      if (state.chat?.id !== chat.id) return;
      state.chat = fresh; renderMessages(false);
      if (!fresh.subscription_pending) { say(fresh.messages.at(-1)?.error || 'Ответ подписочного чата получен'); void loadList(); }
    } catch (error) { say(error.message); }
  }, 3000);
  $('#acInteraction').addEventListener('change', () => {
    const agent = $('#acInteraction').value === 'agent';
    $('#acSubscriptionHint').hidden = agent;
    say('');
    $('#acSend').title = agent ? 'Отправить агенту' : 'Отправить через подписочный чат';
    renderTaskToggle();
  });
  taskPanel = mountTaskPanel($('#acTaskPanel'), { say, onChange: (run) => {
    // The Studio woke the agent (an answer, finished renders): follow its new turn here.
    if (run.busy && !state.live && state.chat?.run_id === run.id) void open(state.chat.id);
    void loadList();
  } });
  const config = document.querySelector('.ac-config');
  document.addEventListener('pointerdown', (event) => {
    if (config.open && !config.contains(event.target)) config.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && config.open) { config.open = false; config.querySelector('summary').focus(); }
  });
  $('#acTaskToggle').addEventListener('change', renderTaskToggle);
  $('#acInput').addEventListener('input', () => { suggestDeliverable(); resizeComposer(); });
  $('#acDeliverable').addEventListener('change', () => { deliverableChosen = true; });
  json('/api/franchises').then((list) => {
    state.franchises = Array.isArray(list) ? list : (list.items || []);
    $('#acFranchise').replaceChildren(el('option', { value: '' }, 'Франшиза: определить из сообщения'),
      ...state.franchises.map((item) => el('option', { value: item.id }, item.name)));
  }).catch(() => {});
  $('#acNew').addEventListener('click', () => { void newChat().catch((error) => say(error.message)); });
  $('#acDelete').addEventListener('click', () => { void removeChat(); });
  $('#acClear').addEventListener('click', () => { void removeAll().catch((error) => say(error.message)); });
  photos = installAttachments({ input: $('#acInput'), dropZone: document.querySelector('.ac-composer'), say, className: 'ac' });
  photos.button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m8.5 12.5 5.7 5.7a3.5 3.5 0 0 0 5-5L12 6a5 5 0 0 0-7.1 7.1l7.2 7.2a6.5 6.5 0 0 0 9.2-9.2L14 3.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  $('#acSend').before(photos.button, photos.picker);
  document.querySelector('.ac-composer').prepend(photos.tray);
  $('#acSend').addEventListener('click', () => { void submit(); });
  $('#acStop').addEventListener('click', () => { void stop(); });
  $('#acModel').addEventListener('change', (event) => { void patch({ model: event.target.value }); });
  $('#acEffort').addEventListener('change', (event) => { void patch({ effort: event.target.value }); });
  $('#acInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); }
  });
  json(`${API}/providers`).then((providers) => {
    state.providers = providers;
    if (!state.chat && !providers.codex?.logged_in && providers.claude?.logged_in) state.provider = 'claude';
    renderHeader();
  }).catch((error) => say(`Не удалось проверить вход: ${error.message}`));
  try {
    await loadList();
  } catch (error) {
    say(`Studio недоступна или устарела: ${error.message}`);
    return;
  }
  const params = new URLSearchParams(location.search);
  if (COMPUTE_MODE) {
    const existing = state.chats.find((chat) => chat.id === params.get('chat'));
    if (existing) await open(existing.id);
    else { state.chat = null; await newChat(); }
    $('#acTaskToggle').checked = false;
    renderTaskToggle();
    $('#acInput').placeholder = 'Вставь данные сервера Vast и напиши: разверни и подключи Studio';
    return;
  }
  if (params.get('task') === 'new') { $('#acTaskToggle').checked = true; }
  // Old «Задачи агента» links (/tasks.html?run=…) land here with ?run=…
  const fromRun = params.get('run') && state.runs.find((run) => run.id === params.get('run'))?.chat_id;
  const wanted = fromRun || params.get('chat');
  const first = state.chats.find((chat) => chat.id === wanted) || state.chats[0];
  if (first && params.get('task') !== 'new') await open(first.id); else { renderHeader(); renderTaskToggle(); renderMessages(); }
}

// The page-boot cover waits for the first render, so the page does not grow in front of the user.
window.studioBoot?.hold(start());
