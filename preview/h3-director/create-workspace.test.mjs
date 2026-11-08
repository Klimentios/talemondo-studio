import test from 'node:test';
import assert from 'node:assert/strict';
import { takeCount, settingsForTake, bindCreateBatch } from './create-workspace.mjs';

test('takeCount accepts one to four jobs and bounds unexpected values', () => {
  assert.equal(takeCount('4'), 4);
  assert.equal(takeCount('2'), 2);
  assert.equal(takeCount('0'), 1);
  assert.equal(takeCount('8'), 4);
  assert.equal(takeCount('invalid'), 1);
});

test('explicit seed advances per take while random-seed jobs stay independent', () => {
  const settings = { seed: 41, duration: 10 };
  assert.deepEqual(settingsForTake(settings, 0, 4), { seed: 41, duration: 10 });
  assert.deepEqual(settingsForTake(settings, 1, 4), { seed: 42, duration: 10 });
  assert.deepEqual(settingsForTake({ seed: null }, 2, 4), { seed: null });
  assert.deepEqual(settingsForTake({ seed: 2 }, 1, 1), { seed: 2 });
  assert.equal(settings.seed, 41);
});

test('batch submission reports actual accepted job IDs in order', async () => {
  const handlers = {};
  const accepted = [];
  const seeds = [];
  const button = { disabled: false, querySelector: () => ({ textContent: '' }),
    setAttribute: () => {}, addEventListener: (event, fn) => { handlers[event] = fn; } };
  const countSelect = { value: '3', addEventListener: () => {} };
  let saved = false;
  bindCreateBatch({ button, countSelect, prepare: () => ({ prompt: 'shot', settings: { seed: 20 } }), referenceIds: () => ['ref'],
    enqueue: async (_prompt, _ids, settings) => { seeds.push(settings.seed); return { job_id: `job-${seeds.length}` }; },
    onSubmitted: (job) => accepted.push(job.job_id), saveDraft: async () => { saved = true; },
    isReady: () => true, toast: () => {} });
  await handlers.click();
  assert.deepEqual(accepted, ['job-1', 'job-2', 'job-3']);
  assert.deepEqual(seeds, [20, 21, 22]);
  assert.equal(saved, true);
  assert.equal(button.disabled, false);
});
