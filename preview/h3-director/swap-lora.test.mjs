import test from 'node:test';
import assert from 'node:assert/strict';
import { swapLoraAdapter, swapLoraSetting } from './swap-lora.mjs';

test('V1 stays the default; V1.5 is sent only for a replacement job', () => {
  assert.deepEqual(swapLoraSetting({ mode: 'replace', swapLora: 'v1' }), {});
  // Picking V1.5 by hand is the user's A/B request (local_studio/store.py require_ab_request).
  assert.deepEqual(swapLoraSetting({ mode: 'replace', swapLora: 'v1.5' }),
    { swap_lora: 'v1.5', ab_user_request: 'Studio: пользователь выбрал Character Swap V1.5 в форме' });
  assert.deepEqual(swapLoraSetting({ mode: 'animation3d', swapLora: 'v1.5' }), {});
  assert.equal(swapLoraAdapter({}), 'character_swap');
  assert.equal(swapLoraAdapter({ swapLora: 'v1.5' }), 'character_swap_v15');
});
