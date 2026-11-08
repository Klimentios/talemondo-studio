import test from 'node:test';
import assert from 'node:assert/strict';
// Same specifier as the Studio modules import, so the test shares their list.
import { aspectOptions, aspects, loadAspects, nearestAspect, selectAspect, setAspects } from './aspects.mjs?v=20261106-aspects-1';
import { effectiveResolution } from './model.mjs?v=20261106-refmatch';
import { recipeFormSettings } from './agent-draft-model.mjs?v=20261106-aspects-1';
import { buildSheet, SHEET_DEFAULTS } from './edit-sheet-model.mjs?v=20261106-aspects-1';

const GPU = ['16:9', '9:16', '4:3', '3:4', '1:1']; // app/contract.py ASPECTS as /api/capabilities reports them

test('before the GPU answers only the pair every API accepts is offered; 9:16 is the default', () => {
  assert.deepEqual(aspects(), ['9:16', '16:9']);
  assert.equal(SHEET_DEFAULTS.aspect_ratio, '9:16');
});

test('the list comes from /api/studio/capabilities, STUDIO vertical first', async () => {
  const calls = [];
  const list = await loadAspects(async (url) => { calls.push(url); return { ok: true, json: async () => ({ aspect_ratios: GPU }) }; });
  assert.deepEqual(calls, ['/api/studio/capabilities']);
  assert.deepEqual(list, ['9:16', '16:9', '4:3', '3:4', '1:1']);
  assert.deepEqual(aspectOptions().map(([value]) => value), list);
  // a saved value the GPU does not list stays visible and says so
  assert.match(aspectOptions('21:9').at(-1)[1], /нет на GPU/);
});

test('4:3 resolves to the same canvas as the GPU contract', () => {
  assert.deepEqual(effectiveResolution('4:3', 'custom', 0.8), { width: 1056, height: 800, pixels: 844800, selectorMp: 0.8 });
  assert.deepEqual(effectiveResolution('3:4', 'custom', 0.8), { width: 800, height: 1056, pixels: 844800, selectorMp: 0.8 });
  assert.throws(() => effectiveResolution('wide', 'quick'), /Недопустимое/);
});

test('an agent recipe keeps its own format: a 4:3 source stays 4:3', () => {
  setAspects(GPU);
  assert.equal(recipeFormSettings({ width: 1056, height: 800 }).aspect_ratio, '4:3');
  assert.equal(recipeFormSettings({ width: 640, height: 1152 }).aspect_ratio, '9:16');
  assert.equal(nearestAspect(1024, 1024), '1:1');
  assert.equal(nearestAspect(undefined, undefined), null);
});

test('the edit sheet carries 4:3 instead of folding it into 9:16', () => {
  const cut = (id, job) => ({ id, duration: 5, shotJobId: job, shotQcReview: 'accepted', shotQc: { job_id: job, decision: 'accept' } });
  const { ready, body } = buildSheet([{ title: 'Кухня', cuts: [cut('a', 'ja')] }], new Map([['ja', { state: 'completed' }]]),
    { ...SHEET_DEFAULTS, aspect_ratio: '4:3' });
  assert.ok(ready);
  assert.equal(body.aspect_ratio, '4:3');
});

test('an agent draft can set a format the select does not have yet', () => {
  const options = [];
  const select = { options, value: '9:16', ownerDocument: { createElement: () => ({}) }, append(o) { options.push(o); } };
  assert.equal(selectAspect(select, '4:3'), true);
  assert.equal(select.value, '4:3');
  assert.equal(options[0].value, '4:3');
  assert.equal(selectAspect(select, 'nonsense'), false);
});
