// Subscription of the Studio agent (local_studio/agent_accounts.py): when Codex or Claude says the usage limit is
// reached, pick another account here or sign in to a new one. The main login of this Mac is never signed out.
// Mounted into the «✦ Агент» panel (agent-dock.mjs) and the «Агент» page under the provider choice.
const API = '/api/agent-accounts';
const LABEL = { codex: 'Codex', claude: 'Claude Code' };

const node = (tag, props = {}, ...children) => {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) if (key.includes('-')) element.setAttribute(key, value); else element[key] = value;
  element.append(...children.filter((child) => child !== null && child !== undefined));
  return element;
};

async function call(path, method = 'GET', body) {
  const response = await fetch(`${API}${path}`, { method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
  return payload;
}

/** The account row for the provider that `providerOf()` returns; `say` shows a status line. */
export function mountAccounts(host, { providerOf, say = () => {} }) {
  const select = node('select', { className: 'k-field', 'aria-label': 'Аккаунт агента' });
  const add = node('button', { type: 'button', className: 'k-btn k-btn--sm', title: 'Войти в другую подписку: откроется окно входа в браузере' }, '＋ Войти в другой');
  const out = node('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--ghost', title: 'Выйти из выбранного дополнительного аккаунта' }, 'Выйти');
  const row = node('div', { className: 'agent-accounts' }, select, node('div', { className: 'agent-accounts-actions' }, add, out));
  host.append(row);
  let provider = '';
  let waiting = null;

  async function render() {
    provider = providerOf();
    if (!LABEL[provider]) { row.hidden = true; return; }
    row.hidden = false;
    const data = await call(`/${provider}`).catch((error) => { say(error.message); return null; });
    if (!data) return;
    select.replaceChildren(...data.accounts.map((account) => node('option', { value: account.id,
      selected: account.id === data.active, disabled: !account.logged_in && account.id !== data.active },
      `${LABEL[provider]} · ${account.label}${account.logged_in ? '' : account.logging_in ? ' — ждёт входа' : ' — не вошёл'}`)));
    out.disabled = data.active === 'main';
    return data;
  }

  select.addEventListener('change', async () => {
    try { await call(`/${provider}/${select.value}/activate`, 'POST'); say(`Агент работает от аккаунта «${select.selectedOptions[0].textContent}». Новые ответы — с него.`); }
    catch (error) { say(error.message); }
    await render();
  });
  add.addEventListener('click', async () => {
    add.disabled = true;
    try {
      const started = await call(`/${provider}`, 'POST', {});
      if (started.login_url) window.open(started.login_url, '_blank', 'noopener');
      say(`Войди в другой аккаунт ${LABEL[provider]} в окне браузера — как только вход завершится, он появится в списке.`);
      clearInterval(waiting);
      let tries = 0;
      waiting = setInterval(async () => {
        const data = await render();
        const ready = data?.accounts.find((account) => account.id === started.id && account.logged_in);
        if (ready || ++tries > 120) {
          clearInterval(waiting);
          if (ready) { await call(`/${provider}/${started.id}/activate`, 'POST'); await render(); say(`Готово: агент переключён на «${ready.label}».`); }
        }
      }, 5000);
    } catch (error) { say(error.message); }
    finally { add.disabled = false; }
  });
  out.addEventListener('click', async () => {
    if (!window.confirm('Выйти из этого аккаунта? Агент вернётся к основному входу этого Mac.')) return;
    try { await call(`/${provider}/${select.value}/logout`, 'POST'); say('Вышел; агент снова на основном аккаунте.'); }
    catch (error) { say(error.message); }
    await render();
  });
  void render();
  return { refresh: render };
}

// The «✦ Агент» panel: mount under its provider buttons once it exists, follow its provider choice.
function mountInDock() {
  const settings = document.querySelector('#agentDock .ad-settings');
  if (!settings) return false;
  const status = document.querySelector('#agentDock .ad-status');
  const providerOf = () => (/Claude/.test(settings.querySelector('.k-chip[aria-pressed="true"]')?.textContent || '') ? 'claude' : 'codex');
  const accounts = mountAccounts(settings, { providerOf, say: (text) => { if (status) status.textContent = text; } });
  let last = providerOf();
  new MutationObserver(() => { if (providerOf() !== last) { last = providerOf(); void accounts.refresh(); } })
    .observe(settings, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed'] });
  return true;
}

if (typeof document !== 'undefined' && !mountInDock()) {
  const wait = new MutationObserver(() => { if (mountInDock()) wait.disconnect(); });
  wait.observe(document.body, { childList: true, subtree: true });
}
