// Adapter to the visible ChatGPT UI. Selectors are deliberately isolated here.
// If login, verification, a changed layout or an ambiguous generation is encountered, fail closed.
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const composer = () => document.querySelector('#prompt-textarea[contenteditable="true"], textarea#prompt-textarea');
const assistants = () => [...document.querySelectorAll('[data-message-author-role="assistant"]')];
const stopButton = () => document.querySelector('[data-testid="stop-button"], button[aria-label="Stop streaming"]');
const sendButton = () => document.querySelector('[data-testid="send-button"], button[aria-label="Send prompt"]');
let busy = false;
async function imageData(img) {
  const url = new URL(img.currentSrc || img.src, location.href);
  if (!(url.protocol === 'blob:' || url.origin === location.origin ||
      (url.protocol === 'https:' && /(^|\.)oaiusercontent\.com$/.test(url.hostname)))) throw new Error('Неизвестный источник изображения');
  const response = await fetch(url.href, { credentials: 'include' });
  if (!response.ok) throw new Error('Файл результата недоступен');
  const blob = await response.blob();
  if (blob.size > 25 * 1024 * 1024 || !/^image\//.test(blob.type)) throw new Error('Некорректный файл результата');
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); reader.onerror = reject;
    reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(blob);
  });
}
async function generate(turn) {
  if (busy || stopButton()) throw new Error('В ChatGPT уже выполняется запрос');
  const input = composer();
  if (!input || (input.value || input.innerText || '').trim()) throw new Error('Поле ChatGPT недоступно или содержит чужой черновик');
  busy = true;
  try {
    if (turn.files.length) {
      const upload = document.querySelector('input[type="file"]');
      if (!upload) throw new Error('Загрузка референсов недоступна в текущем интерфейсе ChatGPT');
      const transfer = new DataTransfer();
      for (const file of turn.files) {
        const bytes = Uint8Array.from(atob(file.data), (c) => c.charCodeAt(0));
        transfer.items.add(new File([bytes], file.name || 'reference.png', { type: file.mime }));
      }
      upload.files = transfer.files;
      upload.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const before = new Set(assistants());
    input.focus();
    if (input.tagName === 'TEXTAREA') {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, turn.prompt);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    } else {
      document.execCommand('insertText', false, turn.prompt);
      input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: turn.prompt }));
    }
    // Wait for enabled submission (including uploads). A pre-dispatch failure has no generation side effect.
    let button;
    for (let n = 0; n < 60; n++) {
      button = sendButton();
      if (button && !button.disabled) break;
      await delay(500);
    }
    if (!button || button.disabled) throw new Error('ChatGPT не готов отправить сообщение; проверьте загрузку референсов');
    button.click(); // exactly once
    let signature = '', stableSince = Date.now();
    const deadline = Date.now() + 600000;
    while (Date.now() < deadline) {
      await delay(1000);
      const fresh = assistants().filter((node) => !before.has(node));
      if (!fresh.length) continue;
      const text = fresh.map((node) => node.innerText).join('\n\n');
      const images = fresh.flatMap((node) => [...node.querySelectorAll('img')])
        .filter((img) => img.complete && img.naturalWidth >= 256 && img.naturalHeight >= 256);
      const signatureNow = text + images.map((img) => img.currentSrc || img.src).join('|');
      if (signature !== signatureNow || stopButton()) { signature = signatureNow; stableSince = Date.now(); continue; }
      if (Date.now() - stableSince < 8000 || (!text && !images.length)) continue;
      // For image turns wait for the actual image, not a preliminary assistant acknowledgement.
      if (turn.mode === 'image' && !images.length) continue;
      return { text, images: await Promise.all(images.slice(0, 4).map(imageData)) };
    }
    throw new Error('Нет подтверждённого результата за 10 минут. Запрос мог выполниться; повтор отключён.');
  } finally { busy = false; }
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === 'tm-probe') { reply({ ready: Boolean(composer()) && !busy && !stopButton() }); return; }
  if (message.type !== 'tm-generate') return;
  generate(message.turn).then(reply, (error) => reply({ error: error.message }));
  return true;
});
