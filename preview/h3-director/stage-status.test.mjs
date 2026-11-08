import test from 'node:test';
import assert from 'node:assert/strict';
import { stageStatus, parseUtc } from './stage-status.mjs';

const now = parseUtc('2026-10-03 18:00:00');

test('running job shows real step progress and percent', () => {
  const s = stageStatus({ state: 'running', sampling_value: 13, sampling_max: 26, updated_at: '2026-10-03 17:59:50' }, { now });
  assert.equal(s.title, 'Генерация идёт');
  assert.equal(s.percent, 50);
  assert.match(s.caption, /шаг 13 из 26 · 50%/);
  assert.equal(s.stale, false);
});

test('stuck job from an old server is not shown as running', () => {
  const s = stageStatus({ state: 'stopping', sampling_value: 13, sampling_max: 26, updated_at: '2026-09-30 20:57:08' }, { gpuReachable: false, now });
  assert.equal(s.title, 'Отмена ждёт GPU');
  assert.match(s.help, /3 дн назад/);
  assert.equal(s.stale, true);
});

test('no step data yet and stale running job are explained', () => {
  assert.match(stageStatus({ state: 'running', updated_at: '2026-10-03 17:59:59' }, { now }).caption, /сэмплинг ещё не начался/);
  assert.equal(stageStatus({ state: 'running', sampling_value: 3, sampling_max: 26, updated_at: '2026-10-03 17:50:00' }, { now }).stale, true);
});

test('live preview polling pauses while the GPU is unreachable and resumes after', async () => {
  const { shouldPollPreview, PREVIEW_INTERVAL_MS } = await import('./stage-status.mjs');
  const base = { lastRequest: 0, now: PREVIEW_INTERVAL_MS + 1 };
  assert.equal(shouldPollPreview({ ...base, gpuReachable: false }), false);
  assert.equal(shouldPollPreview({ ...base, gpuReachable: true }), true);
  assert.equal(shouldPollPreview({ ...base, gpuReachable: true, hidden: true }), false);
  assert.equal(shouldPollPreview({ ...base, gpuReachable: true, inFlight: true }), false);
  assert.equal(shouldPollPreview({ gpuReachable: true, lastRequest: 1000, now: 2000 }), false);
});
