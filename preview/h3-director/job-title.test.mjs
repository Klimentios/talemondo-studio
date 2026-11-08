import test from 'node:test';
import assert from 'node:assert/strict';
import { jobTitle, jobNames } from './job-title.mjs';

test('uses the summary sentence instead of the IR header', () => {
  const job = { job_id: 'abc', original_prompt: 'subject_definitions:\n\n<Subject 1> is NOVA, a cat.\n\nsummary:\n\n[reference generation] Immediate 15-second continuation of SHORT 1. More text.' };
  assert.equal(jobTitle(job), 'NOVA · Immediate 15-second continuation of SHORT 1.');
  assert.deepEqual(jobNames(job.original_prompt), ['NOVA']);
});

test('skips duration and picture lines and prefixes hero names', () => {
  const job = { job_id: 'abc', original_prompt: 'DURATION: EXACTLY 15 SECONDS\nPICTURE 1 — CHARACTER IDENTITY\n<Picture 1> defines ANNA.\nАнна танцует на крыше под дождём' };
  assert.equal(jobTitle(job), 'ANNA · Анна танцует на крыше под дождём');
});

test('falls back to the job id', () => {
  assert.equal(jobTitle({ job_id: '12345678abcd', original_prompt: 'subject_definitions:' }), 'Ролик 12345678');
});
