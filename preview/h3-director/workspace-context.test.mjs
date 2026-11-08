import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { shotSummary } from './workspace-context.mjs';

test('shot summary uses the authored target after a technical prompt header', () => {
  const prompt = 'DURATION: EXACTLY 15 SECONDS\nFORMAT: 9:16\nFINAL TARGET:\n\nTwo cats enter the store.\nMore detail.';
  assert.equal(shotSummary(prompt), 'Two cats enter the store.');
});

test('shot summary keeps a short natural description', () => {
  assert.equal(shotSummary('A cat crosses the room.\nThen it stops.'), 'A cat crosses the room.');
});

test('official H3 prompt shows its summary rather than subject_definitions', () => {
  const prompt = 'subject_definitions:\n<Subject 1> is ANNA.\n\nsummary:\n[reference generation] ANNA dances in one shot.\n\nretention_analysis:\n<Subject 1>: fully_preserved.';
  assert.equal(shotSummary(prompt), 'ANNA dances in one shot.');
});
