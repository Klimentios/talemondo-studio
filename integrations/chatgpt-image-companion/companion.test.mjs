import test from 'node:test';
import assert from 'node:assert/strict';
import { createHostBridge, mountCompanion, workerPrompt } from './companion.mjs';
import { companionLink, imagePurpose } from '../../preview/h3-director/chat-images.mjs';

const id = 'a'.repeat(32);

test('a fantasy shot automatically requests its missing world before a start frame', () => {
  assert.equal(imagePurpose({ fantasy: true }, null), 'fantasy_world');
  assert.equal(imagePurpose({ fantasy: true }, { assetId: 'world' }), 'frame');
  assert.equal(imagePurpose({ fantasy: false }, null), 'frame');
});

function hostHarness() {
  const elements = new Map();
  const el = (key) => {
    if (!elements.has(key)) elements.set(key, { handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; }, replaceChildren() {} });
    return elements.get(key);
  };
  const sent = [], storage = new Map(), listeners = new Map();
  const win = { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name),
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) } };
  win.parent = { postMessage(message) {
    sent.push(message);
    if (!message.id) return;
    let result = {};
    if (message.method === 'ui/initialize') result = { hostContext: { 'openai/deepLink': { url: `/?request_id=${id}` } } };
    if (message.method === 'tools/call') result = { content: [{ type: 'text', text: JSON.stringify({ id, state: 'waiting_chatgpt', purpose: 'frame' }) }] };
    queueMicrotask(() => listeners.get('message')?.({ source: win.parent, data: { jsonrpc: '2.0', id: message.id, result } }));
  } };
  const cleanup = mountCompanion(win, { getElementById: el, createElement: () => ({}) });
  return { sent, el, cleanup };
}

test('companion opens a new conversation via the documented host message and never resends on double click', async () => {
  const h = hostHarness();
  try {
    await new Promise(setImmediate);
    assert.equal(h.el('send').disabled, false);
    h.el('send').handlers.click();
    await new Promise(setImmediate);
    h.el('send').handlers.click();
    await new Promise(setImmediate);
    const messages = h.sent.filter((m) => m.method === 'ui/message');
    assert.equal(messages.length, 1);
    assert.deepEqual(messages[0].params._meta, { 'openai/message': { target: 'new', send: true } });
    assert.match(messages[0].params.content[0].text, /image_request_claim/);
    assert.match(messages[0].params.content[0].text, /ImageGen/);
    assert.ok(h.sent.some((m) => m.params?.name === 'image_request_status' && m.params.arguments.request_id === id));
  } finally { h.cleanup(); }
});

test('draft mode leaves sending to the user; launcher carries only an encoded request ID', async () => {
  const h = hostHarness();
  try {
    await new Promise(setImmediate);
    h.el('draft').handlers.click();
    await new Promise(setImmediate);
    assert.equal(h.sent.find((m) => m.method === 'ui/message').params._meta['openai/message'].send, false);
    const url = new URL(companionLink(id));
    assert.equal(url.searchParams.get('path'), `/?request_id=${id}`);
    assert.throws(() => companionLink('bad/?payload'), /ID/);
    assert.throws(() => workerPrompt({ id: 'untrusted' }), /ID/);
  } finally { h.cleanup(); }
});

test('current chat receives an editable draft without opening a new conversation or resending', async () => {
  const h = hostHarness();
  try {
    await new Promise(setImmediate);
    h.el('active').handlers.click();
    await new Promise(setImmediate);
    h.el('active').handlers.click();
    await new Promise(setImmediate);
    const messages = h.sent.filter((m) => m.method === 'ui/message');
    assert.equal(messages.length, 1);
    assert.deepEqual(messages[0].params._meta, { 'openai/message': { target: 'active', send: false } });
    assert.match(messages[0].params.content[0].text, /image_request_claim/);
  } finally { h.cleanup(); }
});

test('host bridge accepts only its parent and times out without a retry', async () => {
  const sent = [], parent = { postMessage: (m) => sent.push(m) };
  const bridge = createHostBridge({ parentWindow: parent, timeoutMs: 15 });
  const pending = bridge.call('ui/message', {});
  bridge.receive({ source: {}, data: { jsonrpc: '2.0', id: 1, result: {} } });
  await assert.rejects(pending, /не подтвердил/);
  assert.equal(sent.length, 1);
});
