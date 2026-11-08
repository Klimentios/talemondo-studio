import test from 'node:test';
import assert from 'node:assert/strict';
import { renderLightReferencePixels, lightReferenceKey } from './light-reference.mjs';
import { measurePixels } from './photo-analyze.mjs';

const render = (p) => measurePixels(renderLightReferencePixels(p, 192, 108));

test('side key light shows up on the side, warm temperature reads warm', () => {
  const m = render({ source: 'sun', direction: 'side', temperature: 'warm', contrast: 'high' });
  assert.ok(m.right > m.left, 'right side brighter');
  assert.ok(m.warmth > 0.06, 'warm');
});

test('contrast and key level change the image the way the preset says', () => {
  assert.ok(render({ contrast: 'high' }).range > render({ contrast: 'low' }).range);
  assert.ok(render({ key: 'high' }).mean > render({ key: 'low' }).mean);
  assert.ok(render({ temperature: 'cool' }).warmth < 0);
});

test('same parameters → same cache key', () => {
  assert.equal(lightReferenceKey({ source: 'sun', rim: true }), lightReferenceKey({ rim: true, source: 'sun' }));
  assert.notEqual(lightReferenceKey({ source: 'sun' }), lightReferenceKey({ source: 'neon' }));
});
