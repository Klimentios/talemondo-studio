// page-boot.js hides a Studio page until its modules have rebuilt it, so switching sections does not
// flash the raw page or jump. Every page with the shared side menu must load it first in <head>.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = fileURLToPath(new URL('.', import.meta.url));
const source = readFileSync(join(root, 'page-boot.js'), 'utf8');

function pages(dir = root) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'node_modules' ? [] : pages(path);
    return name.endsWith('.html') ? [path] : [];
  });
}

test('every page with the side menu loads page-boot.js synchronously before its styles', () => {
  const withRail = pages().filter((path) => readFileSync(path, 'utf8').includes('studio-rail.mjs'));
  assert.ok(withRail.length >= 7);
  for (const path of withRail) {
    const html = readFileSync(path, 'utf8');
    const boot = html.search(/<script src="\/page-boot\.js\?v=[^"]+"><\/script>/);
    assert.notEqual(boot, -1, `${path}: no page-boot.js`);
    assert.ok(boot < html.search(/<link[^>]+stylesheet/), `${path}: page-boot.js must come before the stylesheets`);
  }
});

function boot({ holdAttr = false } = {}) {
  const classes = new Set();
  const listeners = {};
  const timers = [];
  const window = {};
  const context = {
    window,
    document: {
      documentElement: {
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
        hasAttribute: (name) => holdAttr && name === 'data-boot-hold',
      },
      addEventListener: (name, fn) => { listeners[name] = fn; },
    },
    requestAnimationFrame: (fn) => fn(),
    setTimeout: (fn) => timers.push(fn),
    Promise,
  };
  vm.runInNewContext(source, context);
  return { classes, window, parsed: () => listeners.DOMContentLoaded(), timeout: () => timers.forEach((fn) => fn()) };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('the cover lifts after DOMContentLoaded and after every hold', async () => {
  const page = boot();
  assert.ok(page.classes.has('k-boot'));
  let finish;
  page.window.studioBoot.hold(new Promise((resolve) => { finish = resolve; }));
  page.parsed();
  await tick();
  assert.ok(page.classes.has('k-boot'), 'still waiting for the hold');
  finish();
  await tick();
  assert.ok(!page.classes.has('k-boot'));
});

test('data-boot-hold waits for a hold registered after DOMContentLoaded; a failed hold still reveals', async () => {
  const page = boot({ holdAttr: true });
  page.parsed();
  await tick();
  assert.ok(page.classes.has('k-boot'), 'no hold yet');
  page.window.studioBoot.hold(Promise.reject(new Error('API down')));
  await tick();
  assert.ok(!page.classes.has('k-boot'));
});

test('the safety timeout always reveals the page', () => {
  const page = boot({ holdAttr: true });
  page.window.studioBoot.hold(new Promise(() => {}));
  page.timeout();
  assert.ok(!page.classes.has('k-boot'));
});
