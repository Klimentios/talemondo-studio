// Source-level checks for app.mjs wiring that has no DOM-free seam.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { readdirSync } from 'node:fs';
// app.mjs is the composition root; its parts live in app-*.mjs. Read them as one source.
const app = ['app.mjs', ...readdirSync(new URL('.', import.meta.url)).filter((name) => /^app-[a-z]+\.mjs$/.test(name)).sort()]
  .map((name) => readFileSync(new URL(`./${name}`, import.meta.url), 'utf8')).join('\n');
const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');

function handler(selector, event) {
  const start = app.indexOf(`$('${selector}').addEventListener('${event}'`);
  assert.ok(start >= 0, `${selector} ${event}`);
  // Handlers sit at the top level of app.mjs or inside an install*() function (indented).
  const ends = ['\n});', '\n  });'].map((end) => app.indexOf(end, start)).filter((at) => at >= 0);
  return app.slice(start, Math.min(...ends));
}

test('the size slider keeps the reference-detail choice', () => {
  assert.doesNotMatch(handler('#quality', 'input'), /refImageSize/);
  assert.match(handler('#refImageSize', 'change'), /project\.refImageSize = event\.target\.value/);
});

test('seed edits are saved on input and on change', () => {
  assert.match(app, /for \(const type of \['input', 'change'\]\) \$\('#seed'\)\.addEventListener\(type, [^\n]*scheduleDraft\(\)/);
});

test('the hidden style adapter select is gone; the LoRA comes from the scenario', () => {
  assert.doesNotMatch(app, /#styleAdapter/);
  assert.doesNotMatch(html, /id="styleAdapter"/);
  assert.match(app, /project\.mode === 'realistic' \|\| \((?:appState\.)?project\.mode === 'replace'/);
  assert.match(app, /project\.styleAdapter === 'none'/);
  assert.match(app, /Задание не отправлено: GPU не объявляет Character Swap/);
});

test('live preview polling goes through the GPU-reachability gate', () => {
  assert.match(app, /shouldPollPreview\(\{ gpuReachable: appState\.gpuReachable,/);
  assert.match(html, /id="promptWarnings"/);
});
