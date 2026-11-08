import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { referenceOwnerControlState, referenceOwnerLabels } from './reference-owners.mjs';

test('two face and costume pairs are not silently assigned by upload order', () => {
  const refs = ['Лицо героя', 'Костюм', 'Лицо героя', 'Костюм']
    .map((role, index) => ({ id: String(index), kind: 'image', role }));
  const labels = referenceOwnerLabels(refs, 'subject_definitions:\n<Subject 1> is Anna, x.\n<Subject 2> is Alex, y.');
  assert.deepEqual(refs.map((ref) => labels.get(ref.id)), [undefined, undefined, undefined, undefined]);
});

test('a single prompt hero remains selectable for face, costume and motion', async () => {
  const { referenceOwnerControlState } = await import('./reference-owners.mjs');
  const prompt = 'subject_definitions:\n\n<Subject 1> is MY ORIGINAL CHARACTER.';
  for (const role of ['Лицо героя', 'Костюм', 'Движение']) {
    const state = referenceOwnerControlState({ role }, prompt);
    assert.equal(state.kind, 'select');
    assert.deepEqual(state.names, ['MY ORIGINAL CHARACTER']);
    assert.equal(state.value, 'MY ORIGINAL CHARACTER');
  }
});

test('picture-only NOVA brief binds face, costume and movement to one hero', async () => {
  const { referenceOwnerControlState } = await import('./reference-owners.mjs');
  const prompt = '<Picture 1> is the exact facial identity and anatomy reference for NOVA.\n'
    + '<Picture 2> is the exact costume reference for NOVA.\n'
    + '<Video 1> supplies the choreography.';
  for (const role of ['Лицо героя', 'Костюм', 'Движение и камера']) {
    const state = referenceOwnerControlState({ role }, prompt);
    assert.deepEqual(state, { kind: 'select', names: ['NOVA'], value: 'NOVA' });
  }
  const refs = ['Лицо героя', 'Костюм', 'Движение и камера']
    .map((role, index) => ({ id: String(index), kind: index === 2 ? 'video' : 'image', role }));
  assert.deepEqual(refs.map((ref) => referenceOwnerLabels(refs, prompt).get(ref.id)), ['NOVA', 'NOVA', 'NOVA']);
});

test('plain subject definition and picture binding expose both named heroes', async () => {
  const { subjectNames } = await import('./reference-owners.mjs');
  assert.deepEqual(subjectNames('subject_definitions:\nORION is my tabby cat.\n'
    + '<Picture 1> is the face reference for NOVA.\nsummary:\nThe brothers walk.'),
  ['ORION', 'NOVA']);
});

test('explicit unassigned owner and multi-hero ambiguity stay unassigned', async () => {
  const { referenceOwnerControlState } = await import('./reference-owners.mjs');
  assert.equal(referenceOwnerControlState({ manualOwner: true, owner: '' }, '<Subject 1> is LEO.').value, '');
  assert.equal(referenceOwnerControlState({ owner: 'LEO' }, '<Subject 1> is LEO.\n<Subject 2> is NOVA.').value, '');
});

test('existing manual name is preserved with one hero and without prompt definitions', async () => {
  const { referenceOwnerControlState } = await import('./reference-owners.mjs');
  const ref = { manualOwner: true, owner: 'LEO' };
  assert.deepEqual(referenceOwnerControlState(ref, '<Subject 1> is MY ORIGINAL CHARACTER.'),
    { kind: 'select', names: ['MY ORIGINAL CHARACTER', 'LEO'], value: 'LEO' });
  assert.deepEqual(referenceOwnerControlState(ref, 'LEO walks.'), { kind: 'select', names: ['LEO'], value: 'LEO' });
});


test('reference assignment headings expose NOVA ORION and KAI without six-section syntax', async () => {
 const {subjectNames,referenceOwnerControlState}=await import('./reference-owners.mjs');
 const prompt='TITLE: MUSEUM ESCAPE\nREFERENCE ASSIGNMENT\n<Picture 1> — NOVA FACE AND IDENTITY\n<Picture 2> — NOVA COSTUME\n<Picture 3> — ORION FACE AND IDENTITY\n<Picture 4> — ORION COSTUME\n<Picture 5> — WAITING VEHICLE\n<Picture 6> — KAI FACE AND IDENTITY';
 assert.deepEqual(subjectNames(prompt),['NOVA','ORION','KAI']);
 assert.equal(referenceOwnerControlState({role:'Костюм'},prompt).kind,'select');
});


test('named driver in prose remains selectable without inventing a reference assignment', async()=>{
 const {subjectNames,referenceOwnerControlState}=await import('./reference-owners.mjs');
 const prompt='<Picture 1> — NOVA FACE AND IDENTITY\n<Picture 2> — ORION FACE AND IDENTITY\nKAI is seated behind the steering wheel.';
 assert.deepEqual(subjectNames(prompt),['NOVA','ORION','KAI']);
 assert.equal(referenceOwnerControlState({role:'Лицо героя'},prompt).value,'');
});

test('one hero in the text does not take over another hero\'s saved picture', () => {
  const refs = [{ id: 'a', role: 'Персонаж целиком', owner: 'NOVA' }, { id: 'm', role: 'Персонаж целиком', owner: 'ORION' },
    { id: 'x', role: 'Лицо героя', owner: '' }];
  const labels = referenceOwnerLabels(refs, '<Picture 1> NOVA walks to <Picture 2> ORION and taps his shoulder.');
  assert.equal(labels.get('a'), 'NOVA');
  assert.equal(labels.has('m'), false);
  assert.equal(labels.get('x'), 'NOVA');
  assert.equal(referenceOwnerControlState(refs[1], '<Picture 1> NOVA walks.').value, 'ORION');
});
