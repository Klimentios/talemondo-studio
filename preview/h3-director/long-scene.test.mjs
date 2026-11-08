import test from 'node:test';
import assert from 'node:assert/strict';
import { sceneWindows, prepareShotReferences, openLongScene, sourceSceneCuts } from './long-scene.mjs';

test('shots take successive source ranges and retain the hero and upload order', async () => {
  const refs = [{ id: 'face', kind: 'image', number: 1, owner: 'Anna' },
    { id: 'video', kind: 'video', number: 1, owner: 'Anna', role: 'Движение', sourceDuration: 35 }];
  const cuts = sceneWindows([{ duration: 15 }, { duration: 15 }, { duration: 5 }], refs);
  assert.deepEqual(cuts.map(cut => cut.referenceWindow.start), [0, 15, 30]);
  const made = await prepareShotReferences(refs, cuts[1].referenceWindow, async (url, options) => {
    assert.equal(url, '/api/assets/video/segment');
    assert.deepEqual(JSON.parse(options.body), { start: 15, duration: 15 });
    return { asset_id: 'excerpt', original_name: '15–30' };
  });
  assert.equal(made[0], refs[0]);
  assert.equal(made[1].id, 'excerpt');
  assert.equal(made[1].owner, 'Anna');
  assert.equal(made[1].number, 1);
  assert.equal(refs[1].id, 'video');
});

test('long duration saves a scene brief before navigation without replacing existing shots', async () => {
  const project = { cuts: [{ id: 'original', duration: 5, prompt: 'Story' }] };
  const events = [];
  assert.equal(await openLongScene({ seconds: 35, project, prompt: 'Story',
    save: async () => events.push('save'), navigate: url => events.push(url) }), true);
  assert.deepEqual(events, ['save', '/?view=scenes#scene-idea']);
  assert.equal(project.sceneDirector.targetSeconds, 35);
  assert.equal(project.cuts[0].id, 'original');
});

test('source plan creates shots automatically without missing keyframes, keeps bindings and scopes action', () => {
  const prompt = 'subject_definitions:\nAnna uses <Picture 1>, ALEX uses <Picture 3>.\ndetailed_description:\nReplace the cats.';
  const cuts = sourceSceneCuts({shots:[{duration:15,action:'First action'},{duration:7,action:'Final action'}]}, prompt,
    [{id:'cut-4'}], [{id:'v',kind:'video',role:'Исходное видео',sourceDuration:22.25}]);
  assert.deepEqual(cuts.map(cut => cut.referenceWindow.start), [0,15]);
  assert.ok(cuts.every(cut => cut.startFrame === null && cut.prompt.includes('<Picture 3>')));
  assert.ok(cuts[1].prompt.includes('Final action'));
  assert.equal(cuts[0].id, 'cut-5');
});
test('removed source is rejected before segmentation', async () => {
  await assert.rejects(prepareShotReferences([], {assetIds:['missing']}, () => {throw Error('must not call');}), /удалено/);
});
