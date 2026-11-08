import test from 'node:test';
import assert from 'node:assert/strict';
import { statusSummary, chatQueueView } from './studio-status-panel.mjs';

test('shows only real sampler progress while GPU is reachable', () => {
  assert.deepEqual(statusSummary({ reachable: true, detail: 'Сэмплинг: 25% (6/24 шагов). Затем сохранение.' }), { summary: 'Сэмплинг 25%', progress: 25 });
  assert.deepEqual(statusSummary({ reachable: false, detail: 'Сэмплинг: 25% (6/24 шагов)' }), { summary: 'GPU недоступен', progress: null });
  assert.deepEqual(statusSummary({ reachable: true, detail: 'Скачивание и проверка MP4' }), { summary: 'Сервер и очередь', progress: null });
  assert.equal(statusSummary({ reachable: true, detail: 'Sampling 101%', english: true }).progress, null);
});


test('chat queue never presents stale sampling while GPU is offline', () => {
  const jobs = [{ state: 'running', sampling_value: 6, sampling_max: 24 }];
  assert.match(chatQueueView({ gpu_reachable: true }, { paused: false }, jobs).detail, /25%/);
  assert.doesNotMatch(chatQueueView({ gpu_reachable: false }, { paused: false }, jobs).detail, /25%/);
  assert.match(chatQueueView({ gpu_reachable: true }, { paused: true }, jobs).title, /паузе/);
  assert.equal(chatQueueView(null, null, null).known, false);
});
