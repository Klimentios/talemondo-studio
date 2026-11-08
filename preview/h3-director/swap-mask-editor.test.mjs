import test from 'node:test';
import assert from 'node:assert/strict';
import { clampBox, swapMaskButton, swapMaskLabel, swapMaskSetting } from './swap-mask-editor.mjs';

test('a drawn box is clamped to the frame in either drag direction', () => {
  assert.deepEqual(clampBox(0.8, 0.9, 0.2, 0.1), [0.2, 0.1, 0.6, 0.8]);
  assert.deepEqual(clampBox(-0.2, 0.5, 0.4, 1.3), [0, 0.5, 0.4, 0.5]);
});

test('only a replacement job sends the source video mask of its paired heroes and excerpt', () => {
  const mask = { performers: [{ name: 'NOVA', box: [0.1, 0.4, 0.3, 0.5] }], grow: 24, at: 0 };
  const pairs = { pairs: [{ owner: 'NOVA', performer: 'the cat at the table' }] };
  const refs = [{ kind: 'image', role: 'Персонаж целиком' }, { kind: 'video', role: 'Исходное видео', swapMask: mask, performers: pairs }];
  // Drawing the boxes by hand is the user's A/B request (local_studio/store.py require_ab_request).
  assert.deepEqual(swapMaskSetting({ mode: 'replace', references: refs }), { swap_mask: { performers: mask.performers, grow: 24 },
    ab_user_request: 'Studio: пользователь нарисовал маску замены в форме' });
  assert.deepEqual(swapMaskSetting({ mode: 'replace', references: refs }, 10), {});  // drawn for another shot
  const unpaired = [refs[0], { ...refs[1], performers: { pairs: [{ owner: 'ALEX', performer: 'the man' }] } }];
  assert.deepEqual(swapMaskSetting({ mode: 'replace', references: unpaired }), {});
  assert.equal(swapMaskButton({ kind: 'video', role: 'Исходное видео', name: 'v' }, { mode: 'replace' }), null);  // no pairs yet
  assert.equal(swapMaskLabel(refs[1], 10), 'Маска для другого шота');
  assert.equal(swapMaskLabel(refs[1], 0), '✓ маска · 1');
  assert.deepEqual(swapMaskSetting({ mode: 'animation3d', references: refs }), {});
  assert.deepEqual(swapMaskSetting({ mode: 'replace', references: [refs[0]] }), {});
});
