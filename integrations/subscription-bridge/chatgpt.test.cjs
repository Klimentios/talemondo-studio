const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(__dirname + '/chatgpt.js', 'utf8');

function harness({ draft = '', image = true } = {}) {
  let handler, sent = 0, now = 0, response = null;
  const input = { innerText: draft, tagName: 'DIV', focus() {}, dispatchEvent() {} };
  const document = {
    querySelector(selector) {
      if (selector.includes('prompt-textarea')) return input;
      if (selector.includes('send-button')) return { disabled: false, click() {
        sent++; response = { innerText: 'Готово', querySelectorAll: () => image ? [{
          complete: true, naturalWidth: 1024, naturalHeight: 1536, src: 'https://chatgpt.com/image.png',
        }] : [] };
      } };
      return null;
    },
    querySelectorAll: () => response ? [response] : [],
    execCommand(command, _, text) { input.innerText = text; },
  };
  class Reader { readAsDataURL() { this.result = 'data:image/png;base64,cG5n'; this.onload(); } }
  const context = {
    chrome: { runtime: { id: 'ours', onMessage: { addListener(fn) { handler = fn; } } } },
    document, Date: { now: () => now }, URL, location: { href: 'https://chatgpt.com/', origin: 'https://chatgpt.com' },
    setTimeout(fn, ms) { now += ms; queueMicrotask(fn); }, InputEvent: class {}, FileReader: Reader,
    fetch: async () => ({ ok: true, blob: async () => ({ size: 3, type: 'image/png' }) }),
  };
  vm.runInNewContext(source, context);
  return { call: (message) => new Promise((resolve) => handler(message, { id: 'ours' }, resolve)), sent: () => sent };
}

test('adapter sends once and returns actual image bytes, without CLI or private endpoints', async () => {
  const h = harness();
  const result = await h.call({ type: 'tm-generate', turn: { files: [], prompt: 'Нарисуй', mode: 'image' } });
  assert.equal(h.sent(), 1);
  assert.equal(result.text, 'Готово');
  assert.equal(result.images[0], 'cG5n');
});

test('adapter refuses to overwrite an existing draft', async () => {
  const h = harness({ draft: 'чужой текст' });
  const result = await h.call({ type: 'tm-generate', turn: { files: [], prompt: 'x', mode: 'chat' } });
  assert.match(result.error, /черновик/);
  assert.equal(h.sent(), 0);
});
