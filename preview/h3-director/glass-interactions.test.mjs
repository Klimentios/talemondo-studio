import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('pointer highlight follows a mouse and clears when it leaves; reduced motion does not activate it', () => {
  const listeners = {};
  const query = { matches: true, addEventListener() {} };
  const classes = new Set();
  const vars = {};
  const target = {
    classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x) },
    style: { setProperty: (name, value) => { vars[name] = value; } },
    matches: () => false,
    contains: (node) => node === target,
    getBoundingClientRect: () => ({ left: 10, top: 20, width: 100, height: 30 }),
    closest() { return this; },
  };
  const context = { matchMedia: () => query, document: { addEventListener: (type, fn) => { listeners[type] = fn; } },
    addEventListener() {}, requestAnimationFrame(fn) { fn(); return 1; }, cancelAnimationFrame() {} };
  vm.runInNewContext(readFileSync(new URL('./glass-interactions.mjs', import.meta.url), 'utf8'), context);
  listeners.pointermove({ pointerType: 'mouse', target, clientX: 55, clientY: 30 });
  assert.equal(classes.has('glass-pointer'), true);
  assert.equal(vars['--glass-pointer-x'], '45px');
  assert.equal(vars['--glass-pointer-y'], '10px');
  listeners.pointerout({ relatedTarget: null });
  assert.equal(classes.has('glass-pointer'), false);
  query.matches = false;
  listeners.pointermove({ pointerType: 'mouse', target, clientX: 55, clientY: 30 });
  assert.equal(classes.has('glass-pointer'), false);
});
