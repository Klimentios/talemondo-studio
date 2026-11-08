// No cookies, credentials, private network endpoints or model APIs are read.
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const origin = sender.tab?.url && new URL(sender.tab.url).origin;
  if (!['http://127.0.0.1:18187', 'http://localhost:18187'].includes(origin)) return;
  if (message.type === 'tm-ready') { reply({ ready: true }); return; }
  if (message.type !== 'tm-turn') return;
  (async () => {
    const row = message.turn;
    if (!/^[a-f0-9]{32}$/.test(row?.id) || !/^[a-f0-9]{32}$/.test(row?.chat_id)) throw new Error('Некорректное задание');
    const key = `turn:${row.id}`;
    const saved = (await chrome.storage.local.get(key))[key];
    if (saved?.result) return saved.result; // receipt delivery may be repeated; generation cannot
    if (saved) throw new Error('Задание уже отправлялось. Проверьте фоновую вкладку ChatGPT; автоматический повтор запрещён.');
    const tabKey = `chat:${row.chat_id}`;
    const binding = (await chrome.storage.local.get(tabKey))[tabKey];
    let tabId = binding?.id;
    let tab = tabId ? await chrome.tabs.get(tabId).catch(() => null) : null;
    if (tab && binding.url !== tab.url) throw new Error('Вкладка коннектора открыта в другой беседе. Не отправляю контекст в чужой чат.');
    if (!tab || new URL(tab.url).origin !== 'https://chatgpt.com') {
      tab = await chrome.tabs.create({ url: 'https://chatgpt.com/', active: false });
      tabId = tab.id;
      await chrome.storage.local.set({ [tabKey]: { id: tabId, url: tab.url || 'https://chatgpt.com/' } });
    }
    let ready = false;
    for (let i = 0; i < 30; i++) {
      ready = await chrome.tabs.sendMessage(tabId, { type: 'tm-probe' }).then((r) => r?.ready).catch(() => false);
      if (ready) break;
      await wait(1000);
    }
    if (!ready) throw new Error('Войдите в ChatGPT в браузере. Поле ввода недоступно; запрос не отправлен.');
    // Persist before dispatch: extension/service-worker interruption must never resubmit a paid/included turn.
    await chrome.storage.local.set({ [key]: { state: 'dispatched', tabId, at: Date.now() } });
    const result = await chrome.tabs.sendMessage(tabId, { type: 'tm-generate', turn: row });
    const currentTab = await chrome.tabs.get(tabId);
    await chrome.storage.local.set({ [tabKey]: { id: tabId, url: currentTab.url } });
    await chrome.storage.local.set({ [key]: { state: 'returned', tabId, result, requestId: row.id, token: row.token } });
    return result;
  })().then(reply, (error) => reply({ error: error.message }));
  return true;
});
