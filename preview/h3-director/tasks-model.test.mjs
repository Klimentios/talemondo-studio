import test from 'node:test';
import assert from 'node:assert/strict';
import { artifactLinks, openAsks, progress, statusOf, waitLine } from './tasks-model.mjs';

test('task view helpers', () => {
  const run = { status: 'waiting_user', steps: [{ status: 'done' }, { status: 'running' }],
    asks: [{ id: 'a', answer: 'Да' }, { id: 'b', answer: '' }] };
  assert.equal(statusOf(run)[0], 'Ждёт твоего ответа');
  assert.deepEqual(openAsks(run).map((ask) => ask.id), ['b']);
  assert.deepEqual(progress(run), { done: 1, total: 2 });
  assert.equal(waitLine({ job_ids: ['1', '2'], render_ids: ['r'] }), 'Ждёт: 2 рендеров, 1 сборок');
  assert.equal(waitLine(null), '');
  const links = artifactLinks({ scene: 's', job_ids: ['abcdef123'], render_id: 'r1', export_id: 'e1', url: 'https://x' });
  assert.deepEqual(links.map((link) => link.label), ['Сцена на холсте', 'Рендер abcdef12', 'Монтаж r1', 'Экспорт e1']);
});

test('franchise matching requires an unambiguous full title', async () => {
  const { resolveFranchise } = await import('./chat-production-model.mjs');
  const franchises = [{ id: '1', name: 'ADAM & NOVA' }, { id: '2', name: 'NOVA & ORION' }, { id: '3', name: 'SAMPLE · Iris & Mira' }];
  assert.equal(resolveFranchise('Сделай ADAM и NOVA по идее 44', franchises)?.id, '1');
  assert.equal(resolveFranchise('Iris and Mira: идея 4', franchises)?.id, '3');
  assert.equal(resolveFranchise('Адам и Нова: идея 44', franchises)?.id, '1');
  assert.equal(resolveFranchise('Ирис и Мира: идея 4', franchises)?.id, '3');
  assert.equal(resolveFranchise('Нова по идее 44', franchises), null);
  assert.equal(resolveFranchise('ADAM & NOVA и NOVA & ORION', franchises), null);
});

test('task activity uses only associated IDs and measured sampling progress', async () => {
  const { productionIds, jobStatus } = await import('./chat-production-model.mjs');
  assert.deepEqual(productionIds({ wait: { scene_ids: ['scene'], job_ids: ['job'] }, steps: [{ artifacts: { job_id: 'job', keyframe_id: 'frame' } }] }),
    { scene: ['scene'], job: ['job'], render: [] });
  assert.equal(jobStatus({ state: 'running', sampling_value: 3, sampling_max: 24 }), 'Генерация · шаг 3/24');
  assert.equal(jobStatus({ state: 'uploading' }), 'Загрузка на GPU');
  assert.equal(jobStatus({ state: 'completed' }), 'Готово технически · ожидает проверки');
});


test('a request to create a video chooses film acceptance, while review stays a task', async () => {
  const { suggestedDeliverable } = await import('./chat-production-model.mjs');
  assert.equal(suggestedDeliverable('ADAM & NOVA: сделай ролик по идее 44'), 'film');
  assert.equal(suggestedDeliverable('Проверь план видео'), 'task');
});
