import test from 'node:test';
import assert from 'node:assert/strict';
import { removePattern } from './production-behavior-editor.mjs';
import { revisedSpec } from './production-spec-editor.mjs';

test('referenced behavior cannot be deleted until unbound from the shot',()=>{
 const patterns=[{id:'p1'}];
 assert.throws(()=>removePattern(patterns,'p1',[{behavior_pattern_ids:['p1']}]),/Сначала убери паттерн/);
 assert.deepEqual(removePattern(patterns,'p1',[]),[]);
 assert.equal(patterns.length,1);
});
test('new behavior evidence is saved by value without mutating the previous version',()=>{
 const original={characters:[],behavior_patterns:[],shot:{revision:1,beats:[]}};
 const patterns=[{id:'p1',status:'draft',source:'observed clip',evidence:['00:01–00:02']}];
 const next=revisedSpec(original,{shot:{},characters:[],beats:[],behaviorPatterns:patterns},5);
 patterns[0].source='changed';
 assert.equal(next.behavior_patterns[0].source,'observed clip');
 assert.deepEqual(original.behavior_patterns,[]);
});
