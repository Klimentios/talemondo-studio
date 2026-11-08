import test from 'node:test';
import assert from 'node:assert/strict';
import { startStudioPolling } from './studio-polling.mjs';
import { featureAvailability } from './gpu-availability.mjs';

test('unreachable GPU does not imply an uninstalled feature', () => {
  const offline = featureAvailability({ gpu_configured: true, gpu_reachable: false }, false);
  assert.equal(offline.state, 'offline');
  assert.doesNotMatch(offline.message, /не установлен/);
  assert.equal(featureAvailability({ gpu_configured: true, gpu_reachable: true }, false).state, 'unsupported');
  assert.equal(featureAvailability({ gpu_configured: true, gpu_reachable: true }, true).state, 'ready');
  assert.equal(featureAvailability(null, false).state, 'unknown');
});

test('polling backs off when idle, follows active jobs, stops in a hidden tab and avoids overlap', async () => {
  let active = false; let refreshes = 0; let finish;
  let listener; let scheduled;
  const doc = { hidden: false, addEventListener: (_name, fn) => { listener = fn; }, removeEventListener: () => {} };
  const polling = startStudioPolling({
    refresh: () => { refreshes++; return new Promise((resolve) => { finish = resolve; }); },
    isActive: () => active, document: doc,
    setTimer: (callback, ms) => { scheduled = { callback, ms }; return 1; },
    clearTimer: () => { scheduled = null; },
  });
  assert.equal(scheduled.ms, 30000);
  const tick = scheduled.callback();
  listener();
  assert.equal(refreshes, 1);
  active = true; finish(); await tick;
  assert.equal(scheduled.ms, 3000);
  doc.hidden = true; listener();
  assert.equal(scheduled, null);
  doc.hidden = false;
  const resumed = listener();
  assert.equal(refreshes, 2);
  finish(); await resumed;
  assert.equal(scheduled.ms, 3000);
  polling.stop();
  assert.equal(scheduled, null);
});
