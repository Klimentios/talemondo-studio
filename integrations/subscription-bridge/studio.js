// This isolated content script talks only to the local Studio origin and its own extension.
const API = '/api/subscription-bridge';
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function api(path, body) {
  const response = await fetch(API + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Studio HTTP ${response.status}`);
  return response.json();
}
async function base64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onerror = reject;
    reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(blob);
  });
}
async function loop() {
  for (;;) {
    try {
      if (!(await chrome.runtime.sendMessage({ type: 'tm-ready' }))?.ready) return;
      // Recover only stored results after a Studio tab reload; never resubmit a browser message.
      const stored = await chrome.storage.local.get(null);
      for (const [key, value] of Object.entries(stored)) {
        if (!key.startsWith('turn:') || !value.result || !value.token) continue;
        await api(`/turns/${value.requestId}/result`, { ...value.result, token: value.token });
        await chrome.storage.local.set({ [key]: { state: 'delivered', tabId: value.tabId } });
      }
      // Another Studio tab may be polling too; the server claim is transactional and global.
      const turn = await api('/claim', {});
      if (turn) {
        const receiptKey = `receipt:${turn.id}`;
        let result = (await chrome.storage.local.get(receiptKey))[receiptKey];
        if (!result) {
          try {
            const files = [];
            for (const file of turn.files) {
              const response = await fetch(`${API}/turns/${turn.id}/files/${encodeURIComponent(file.asset_id)}?token=${turn.token}`);
              if (!response.ok) throw new Error('Не удалось получить референс Studio');
              files.push({ ...file, data: await base64(await response.blob()) });
            }
            result = await chrome.runtime.sendMessage({ type: 'tm-turn', turn: { ...turn, files } });
          } catch (error) { result = { error: error.message }; }
          await chrome.storage.local.set({ [receiptKey]: result });
        }
        // Retry only returning the receipt. Never repeat the ChatGPT request.
        for (let n = 0; n < 30; n++) {
          try {
            await api(`/turns/${turn.id}/result`, { ...result, token: turn.token });
            await chrome.storage.local.remove(receiptKey);
            break;
          } catch { await delay(2000); }
        }
      }
    } catch { /* Studio restart/offline: poll later; claimed turns are never replayed */ }
    await delay(3000);
  }
}
void loop();
