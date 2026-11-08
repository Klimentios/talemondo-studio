import test from 'node:test';
import assert from 'node:assert/strict';
import { completedJobs, sendAllCuts } from './scenes.mjs';

test('all cuts with a description are sent in order', async () => {
  const sent = [];
  const api = { cuts: () => [{ id: 'cut-1', prompt: 'A' }, { id: 'cut-2', prompt: ' ' }, { id: 'cut-3', prompt: 'C' }],
    enqueueCut: async (id, options) => { sent.push([id, options.withSceneFrame]); } };
  assert.equal(await sendAllCuts(api), 2);
  assert.deepEqual(sent, [['cut-1', true], ['cut-3', false]]);
});

test('empty scene is refused', async () => {
  await assert.rejects(sendAllCuts({ cuts: () => [{ id: 'c', prompt: '' }], enqueueCut: async () => {} }), /нет описаний/);
});

test('only finished clips can be continued', () => {
  assert.deepEqual(completedJobs([{ job_id: 'a', state: 'running' }, { job_id: 'b', state: 'completed' }]).map((j) => j.job_id), ['b']);
});
