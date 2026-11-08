import test from 'node:test';
import assert from 'node:assert/strict';
import {applyBoardOperations, applyDirectorRecommendations, checkScenePlan, cutsFromScenePlan, directorReview, planForReview} from './scene-director-model.mjs';

test('a generated plan is saved for review without marking or applying it', () => {
  const current = { idea: 'existing scene', appliedAt: 'previous', previousCuts: [{ id: 'cut-1' }] };
  const plan = { shots: [{ title: 'new shot' }] };
  const pending = planForReview(current, plan, 'revise the chase', 24);
  assert.equal(pending.idea, 'revise the chase');
  assert.equal(pending.targetSeconds, 24);
  assert.equal(pending.plan, plan);
  assert.equal(pending.appliedAt, null);
  assert.deepEqual(pending.previousCuts, current.previousCuts);
  assert.equal(current.plan, undefined);
});

const shot = (index, frame = 'new_keyframe') => ({title:`Shot ${index}`,purpose:'Поворот истории',action:'The hero turns toward the door and reaches for the handle.',
  first_frame:'Дверь закрыта',characters:[],duration:10,
  framing:'medium',lens_mm:35,camera_motion:'Статичная камера',start_frame:frame,state_before:'Дверь закрыта',state_after:'Дверь открыта'});

test('validated plan becomes editable storyboard with explicit start-frame links', () => {
  const plan = {shots:[shot(1),shot(2,'previous_end'),shot(3),shot(4),shot(5)]};
  assert.equal(checkScenePlan(plan,50),50);
  const cuts = cutsFromScenePlan(plan,[{id:'cut-7'}]);
  assert.equal(cuts[0].id,'cut-8');
  assert.equal(cuts[1].startFrame.source,'previous');
  assert.equal(cuts[2].startFrame.source,'keyframe');
  assert.equal(cuts[0].prompt, plan.shots[0].action);
  assert.equal(cuts[0].sceneShot.first_frame, 'Дверь закрыта');
  assert.equal(cuts[0].sceneShot.action, plan.shots[0].action);
});

test('rejects a long continuation and detects repetitive framing', () => {
  const long = {shots:[shot(1),shot(2,'previous_end'),shot(3,'previous_end'),shot(4,'previous_end'),shot(5,'previous_end')]};
  assert.throws(() => checkScenePlan(long,50),/цепь/);
  assert.ok(directorReview(long).some((note) => note.type === 'framing'));
  assert.ok(directorReview(long).some((note) => note.type === 'continuity'));
  const fixed = applyDirectorRecommendations(long);
  assert.equal(checkScenePlan(fixed,50),50);
  assert.equal(fixed.shots[4].start_frame,'new_keyframe');
  fixed.shots[0].state_after = fixed.shots[0].state_before;
  assert.throws(() => checkScenePlan(fixed,50),/состояния/);
  fixed.shots[0].state_after = 'Дверь открыта';
  fixed.shots[0].action = 'Герой смотрит на дверь';
  assert.throws(() => checkScenePlan(fixed,50),/английском/);
});

test('board operations preserve untouched shots, insert after anchors, and update only requested fields', () => {
  const cuts = [{id:'cut-1',prompt:'Вход героя',duration:4,startFrame:{source:'keyframe',asset_id:'asset-a'},sceneShot:{title:'Вход'}},
    {id:'cut-2',prompt:'Смотрит на дверь',duration:5,sceneShot:{title:'Реакция'}}];
  const next = applyBoardOperations(cuts,[
    {op:'add_after',after_shot_id:'cut-1',shot:{title:'Крупная реакция',action:'The hero turns sharply.',duration:10,framing:'close',lens_mm:85,camera_motion:'Наезд',start_frame:'previous_end'}},
    {op:'update',shot_id:'cut-2',changes:{camera_motion:'tracking',action:'The hero reaches the door.'}}
  ]);
  assert.deepEqual(next.map((cut)=>cut.id),['cut-1','cut-3','cut-2']);
  assert.deepEqual(next[0],cuts[0]);
  assert.equal(next[1].startFrame.source,'previous');
  assert.equal(next[2].prompt,'The hero reaches the door.');
  assert.deepEqual(next[2].sceneShot,{title:'Реакция',camera_motion:'tracking',action:'The hero reaches the door.'});
  assert.equal(cuts[1].prompt,'Смотрит на дверь');
});

test('board operation rejects unknown anchors and empty plans', () => {
  assert.throws(()=>applyBoardOperations([{id:'cut-1'}],[{op:'add_after',after_shot_id:'missing',shot:{}}]),/вставки/);
  assert.throws(()=>applyBoardOperations([{id:'cut-1'}],[]),/Нет шотов/);
});
