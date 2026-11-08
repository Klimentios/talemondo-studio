// «Агент» on the «Создать видео» page and its story canvas: Codex or Claude Code on the user's subscription.
// Same chats as agent-chat.html (local_studio/agent_chat.py). Every message carries what the form and the
// board show; the agent answers with composer_draft (one clip for the form) or scene_draft (a whole scene:
// shared franchise references, shots with H3 prompts, keyframe text, camera and seams), which the user
// applies with one click (agent-draft.mjs). The agent never starts keyframes or renders here.
import { MODES, TOOL_TEXT, applyEvent, providerNote, renderMarkdown, splitLines } from './agent-chat-model.mjs?v=20261028-2';
import { draftChips, draftFromEvent, sceneChips, sceneFromEvent, sceneLines, screenContext } from './agent-draft-model.mjs?v=20261106-aspects-1';
import { attachmentsView, installAttachments } from './agent-attachments.mjs?v=20261101-chat-production-1';
import { applyDraft, applySceneDraft } from './agent-draft.mjs?v=20261106-aspects-1';
import { scenePreviz } from './scene-previz.mjs?v=20261031-staging-1';

const API = '/api/agent-chat';
const STORE = 'studio-agent-dock';
const state = { open: false, providers: {}, chat: null, live: null, provider: 'codex', applied: new Set(), sceneId: '', stream: null, newChat: false };
let sceneRequest = 0;
const ui = {};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (['className', 'value', 'disabled', 'selected', 'hidden', 'innerHTML', 'title'].includes(key)) node[key] = value;
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

function remember(patch) {
  try { localStorage.setItem(STORE, JSON.stringify({ ...recall(), ...patch })); } catch { /* private mode */ }
}
function recall() {
  try { return JSON.parse(localStorage.getItem(STORE) || '{}'); } catch { return {}; }
}

function say(text) { ui.status.textContent = text || ''; }

function screen() {
  // A page with its own screen (the «Монтаж» timeline) describes itself; else the «Создать видео» form.
  if (typeof window.studioScreen === 'function') return window.studioScreen();
  const studio = window.h3Studio;
  const value = (selector) => document.querySelector(selector)?.value;
  const scenes = studio?.sceneState?.();
  return screenContext({
    prompt: value('#mainPrompt') || '', references: studio?.references?.() || [], duration: value('#duration'),
    aspect: value('#aspect'), quality: value('#quality'), cuts: studio?.cuts?.() || [],
    scene: scenes?.scenes?.find((scene) => scene.id === scenes.activeSceneId)?.title || '',
  });
}

// --- header ---------------------------------------------------------------------------------

function renderHeader() {
  const chat = state.chat;
  const provider = chat?.provider || state.provider;
  const locked = Boolean(chat?.messages?.length);
  ui.providers.replaceChildren(...['codex', 'claude'].map((name) => el('button', {
    type: 'button', className: 'k-chip', 'aria-pressed': String(provider === name), disabled: Boolean(state.live),
    title: locked && provider !== name ? `Начать новый чат с ${state.providers[name]?.label || name}` : providerNote(state.providers[name]),
    // A chat keeps its agent (the CLI session belongs to it): another agent means a new chat, started right here.
    onclick: () => {
      if (provider === name || state.live) return;
      state.provider = name;
      if (locked) { state.chat = null; state.newChat = true; remember({ chat: '' }); renderHeader(); renderMessages(true); say(`Новый чат: ${state.providers[name]?.label || name}`); }
      else void restart();
    },
  }, state.providers[name]?.label || name)));
  const info = state.providers[provider];
  ui.model.replaceChildren(el('option', { value: '' }, info?.default ? `По умолчанию (${info.default})` : 'Модель по умолчанию'),
    ...(info?.models || []).map(([value, label]) => el('option', { value, selected: value === (chat?.model || '') }, label)));
  ui.model.value = chat?.model || '';
  // Lighter effort = fewer tokens of the subscription; «по умолчанию» is the CLI's own setting.
  const efforts = info?.efforts || [];
  ui.effort.replaceChildren(el('option', { value: '' }, `Мощность: по умолчанию${info?.default_effort ? ` (${info.default_effort})` : ''}`),
    ...efforts.map(([value, label]) => el('option', { value, selected: value === (chat?.effort || recall().effort || '') }, `Мощность: ${label}`)));
  ui.effort.value = efforts.some(([value]) => value === (chat?.effort ?? recall().effort)) ? (chat?.effort ?? recall().effort) : '';
  ui.modes.replaceChildren(...MODES.map(([value, label]) => el('button', { type: 'button', className: 'k-chip',
    'aria-pressed': String((chat?.mode || recall().mode || 'read') === value), onclick: () => void patch({ mode: value }) }, label)));
  ui.full.href = chat ? `/agent-chat.html?chat=${chat.id}` : '/agent-chat.html';
  ui.login.hidden = !info || info.logged_in;
  ui.login.textContent = info && !info.logged_in
    ? `${info.label}: ${info.installed ? `нет входа — в терминале: ${provider === 'claude' ? 'claude auth login' : 'codex login'}` : 'CLI не установлен'}` : '';
}

async function patch(fields) {
  if (fields.mode) remember({ mode: fields.mode });
  if (fields.effort !== undefined) remember({ effort: fields.effort });
  if (!state.chat || state.live) { renderHeader(); return; }
  try { state.chat = await send(`${API}/chats/${state.chat.id}`, 'PATCH', fields); } catch (error) { say(error.message); }
  renderHeader();
}

async function restart() {
  // A chat without messages switches provider by being replaced.
  if (state.chat && !state.chat.messages.length) await json(`${API}/chats/${state.chat.id}`, { method: 'DELETE' }).catch(() => {});
  state.chat = null;
  state.newChat = true;
  remember({ chat: '' });
  renderHeader(); renderMessages();
}

// --- one chat per scene ------------------------------------------------------------------------
// Every Studio scene is its own space with its own agent chat: switching scenes switches chats, a new scene
// starts an empty one, and the agent of a scene acts only in it (agent_chat.py scene_id → MCP H3_SCENE_ID).
// A chat keeps running on the server while the user looks at another scene.

function sceneNow() {
  const studio = window.h3Studio;
  const id = studio?.sceneState?.()?.activeSceneId || '';
  const item = studio?.sceneState?.()?.scenes?.find((scene) => scene.id === id);
  const franchise = studio?.project?.()?.sceneDirector?.agentScene?.franchise_id
    || item?.sceneDirector?.agentScene?.franchise_id || item?.franchiseSource?.franchise_id || '';
  return { id, franchise };
}

async function openSceneChat() {
  const scene = sceneNow();
  const request = ++sceneRequest;
  state.stream?.abort();
  state.stream = null; state.live = null; state.chat = null;
  renderHeader(); renderMessages(true);
  const effort = (state.providers[state.provider]?.efforts || []).some(([value]) => value === recall().effort) ? recall().effort : '';
  let chat;
  try {
    chat = await send(`${API}/scene-chats/${encodeURIComponent(scene.id)}`, 'POST', {
      provider: state.provider, mode: recall().mode || 'read', effort, franchise_id: scene.franchise,
      title: `Сцена: ${window.h3Studio.sceneState().scenes.find((item) => item.id === scene.id)?.title || ''}`.slice(0, 120),
    });
  } catch (error) { if (request === sceneRequest) say(error.message); return; }
  if (request !== sceneRequest || state.sceneId !== scene.id) return;
  state.chat = chat;
  state.newChat = false;
  state.provider = chat.provider;
  renderHeader(); renderMessages(true);
  say('');
  if (chat.running) await reconnect(chat);
}

async function reconnect(chat) {
  state.live = { role: 'assistant', text: '', events: [], provider: chat.provider, model: chat.model };
  renderMessages();
  state.stream = new AbortController();
  const live = state.live;
  try {
    const response = await fetch(`${API}/chats/${chat.id}/stream`, { signal: state.stream.signal });
    if (response.ok) await follow(response, live);
  } catch (error) { if (error.name !== 'AbortError') say(error.message); }
  finally { await finish(chat.id); }
}

async function watchScene() {
  const now = sceneNow();
  if (now.id === state.sceneId) return;
  const before = state.sceneId;
  state.sceneId = now.id;
  if (!now.id) {
    ++sceneRequest; state.stream?.abort(); state.stream = null; state.live = null; state.chat = null;
    renderHeader(); renderMessages(true); return;
  }
  // A deleted scene ends its agent: its running answer is stopped.
  const scenes = window.h3Studio?.sceneState?.()?.scenes || [];
  if (before && !scenes.some((scene) => scene.id === before)) {
    const orphans = await json(`${API}/chats?scene_id=${encodeURIComponent(before)}`).catch(() => []);
    for (const chat of orphans.filter((item) => item.running)) void send(`${API}/chats/${chat.id}/stop`, 'POST', {}).catch(() => {});
  }
  await openSceneChat();
}

async function ensureChat() {
  if (state.chat) return state.chat;
  const effort = (state.providers[state.provider]?.efforts || []).some(([value]) => value === recall().effort) ? recall().effort : '';
  const scene = state.sceneId ? sceneNow() : { id: '', franchise: '' };
  const endpoint = scene.id && !state.newChat ? `${API}/scene-chats/${encodeURIComponent(scene.id)}` : `${API}/chats`;
  const chat = await send(endpoint, 'POST', { provider: state.provider, mode: recall().mode || 'read', effort,
    ...(scene.id ? { scene_id: scene.id, title: `Сцена: ${window.h3Studio.sceneState().scenes.find((item) => item.id === scene.id)?.title || ''}`.slice(0, 120) } : {}),
    ...(scene.franchise ? { franchise_id: scene.franchise } : {}) });
  if (scene.id && (scene.id !== state.sceneId || scene.id !== sceneNow().id)) throw new Error('Сцена переключилась — отправь сообщение в открытой сцене');
  state.chat = chat;
  state.newChat = false;
  remember({ chat: state.chat.id });
  renderHeader();
  return state.chat;
}

// --- messages -------------------------------------------------------------------------------

function draftCard(draft, key) {
  const applied = state.applied.has(key);
  const result = el('small', { className: 'ad-draft-result' }, applied ? 'Применено в форму' : '');
  const button = el('button', { type: 'button', className: 'k-btn k-btn--primary k-btn--sm', disabled: applied,
    onclick: async () => {
      button.disabled = true; result.textContent = 'Применяю…';
      try {
        const done = await applyDraft(draft);
        state.applied.add(key);
        result.textContent = `Применено: ${done.join(', ')}. Проверь форму и нажми «Создать».`;
        button.textContent = 'Применено';
      } catch (error) { result.textContent = error.message; button.disabled = false; }
    } }, applied ? 'Применено' : 'Применить в форму');
  const refs = draft.references || [];
  return el('section', { className: 'ad-draft' },
    el('header', {}, el('strong', {}, draft.title || 'Черновик ролика'), ...draftChips(draft).map((chip) => el('span', { className: 'ad-chip' }, chip))),
    draft.note ? el('p', { className: 'ad-draft-note' }, draft.note) : null,
    refs.length ? el('div', { className: 'ad-refs' }, ...refs.map((ref) => el('figure', { title: `${ref.role}${ref.owner ? ` · ${ref.owner}` : ''}` },
      el('img', { src: `/api/assets/${encodeURIComponent(ref.asset_id)}/thumbnail`, alt: '', loading: 'lazy' }),
      el('figcaption', {}, ref.role)))) : null,
    el('details', {}, el('summary', {}, 'Промпт'), el('pre', {}, draft.prompt)),
    el('div', { className: 'ad-draft-actions' }, button, result));
}

function sceneCard(scene, key) {
  const applied = state.applied.has(key);
  const result = el('small', { className: 'ad-draft-result' }, applied ? 'Сцена на холсте' : '');
  const button = el('button', { type: 'button', className: 'k-btn k-btn--primary k-btn--sm', disabled: applied,
    onclick: async () => {
      button.disabled = true; result.textContent = 'Раскладываю на холст…';
      try {
        const done = await applySceneDraft(scene);
        state.applied.add(key);
        result.textContent = `Готово: ${done.join(', ')}. Проверь шоты и нажми «Собрать сцену».`;
        button.textContent = 'На холсте';
      } catch (error) { result.textContent = error.message; button.disabled = false; }
    } }, applied ? 'На холсте' : 'Применить на холст');
  const refs = scene.references || [];
  return el('section', { className: 'ad-draft ad-scene' },
    el('header', {}, el('strong', {}, scene.title || 'Сцена'),
      ...sceneChips(scene).map((chip) => el('span', { className: 'ad-chip' }, chip)),
      scene.idea_id ? el('span', { className: 'ad-chip', title: 'Сцена проверена по пакету идеи: герои, референсы, стиль и режим' }, 'в пакете идеи') : null),
    scene.idea ? el('p', { className: 'ad-draft-note' }, scene.idea) : null,
    refs.length ? el('div', { className: 'ad-refs' }, ...refs.map((ref) => el('figure', { title: `${ref.role}${ref.owner ? ` · ${ref.owner}` : ''}` },
      el('img', { src: `/api/assets/${encodeURIComponent(ref.asset_id)}/thumbnail`, alt: '', loading: 'lazy' }),
      el('figcaption', {}, ref.owner || ref.role)))) : null,
    scenePreviz(scene, el),
    el('ol', { className: 'ad-shots' }, ...sceneLines(scene).map((line, at) => el('li', {},
      el('details', {}, el('summary', {}, el('strong', {}, line.head), el('small', {}, line.meta)),
        line.beat ? el('p', {}, line.beat) : null, el('pre', {}, scene.shots[at].prompt))))),
    el('div', { className: 'ad-draft-actions' }, button, result));
}

function messageNode(message, index, live = false) {
  if (message.role === 'user') return el('article', { className: 'ad-msg ad-user' }, message.text, attachmentsView(message.attachments));
  // A reply may revise its draft after shot_lint: only the last accepted one is offered.
  const drafts = (message.events || []).map((event, at) => [draftFromEvent(event), `${index}:${at}`]).filter(([draft]) => draft).slice(-1);
  const scenes = (message.events || []).map((event, at) => [sceneFromEvent(event), `${index}:${at}`]).filter(([scene]) => scene).slice(-1);
  const tools = (message.events || []).filter((event) => !draftFromEvent(event) && !sceneFromEvent(event));
  return el('article', { className: 'ad-msg ad-agent' },
    tools.length ? el('div', { className: 'ad-tools' }, ...tools.map((event) => el('span', { className: `ad-tool is-${event.status || 'started'}`,
      title: TOOL_TEXT[event.status] || '' }, event.name || 'инструмент'))) : null,
    message.text ? el('div', { className: 'ad-text', innerHTML: renderMarkdown(message.text) }) : null,
    ...drafts.map(([draft, key]) => draftCard(draft, key)),
    ...scenes.map(([scene, key]) => sceneCard(scene, key)),
    live && !message.text && !message.error && !drafts.length && !scenes.length ? el('p', { className: 'ad-thinking' }, 'Агент думает…') : null,
    message.error ? el('p', { className: 'ad-error' }, message.error) : null);
}

function renderMessages(force = false) {
  const box = ui.messages;
  // Follow the stream only while the reader is at the bottom; scrolling up to read stays put.
  const follow = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const keep = box.scrollTop;
  const messages = state.chat?.messages || [];
  ui.messages.replaceChildren(...messages.map((message, index) => messageNode(message, index)),
    ...(state.live ? [messageNode(state.live, messages.length, true)] : []));
  if (!messages.length && !state.live) {
    ui.messages.append(el('div', { className: 'ad-empty' },
      el('p', {}, 'Агент видит форму и холст, библии и паспорта франшиз, библиотеку. Назови франшизу и что снять — он соберёт один шот или всю сцену: герои, костюмы, окружение, камера, переходы, промпты MiniMax H3. Применяешь кнопкой, генерацию запускаешь сам.'),
      ...['Моя франшиза, сцена 30 с: герой замечает пропажу и бросается в погоню',
        'Собери шот: герой замирает у двери, прислушивается и оборачивается',
        'Проверь мой промпт и референсы как оператор и предложи исправленный черновик'].map((text) =>
        el('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--ghost', onclick: () => { ui.input.value = text; ui.input.focus(); } }, text))));
  }
  box.scrollTop = follow || force ? box.scrollHeight : keep;
  ui.send.hidden = Boolean(state.live);
  ui.stop.hidden = !state.live;
}

async function follow(response, live = state.live) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!live || state.live !== live) { await reader.cancel(); return; }
    let events;
    [events, buffer] = splitLines(buffer + decoder.decode(value, { stream: true }));
    for (const event of events) if (event.kind !== 'ping') applyEvent(live, event);
    renderMessages();
  }
}

async function finish(chatId) {
  const current = state.chat;
  const live = state.live;
  if (current?.id !== chatId) return;
  const saved = await json(`${API}/chats/${chatId}`).catch(() => current);
  if (state.chat !== current || state.live !== live) return;
  state.live = null;
  state.chat = saved;
  renderMessages();
  json(`${API}/providers`).then((providers) => { state.providers = providers; renderHeader(); }).catch(() => {});
}

async function submit() {
  if (state.live) return;
  if (ui.photos.busy()) { say('Файл ещё загружается'); return; }
  const attachments = ui.photos.take();
  const text = ui.input.value.trim() || (attachments.length ? 'Посмотри прикреплённые файлы и опиши, как их можно использовать в Studio.' : '');
  if (!text) return;
  say('');
  let chat;
  try { chat = await ensureChat(); } catch (error) { say(error.message); ui.photos.restore(attachments); return; }
  if (state.sceneId && (chat.scene_id !== state.sceneId || sceneNow().id !== state.sceneId)) {
    say('Сцена переключилась — отправь сообщение в открытой сцене'); ui.photos.restore(attachments); return;
  }
  chat.messages.push({ role: 'user', text, attachments });
  state.live = { role: 'assistant', text: '', events: [], provider: chat.provider, model: chat.model };
  ui.input.value = '';
  renderMessages(true);
  state.stream = new AbortController();  // switching scenes stops watching; the answer goes on on the server
  const live = state.live;
  try {
    const response = await fetch(`${API}/chats/${chat.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, context: screen(), attachments: attachments.map((photo) => photo.asset_id) }), signal: state.stream.signal });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    await follow(response, live);
  } catch (error) {
    if (error.name === 'AbortError' || state.chat?.id !== chat.id) return;
    say(error.message); chat.messages.pop(); ui.input.value = text; ui.photos.restore(attachments);
  }
  await finish(chat.id);
}

// --- panel ----------------------------------------------------------------------------------

function toggle(open = !state.open) {
  state.open = open;
  ui.panel.hidden = !open;
  ui.toggle.setAttribute('aria-expanded', String(open));
  remember({ open });
  if (open) { renderMessages(); ui.input.focus(); }
}

async function load() {
  json(`${API}/providers`).then((providers) => {
    state.providers = providers;
    if (!state.chat && !providers.codex?.logged_in && providers.claude?.logged_in) state.provider = 'claude';
    renderHeader();
  }).catch((error) => say(`Не удалось проверить вход: ${error.message}`));
  // On the story page every scene has its own chat; elsewhere (Монтаж) the last chat is kept.
  // The Studio may still be loading: the watcher opens the scene's chat as soon as there is a scene.
  setInterval(() => { void watchScene(); }, 1000);
  if (sceneNow().id) { state.sceneId = sceneNow().id; await openSceneChat(); return; }
  const saved = recall().chat;
  if (saved) {
    try {
      state.chat = await json(`${API}/chats/${saved}`);
      state.provider = state.chat.provider;
    } catch { remember({ chat: '' }); }
  }
  renderHeader(); renderMessages(true);
  if (state.chat?.running) {
    const chat = state.chat;
    state.live = { role: 'assistant', text: '', events: [], provider: state.chat.provider, model: state.chat.model };
    const live = state.live;
    renderMessages();
    try {
      const response = await fetch(`${API}/chats/${state.chat.id}/stream`);
      if (response.ok) await follow(response, live);
    } finally { await finish(chat.id); }
  }
}

function install() {
  if (document.querySelector('.ad-panel')) return;
  ui.toggle = el('button', { type: 'button', className: 'k-btn k-btn--primary k-btn--pill ad-toggle', 'aria-expanded': 'false',
    'aria-controls': 'agentDock', title: 'Claude или Codex по подписке собирает ролик в этой форме', onclick: () => toggle() }, '✦ Агент');
  ui.providers = el('div', { className: 'ad-row', role: 'group', 'aria-label': 'Агент' });
  ui.model = el('select', { className: 'k-field', 'aria-label': 'Модель', onchange: (event) => void patch({ model: event.target.value }) });
  ui.effort = el('select', { className: 'k-field', 'aria-label': 'Мощность', title: 'Чем легче, тем меньше расход подписки',
    onchange: (event) => void patch({ effort: event.target.value }) });
  ui.modes = el('div', { className: 'ad-row', role: 'group', 'aria-label': 'Режим' });
  ui.full = el('a', { className: 'k-btn k-btn--sm k-btn--ghost', href: '/agent-chat.html', target: '_blank', rel: 'noopener', title: 'Все чаты' }, '↗');
  ui.login = el('p', { className: 'ad-login', hidden: true });
  ui.messages = el('div', { className: 'ad-messages', 'aria-live': 'polite' });
  ui.input = el('textarea', { className: 'k-field ad-input', rows: 3, placeholder: 'Что снять? Enter — отправить, Shift+Enter — новая строка',
    onkeydown: (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); } } });
  ui.send = el('button', { type: 'button', className: 'k-btn k-btn--primary', onclick: () => void submit() }, 'Отправить');
  ui.compose = el('footer', { className: 'ad-compose' });
  ui.photos = installAttachments({ input: ui.input, dropZone: ui.compose, say });
  ui.stop = el('button', { type: 'button', className: 'k-btn k-btn--danger', hidden: true,
    onclick: () => { if (state.chat) void send(`${API}/chats/${state.chat.id}/stop`, 'POST', {}).catch(() => {}); } }, 'Стоп');
  ui.status = el('small', { className: 'ad-status', role: 'status' });
  ui.panel = el('aside', { id: 'agentDock', className: 'ad-panel k-panel', hidden: true, 'aria-label': 'Агент' },
    el('header', { className: 'ad-head' },
      el('div', { className: 'ad-title' }, el('strong', {}, 'Агент'), el('small', {}, 'по подписке · видит этот экран')),
      el('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--ghost', title: 'Новый чат', onclick: () => { if (!state.live) void restart(); } }, '＋'),
      ui.full,
      el('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--ghost', 'aria-label': 'Закрыть', onclick: () => toggle(false) }, '×')),
    el('div', { className: 'ad-settings' }, ui.providers, el('div', { className: 'ad-pair' }, ui.model, ui.effort), ui.modes),
    ui.login, ui.messages,
    ui.compose);
  ui.compose.append(ui.photos.tray, ui.input, el('div', { className: 'ad-row' }, ui.photos.button, ui.photos.picker, ui.status, ui.send, ui.stop));
  document.body.append(ui.toggle, ui.panel);
  void load();
  if (recall().open) toggle(true);
}

install();
// Studio buttons that hand work to the agent («◇ Проверить» шот): open the panel and send the request.
window.h3AgentDock = {
  ask(text) { toggle(true); ui.input.value = text; void submit(); },
};
