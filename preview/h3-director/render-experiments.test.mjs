import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderSettings, restoreRenderSettings } from './render-experiments.mjs';

test('baseline request omits optional settings; experimental profile keeps cache outside overrides', () => {
  assert.deepEqual(renderSettings({profile:'bf16',cacheMode:'fast'}),{});
  assert.deepEqual(renderSettings({profile:'int8_full',cacheMode:'safe',renderOptions:{adapter:'orb360',strength:0.8}}),
    {profile:'int8_full',render_options:{adapter:'orb360',strength:0.8}});
});
test('replay and reset clear old experiments without changing cache', () => {
  const project = {profile:'int8_full',renderOptions:{attention:'sol-attn'},cacheMode:'fast'};
  restoreRenderSettings(project,{});
  assert.deepEqual(project,{profile:'bf16',renderOptions:{},cacheMode:'fast'});
});
test('invalid strength is refused before submission', () => {
  assert.throws(() => renderSettings({renderOptions:{adapter:'orb360',strength:NaN}}));
});
test('native context is opt-in and unavailable heads do not change old board state', async () => {
  const { storyboardState } = await import('./storyboard-model.mjs');
  assert.equal(storyboardState({}).motionContext,false);
  assert.equal(storyboardState({storyboard:{motionContext:true}}).motionContext,true);
});
