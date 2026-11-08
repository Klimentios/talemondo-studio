import test from 'node:test';
import assert from 'node:assert/strict';
import { setChatStartFrame } from './chat-image-handoff.mjs';

test('chat image goes only to its named shot and requires fresh acceptance', async () => {
  let cuts = [{ id: 'shot', frameAcceptance: { accepted: true } }, { id: 'other' }];
  let saved = false;
  const studio = { sceneState: () => ({ activeSceneId: 'scene' }), cuts: () => cuts,
    editCuts: (edit) => { cuts = edit(cuts); }, saveDraft: async () => { saved = true; } };
  const storyboard = { setStartFrame: (id, asset) => { cuts[0].startFrame = { asset_id: asset.asset_id }; } };
  const args = { studio, storyboard, sceneId: 'scene', shotId: 'shot', assetId: 'image',
    fetcher: async () => ({ ok: true, json: async () => [{ asset_id: 'image', kind: 'image' }] }) };
  assert.equal((await setChatStartFrame(args)).accepted, false);
  assert.equal(cuts[0].startFrame.asset_id, 'image');
  assert.equal(cuts[0].frameAcceptance, undefined);
  assert.deepEqual(cuts[1], { id: 'other' });
  assert.equal(saved, true);
  await assert.rejects(setChatStartFrame({ ...args, sceneId: 'wrong' }), /другая сцена/);
  cuts[0].shotJobId = 'running';
  await assert.rejects(setChatStartFrame(args), /уже есть работа/);
});
