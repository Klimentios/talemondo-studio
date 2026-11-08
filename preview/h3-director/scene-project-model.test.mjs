import test from 'node:test';
import assert from 'node:assert/strict';
import { addScene, deleteScene, normalizeSceneProject, saveActiveScene, switchScene, renameScene, groupShots, renameShotGroup, ungroupShots, russianShotCount } from './scene-project-model.mjs';

test('shot counts use correct Russian noun forms', () => {
  assert.deepEqual([0, 1, 2, 4, 5, 11, 21, 24, 111].map(russianShotCount), [
    '0 шотов', '1 шот', '2 шота', '4 шота', '5 шотов', '11 шотов', '21 шот', '24 шота', '111 шотов',
  ]);
});

test('legacy draft migrates into one scene without losing its shots', () => {
  const project = normalizeSceneProject({ cuts: [{ id: 'cut-a', duration: 6, prompt: 'action' }], seed: '123', sceneDirector: { idea: 'legacy idea' } });
  assert.equal(project.scenes.length, 1);
  assert.equal(project.scenes[0].cuts[0].prompt, 'action');
  assert.equal(project.scenes[0].seed, '123');
  assert.equal(project.scenes[0].sceneDirector.idea, 'legacy idea');
});

test('scene switching saves each scene board and restores it on return', () => {
  const project = normalizeSceneProject({ cuts: [{ id: 'one', duration: 5, prompt: 'one' }], mode: 'realistic' });
  const first = project.activeSceneId;
  project.sceneDirector = { idea: 'first scene idea' };
  addScene(project);
  const second = project.activeSceneId;
  project.mode = 'animation3d';
  assert.deepEqual(project.sceneDirector, {});
  project.cuts[0].prompt = 'second scene shot'; saveActiveScene(project);
  project.sceneDirector = { idea: 'second scene idea' }; saveActiveScene(project);
  switchScene(project, first);
  assert.equal(project.cuts[0].prompt, 'one');
  assert.equal(project.sceneDirector.idea, 'first scene idea');
  assert.equal(project.mode, 'realistic');
  switchScene(project, second);
  assert.equal(project.cuts[0].prompt, 'second scene shot');
  assert.equal(project.sceneDirector.idea, 'second scene idea');
  assert.equal(project.mode, 'animation3d');
});

test('legacy franchise scenes recover their own visual mode', () => {
  const project = normalizeSceneProject({ mode: 'animation3d', activeSceneId: 'a', cuts: [{ id: 'a1', prompt: 'x' }],
    scenes: [{ id: 'a', title: 'cats', cuts: [{ id: 'a1', prompt: 'x' }], sceneStyle: 'Photoreal real cats' },
      { id: 'b', title: 'cartoon', cuts: [{ id: 'b1', prompt: 'y' }], sceneStyle: 'Premium stylized 3D animation' }] });
  assert.equal(project.mode, 'realistic');
  switchScene(project, 'b');
  assert.equal(project.mode, 'animation3d');
});

test('scene titles are editable and bounded', () => {
  const project = normalizeSceneProject({ cuts: [{ id: 'one', duration: 5, prompt: '' }] });
  renameScene(project, project.activeSceneId, 'Factory');
  assert.equal(project.scenes[0].title, 'Factory');
});

test('deleting a scene restores a neighbour and clearing the last scene leaves a fresh draft', () => {
  const project = normalizeSceneProject({ cuts: [{ id: 'old-shot', duration: 8, prompt: 'old action' }] });
  const oldId = project.activeSceneId;
  addScene(project);
  const newId = project.activeSceneId;
  project.cuts[0].prompt = 'new action';
  assert.equal(deleteScene(project, oldId), true);
  assert.equal(project.activeSceneId, newId);
  assert.equal(project.cuts[0].prompt, 'new action');
  assert.equal(deleteScene(project, newId), true);
  assert.equal(project.scenes.length, 1);
  assert.notEqual(project.activeSceneId, newId);
  assert.equal(project.cuts[0].prompt, '');
  assert.equal(deleteScene(project, 'missing'), false);
});

test('shot blocks group, rename and ungroup selected shots without changing order', () => {
  const project = normalizeSceneProject({ cuts: [
    { id: 'a', duration: 4, prompt: 'one' }, { id: 'b', duration: 5, prompt: 'two' }, { id: 'c', duration: 6, prompt: 'three' },
  ] });
  assert.equal(groupShots(project, ['a'], 'Opening'), null);
  assert.equal(groupShots(project, ['a', 'c'], 'Opening'), null);
  const id = groupShots(project, ['a', 'b'], 'Opening');
  assert.deepEqual(project.cuts.map((cut) => cut.id), ['a', 'b', 'c']);
  assert.deepEqual(project.cuts.map((cut) => cut.groupId), [id, id, undefined]);
  assert.equal(project.storyboard.shotGroups[id].title, 'Opening');
  assert.equal(renameShotGroup(project, id, 'Fight'), true);
  assert.equal(project.storyboard.shotGroups[id].title, 'Fight');
  assert.equal(ungroupShots(project, id), true);
  assert.equal(project.cuts.some((cut) => cut.groupId), false);
  assert.equal(project.storyboard.shotGroups[id], undefined);
});

test('reset scene leaves one empty shot and no materials, other scenes keep theirs', async () => {
  const { resetScene, normalizeSceneProject, switchScene } = await import('./scene-project-model.mjs');
  const project = normalizeSceneProject({ cuts: [{ id: 'a', duration: 8, prompt: 'shot', shotJobId: 'j1' }, { id: 'b', duration: 5, prompt: 'x' }],
    references: [{ id: 'r1', kind: 'image' }], sceneStyle: 'noir', storyboard: { running: true }, sceneDirector: { plan: {} } });
  project.sharedReferenceState = { references: [{ id: 'r1', kind: 'image' }] };
  project.scenes.push({ id: 'other', title: 'Сцена 02', cuts: [{ id: 'o', duration: 5, prompt: 'keep' }], sceneStyle: 's', seed: '', storyboard: {}, sceneDirector: {} });
  assert.equal(resetScene(project), true);
  assert.equal(project.cuts.length, 1);
  assert.equal(project.cuts[0].prompt, '');
  assert.deepEqual(project.references, []);
  assert.equal(project.sceneStyle, '');
  assert.deepEqual(project.storyboard, {});
  switchScene(project, 'other');
  assert.equal(project.cuts[0].prompt, 'keep');
  assert.deepEqual(project.references.map((ref) => ref.id), ['r1']);  // shared materials of the other scene stay
});

test('every scene owns its materials: a new scene is empty and never sees another scene\'s heroes', async () => {
  const { normalizeSceneProject, addScene, switchScene } = await import('./scene-project-model.mjs');
  const project = normalizeSceneProject({ cuts: [{ id: 'c1', prompt: 'ALEX', duration: 5 }],
    references: [{ id: 'alex', kind: 'image', number: 1, owner: 'ALEX' }], referenceCounters: { image: 1, video: 0, audio: 0 } });
  const firstScene = project.activeSceneId;
  addScene(project);
  assert.deepEqual(project.references, []);  // the IRIS & MIRA scene does not inherit ALEX
  project.references.push({ id: 'iris', kind: 'image', number: 1, owner: 'IRIS' });
  const secondScene = project.activeSceneId;
  switchScene(project, firstScene);
  assert.deepEqual(project.references.map((ref) => ref.id), ['alex']);
  switchScene(project, secondScene);
  assert.deepEqual(project.references.map((ref) => ref.id), ['iris']);
});

