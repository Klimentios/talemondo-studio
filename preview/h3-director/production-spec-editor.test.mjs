import test from 'node:test';
import assert from 'node:assert/strict';
import { revisedSpec } from './production-spec-editor.mjs';
import { shotStatus } from './storyboard-model.mjs';

test('editing staging preserves identity bindings and evidence with independent revisions',()=>{
 const original={characters:[{id:'Milo',revision:3,reference_asset_ids:['a'],individual_behavior:'Curious'}],
 behavior_patterns:[{id:'p1',source:'reference',evidence:['frame'],status:'verified'}],
 shot:{revision:2,duration:5,beats:[{id:'b1',actor_ids:['Milo'],start:0,end:3,behavior_pattern_ids:['p1']}]}};
 const next=revisedSpec(original,{shot:{purpose:'Notice'},characters:[{individual_behavior:'Cautious'}],beats:original.shot.beats},6);
 assert.equal(next.shot.revision,3);assert.equal(next.characters[0].revision,4);
 assert.deepEqual(next.characters[0].reference_asset_ids,['a']);assert.deepEqual(next.behavior_patterns,original.behavior_patterns);
 assert.equal(original.shot.duration,5);assert.equal(original.characters[0].revision,3);
});
test('finished video becomes stale after staging edit',()=>{
 const cuts=[{id:'c',shotJobId:'j1',productionSpecChanged:true}];
 assert.equal(shotStatus(cuts,0,new Map([['j1',{state:'completed'}]])),'stale');
});
