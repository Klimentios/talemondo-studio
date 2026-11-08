import test from 'node:test';
import assert from 'node:assert/strict';
import { cutWithJob, sendJobToStory } from './story-handoff.mjs';

const job = { state: 'completed', job_id: 'job-42', original_prompt: 'Кот смотрит под диван', settings_json: '{"duration":10}' };

test('a completed result becomes a selected Story take without duplicating an existing take', () => {
  const cut = { id: 'shot-1', prompt: 'Кот смотрит под диван', duration: 5, shotJobId: 'job-42', takes: [{ id: 'job-42', jobId: 'job-42' }] };
  const result = cutWithJob(cut, job);
  assert.equal(result.takes.length, 1);
  assert.equal(result.shotJobId, 'job-42');
  assert.equal(result.duration, 10);
  assert.equal(result.prompt, cut.prompt);
});

test('a different prompt creates a new shot and persists the handoff', async () => {
  const cuts = [{ id: 'shot-1', prompt: 'Другой кадр', duration: 5 }];
  let saved = false;
  const studio = { cuts: () => cuts, addShot: () => { const cut = { id: 'shot-2', prompt: '', duration: 5 }; cuts.push(cut); return cut; },
    editCuts: (change) => { cuts.splice(0, cuts.length, ...change(cuts)); }, saveDraft: async () => { saved = true; } };
  assert.equal(await sendJobToStory(job, studio), 'shot-2');
  assert.equal(cuts[0].prompt, 'Другой кадр');
  assert.equal(cuts[1].prompt, job.original_prompt);
  assert.equal(cuts[1].shotJobId, job.job_id);
  assert.equal(saved, true);
});
