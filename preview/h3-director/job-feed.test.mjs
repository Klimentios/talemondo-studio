import test from 'node:test';
import assert from 'node:assert/strict';
import { cacheSummary, visibleCreateJobs, createResultLabel, jobTimeLine } from './job-feed.mjs';

test('queue time uses the local timezone and labels synchronization separately', () => {
  const job = { state: 'running', created_at: '2026-10-22 21:12:30', updated_at: '2026-10-23 01:22:54' };
  const line = jobTimeLine(job, 'ru', Date.parse('2026-10-23T01:22:30Z'));
  assert.match(line, /Поставлено.*с постановки 4 ч 10 мин.*Синхронизация/);
  assert.equal(jobTimeLine({ state: 'queued_gpu' }), '');
});

test('historical videos are named by their prompt, only submitted batch jobs are takes', () => {
  const job = { job_id: 'old', state: 'completed', original_prompt: 'Автомобиль едет по ночному городу.' };
  assert.match(createResultLabel(job, []), /Автомобиль/);
  assert.equal(createResultLabel(job, ['old', 'new']), 'ВАРИАНТ 01');
  assert.equal(createResultLabel({ ...job, job_id: 'new' }, ['old', 'new']), 'ВАРИАНТ 02');
  assert.match(createResultLabel(job, ['other']), /Автомобиль/);
});

test('Create shows its latest batch first, then active and the last finished jobs (agents, API)', () => {
  // 02a0a4c5: jobs launched by agents or the API stay visible even when this browser remembers another batch.
  const old = { job_id: 'old', state: 'completed' };
  const first = { job_id: 'new-1', state: 'completed' };
  const second = { job_id: 'new-2', state: 'queued_local' };
  const jobs = [old, second, first];
  assert.deepEqual(visibleCreateJobs(jobs, [second], ['new-1', 'new-2']), [first, second, old]);
  assert.deepEqual(visibleCreateJobs(jobs, [second], ['new-1', 'new-2'], true), jobs);
  const current = { job_id: 'current', state: 'queued_gpu' };
  assert.deepEqual(visibleCreateJobs([current, ...jobs], [current, second], ['new-1', 'new-2']), [first, second, current, old]);
  const many = Array.from({ length: 6 }, (_, i) => ({ job_id: `done-${i}`, state: 'completed' }));
  assert.equal(visibleCreateJobs(many, [], []).length, 4);
});

test('an API job is cloud from creation, before MiniMax returns its task ID', async () => {
  const { isCloudJob } = await import('./job-feed.mjs');
  assert.equal(isCloudJob({ settings_json: JSON.stringify({ backend: 'minimax_api' }) }), true);
  assert.equal(isCloudJob({ remote_job_id: 'minimax:1', settings_json: '{}' }), true);
  assert.equal(isCloudJob({ remote_job_id: 'gpu-1', settings_json: '{}' }), false);
  assert.equal(isCloudJob({ settings_json: 'broken' }), false);
});

test('cache summary reports the mode the graph used and the requested one', () => {
  assert.equal(cacheSummary({ cache_mode: 'fast' }), 'кеш fast');
  assert.equal(cacheSummary({ cache_mode: 'safe', cache_mode_requested: 'fast' }), 'кеш safe (запрошен fast: видео-референс)');
  assert.equal(cacheSummary({ cache_mode: 'safe', cache_mode_requested: 'fast' }, true), 'cache safe (requested fast: video reference)');
  assert.equal(cacheSummary(null), '');
});

test('director fallback is a visible note', async () => {
  const { directorFallbackNote } = await import('./job-feed.mjs');
  assert.equal(directorFallbackNote({ director_fallback: null }), '');
  assert.match(directorFallbackNote({ director_fallback: 'Qwen вернула не JSON' }), /^Qwen не составил план — ролик собран по промпту Studio \(Qwen вернула не JSON\)$/);
  assert.match(directorFallbackNote({ director_fallback: 'x' }, true), /rendered from the Studio prompt/);
});

test('queue page names side jobs that hold the GPU and their errors', async () => {
  const { sideJobLine, activeSideJobs } = await import('./job-feed.mjs');
  const upscale = { kind: 'upscale', id: '105c9d13-9', title: 'Улучшение ролика', href: '/#jobQueue', state: 'running', source_job_id: '07e037bb-1' };
  assert.equal(sideJobLine(upscale).text, 'Улучшение ролика: обработка на GPU · ролик #07e037bb · #105c9d13');
  const edit = { kind: 'video_edit', id: 'abc', title: 'Правка видео', href: '/ltx-edit.html', state: 'failed', error: null };
  assert.equal(sideJobLine(edit).text, 'Правка видео: ошибка · #abc — GPU не сообщил причину');
  assert.match(sideJobLine({ ...edit, state: 'queued_local' }, false, true).text, /пауза очереди/);
  assert.match(sideJobLine(edit, true).text, /^Video edit: error/);
  const motion = { kind: 'motion', title: 'Сцена Motion Director', state: 'needs_reconciliation',
    scene_id: 'scene-a', revision: 3, id: 'm1', error: 'Ответ потерян' };
  assert.match(sideJobLine(motion).text, /сцена #scene-a · версия 3.*Ответ потерян/);
  assert.match(sideJobLine(motion, true).text, /^Motion Director scene:/);
  assert.deepEqual(activeSideJobs([upscale, edit]), [upscale]);
});

 test('cache report follows effective node configuration, including an A/B removal', () => {
  assert.match(cacheSummary({cache_mode:'fast', cache_execution:{enabled:true,effective:{threshold:0.1,start_percent:0.1,end_percent:0.95,max_consecutive_hits:2,temporal_guard:false}}}), /0.1.*guard off/);
  assert.equal(cacheSummary({cache_mode:'safe',cache_execution:{enabled:false}}), 'кеш off');
 });

test('cache counters report actual model calls without claiming speedup', () => {
  const text=cacheSummary({cache_mode:'safe',cache_execution:{enabled:true,effective:{threshold:.08,start_percent:.1,end_percent:.95,max_consecutive_hits:2,temporal_guard:true},runtime_counters:{passes:[{full_forward_calls:20,reused_forward_calls:5}]}}});
  assert.match(text,/5\/25/); assert.doesNotMatch(text,/speedup|ускорение/);
});
