import test from 'node:test';
import assert from 'node:assert/strict';
import {bindExplicitSubjects} from './reference-subjects.mjs';
const refs = [{kind:'image',number:1,role:'Лицо героя',owner:'NOVA'}, {kind:'image',number:2,role:'Костюм',owner:'NOVA'}];
const prompt = 'subject_definitions:\nNOVA is my original cat.\n<Picture 1> is the face reference for NOVA.\n<Picture 2> is the costume reference for NOVA.\n\nsummary:\nKeep the dance unchanged.';
test('explicit hero name and picture bindings survive older Studio validation', () => {
  const fixed=bindExplicitSubjects(prompt,refs);
  assert.match(fixed, /<Subject 1> is NOVA, whose face and identity come from <Picture 1> and whose costume and carried props come from <Picture 2>\./);
  assert.ok(fixed.endsWith(prompt.slice(prompt.indexOf('NOVA is'))));
  assert.equal(bindExplicitSubjects(fixed,refs),fixed);
});
test('mismatched or missing ownership is not repaired by file order', () => {
  assert.equal(bindExplicitSubjects(prompt,refs.map(r=>({...r,owner:'ORION'}))),prompt);
  assert.equal(bindExplicitSubjects(prompt.replace('for NOVA.','for ORION.'),refs),prompt.replace('for NOVA.','for ORION.'));
});
