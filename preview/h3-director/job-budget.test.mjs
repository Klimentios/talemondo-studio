import test from 'node:test';
import assert from 'node:assert/strict';

test('job card shows the packed H3 sequence estimate', async () => {
  const { jobBudgetText } = await import('./job-feed.mjs');
  const job = { budget_json: JSON.stringify({ total_rows: 143692, video_rows: 68290 }) };
  assert.equal(jobBudgetText(job), ' · ≈144k токенов · исходник 68k');
  assert.equal(jobBudgetText({ budget_json: JSON.stringify({ total_rows: 68000, video_rows: 0 }) }, true), ' · ≈68k tokens');
  assert.equal(jobBudgetText({}), '');
  assert.equal(jobBudgetText({ budget_json: '{bad' }), '');
});
