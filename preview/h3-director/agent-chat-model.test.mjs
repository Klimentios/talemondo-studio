import test from 'node:test';
import assert from 'node:assert/strict';
import { applyEvent, providerNote, renderMarkdown, splitLines } from './agent-chat-model.mjs';

test('markdown is escaped and small', () => {
  const html = renderMarkdown('**Итог** `x`\n- <b>a</b>\n- b\n\n```js\nlet a = 1 < 2;\n```');
  assert.match(html, /<p><strong>Итог<\/strong> <code>x<\/code><\/p>/);
  assert.match(html, /<ul><li>&lt;b&gt;a&lt;\/b&gt;<\/li><li>b<\/li><\/ul>/);
  assert.match(html, /<pre><code>let a = 1 &lt; 2;\n<\/code><\/pre>/);
  assert.doesNotMatch(renderMarkdown('<img src=x onerror=alert(1)>'), /<img/);
});

test('stream events build the live message', () => {
  const message = { text: '', events: [] };
  applyEvent(message, { kind: 'tool', id: 't', name: 'assets_list', status: 'started' });
  applyEvent(message, { kind: 'tool', id: 't', status: 'done', output: '3' });
  applyEvent(message, { kind: 'text', text: 'Готово' });
  applyEvent(message, { kind: 'done', seconds: 4 });
  assert.deepEqual(message, { text: 'Готово', events: [{ kind: 'tool', id: 't', name: 'assets_list', status: 'done', output: '3' }], seconds: 4 });
});

test('an API error repeated as answer text is shown once', () => {
  const message = { text: '', events: [] };
  applyEvent(message, { kind: 'text', text: 'Failed to authenticate. API Error: 401' });
  applyEvent(message, { kind: 'error', text: 'Failed to authenticate. API Error: 401 — claude auth login' });
  assert.equal(message.text, '');
  assert.match(message.error, /401/);
});

test('ndjson split keeps the partial tail', () => {
  const [events, rest] = splitLines('{"kind":"text","text":"a"}\n{"kind":"pi');
  assert.deepEqual(events, [{ kind: 'text', text: 'a' }]);
  assert.equal(rest, '{"kind":"pi');
  assert.equal(providerNote({ installed: true, logged_in: false, detail: 'Войди' }), 'Войди');
});
