import test from 'node:test';
import assert from 'node:assert/strict';
import { planFilm } from './film-assembly.mjs';

const scene = (title, cuts) => ({ title, cuts });
const cut = (id, job, startFrame = undefined) => ({ id, duration: 5, shotJobId: job, startFrame,
  shotQcReview: job ? 'accepted' : null, shotQc: job ? { job_id: job, decision: 'accept' } : null });

test('film plan keeps scene and shot order', () => {
  const scenes = [scene('Начало', [cut('a','job-a'), cut('b','job-b')]), scene('Финал', [cut('c','job-c')])];
  const jobs = new Map(['job-a','job-b','job-c'].map((id) => [id, { state: 'completed' }]));
  assert.deepEqual(planFilm(scenes, jobs), { ids: ['job-a','job-b','job-c'], issues: [], scenes: 2, shots: 3, seconds: 15, ready: true });
});

test('film plan reports missing and stale shots without dropping them', () => {
  const scenes = [scene('Комната', [cut('a','job-a'), cut('b','job-b',{ source: 'previous', from_job: 'old-job' })]), scene('Улица', [cut('c',null)])];
  const jobs = new Map([['job-a',{state:'completed'}],['job-b',{state:'completed'}]]);
  const plan = planFilm(scenes, jobs);
  assert.equal(plan.ready, false);
  assert.deepEqual(plan.ids, ['job-a']);
  assert.match(plan.issues[0], /Комната · шот 2: продолжение устарело/);
  assert.match(plan.issues[1], /Улица · шот 1: нет выбранного дубля/);
});

test('film plan refuses more shots than the concat endpoint accepts', () => {
  const cuts = Array.from({ length: 61 }, (_, index) => cut(`s${index}`, `job-${index}`));
  const jobs = new Map(cuts.map((item) => [item.shotJobId, { state: 'completed' }]));
  const plan = planFilm([scene('Длинный фильм', cuts)], jobs);
  assert.equal(plan.ready, false);
  assert.match(plan.issues.at(-1), /не более 60 шотов/);
});

test('film plan blocks a completed take rejected by QC', () => {
  const rejected = { ...cut('a', 'job-a'), shotQcReview: 'rejected' };
  const plan = planFilm([scene('Брак', [rejected])], new Map([['job-a', { state: 'completed' }]]));
  assert.equal(plan.ready, false);
  assert.deepEqual(plan.ids, []);
});
