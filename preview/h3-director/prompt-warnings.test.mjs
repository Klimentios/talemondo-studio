import test from 'node:test';
import assert from 'node:assert/strict';
import { nameKey, ownerMentioned, promptWarnings, PROMPT_SOFT_LIMIT } from './prompt-warnings.mjs';

test('owner names match across Cyrillic and Latin spellings and Russian endings', () => {
  assert.equal(nameKey('Нова'), nameKey('NOVA'));
  assert.ok(ownerMentioned('нова входит в комнату', 'NOVA'));
  assert.ok(ownerMentioned('NOVA walks in', 'Нова'));
  assert.ok(ownerMentioned('Орион смотрит на Нову', 'NOVA'));
  assert.ok(ownerMentioned('ORION waits', 'Орион'));
  assert.ok(ownerMentioned('Алиса улыбается', 'ALICE'));
  assert.ok(!ownerMentioned('ORION walks alone', 'NOVA'));
  assert.ok(!ownerMentioned('Казанова ждёт', 'NOVA'), 'a name inside another word is not a mention');
});

test('prompt warnings are non-blocking notes for missing owners and very long prompts', () => {
  const refs = [{ owner: 'NOVA' }, { owner: 'ORION' }, { owner: '' }];
  assert.deepEqual(promptWarnings('нова и Орион спорят', refs), []);
  const [missing] = promptWarnings('NOVA sleeps', refs);
  assert.match(missing, /не упомянут ORION/);
  const long = promptWarnings('NOVA ORION ' + 'x'.repeat(PROMPT_SOFT_LIMIT), refs);
  assert.equal(long.length, 1);
  assert.match(long[0], /350–500 слов/);
  assert.match(promptWarnings('NOVA', refs, { en: true })[0], /never names ORION/);
  assert.deepEqual(promptWarnings('', refs), []);
});
