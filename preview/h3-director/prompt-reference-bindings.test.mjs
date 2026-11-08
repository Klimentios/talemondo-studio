import test from 'node:test';
import assert from 'node:assert/strict';
import {alignPromptPictures} from './prompt-reference-bindings.mjs';
import {bindExplicitSubjects} from './reference-subjects.mjs';
test('two named cats keep identities and outfits after attachment order changes',()=>{
 const refs=[{kind:'image',number:1,role:'Лицо героя',owner:'NOVA'},
 {kind:'image',number:2,role:'Костюм',owner:'NOVA'},
 {kind:'image',number:3,role:'Композиция',owner:''},
 {kind:'image',number:4,role:'Костюм',owner:'ORION'},
 {kind:'image',number:5,role:'Лицо героя',owner:'ORION'}];
 const p='subject_definitions:\nNOVA is a cat.\n<Picture 1> is the FACE reference for NOVA.\n<Picture 3> is the COSTUME reference for NOVA.\nORION is a cat.\n<Picture 2> is the FACE reference for ORION.\n<Picture 4> is the COSTUME reference for ORION.\n<Picture 5> is the ENVIRONMENT reference.\nsummary:\nNOVA face <Picture 1>, outfit <Picture 3>; ORION face <Picture 2>, outfit <Picture 4>; car <Picture 5>.';
 const fixed=alignPromptPictures(p,refs);
 assert.match(fixed,/NOVA face <Picture 1>, outfit <Picture 2>; ORION face <Picture 5>, outfit <Picture 4>; car <Picture 3>/);
 assert.equal(alignPromptPictures(fixed,refs),fixed);
 const ready=bindExplicitSubjects(fixed,refs);
 assert.match(ready,/<Subject 1> is NOVA/);assert.match(ready,/<Subject 2> is ORION/);
});

test('all 120 five-photo upload orders preserve hero face, costume and environment bindings',()=>{
 const sources=[{kind:'image',role:'Лицо героя',owner:'NOVA'},
 {kind:'image',role:'Лицо героя',owner:'ORION'},
 {kind:'image',role:'Костюм',owner:'NOVA'},
 {kind:'image',role:'Костюм',owner:'ORION'},
 {kind:'image',role:'Композиция',owner:''}];
 const prompt='subject_definitions:\nNOVA is a cat.\nORION is a cat.\n<Picture 1> is the FACE reference for NOVA.\n<Picture 2> is the FACE reference for ORION.\n<Picture 3> is the COSTUME reference for NOVA.\n<Picture 4> is the COSTUME reference for ORION.\n<Picture 5> is the ENVIRONMENT reference.\nsummary:\nFaceA <Picture 1>; FaceM <Picture 2>; OutfitA <Picture 3>; OutfitM <Picture 4>; Car <Picture 5>.';
 function permutations(items){return items.length?items.flatMap((v,i)=>permutations(items.filter((_,j)=>i!==j)).map(rest=>[v,...rest])):[[]];}
 let checked=0;
 for(const order of permutations(sources)){
  const refs=order.map((r,i)=>({...r,number:i+1}));
  const fixed=alignPromptPictures(prompt,refs);
  const expected=sources.map(source=>refs.find(r=>r.role===source.role&&r.owner===source.owner).number);
  assert.ok(fixed.endsWith(`FaceA <Picture ${expected[0]}>; FaceM <Picture ${expected[1]}>; OutfitA <Picture ${expected[2]}>; OutfitM <Picture ${expected[3]}>; Car <Picture ${expected[4]}>.`));
  assert.equal(alignPromptPictures(fixed,refs),fixed);checked++;
 }
 assert.equal(checked,120);
});


test('heading briefs align face and costume by selected owner even without subject_definitions',()=>{
 const prompt='REFERENCE ASSIGNMENT\n<Picture 1> — NOVA FACE AND IDENTITY\n<Picture 2> — NOVA COSTUME\n<Picture 3> — ORION FACE AND IDENTITY\n<Picture 4> — ORION COSTUME\nACTION\nNOVA wears <Picture 2>; ORION face <Picture 3>.';
 const refs=[{kind:'image',number:1,role:'Лицо героя',owner:'NOVA'},{kind:'image',number:2,role:'Лицо героя',owner:'ORION'},{kind:'image',number:3,role:'Костюм',owner:'NOVA'},{kind:'image',number:4,role:'Костюм',owner:'ORION'}];
 const fixed=alignPromptPictures(prompt,refs);
 assert.ok(fixed.endsWith('NOVA wears <Picture 3>; ORION face <Picture 2>.'));
});
