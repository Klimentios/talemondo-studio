import assert from 'node:assert/strict';
import test from 'node:test';
import { heroNames, inferPerformerPairs, performerSpec } from './reference-pairing.mjs';

const references = [
  { kind: 'image', role: 'Лицо героя', owner: 'нова' },
  { kind: 'image', role: 'Костюм', owner: 'нова' },
  { kind: 'image', role: 'Лицо героя', owner: 'орион' },
  { kind: 'video', role: 'Движение', id: 'motion' },
];

test('explicit two-performer prompt maps Cyrillic Studio owners without guessing', () => {
  const prompt = 'REFERENCE PERFORMER A → NOVA.\nREFERENCE PERFORMER B → ORION.';
  assert.deepEqual(heroNames(references), ['нова', 'орион']);
  assert.deepEqual(performerSpec(references[3], references, prompt), {
    pairs: [{ owner: 'нова', performer: 'performer A' },
            { owner: 'орион', performer: 'performer B' }],
    keep_others: false,
  });
});

test('ambiguous or incomplete pairing is never invented', () => {
  assert.equal(inferPerformerPairs('PERFORMER A → NOVA.', heroNames(references)), null);
  assert.equal(inferPerformerPairs('PERFORMER A → NOVA.\nPERFORMER B → UNKNOWN.', heroNames(references)), null);
});

test('saved pairing wins over prompt inference', () => {
  const video = { ...references[3], performers: {
    pairs: [{ owner: 'нова', performer: 'person by the door' }], keep_others: true,
  } };
  assert.deepEqual(performerSpec(video, references, 'PERFORMER A → ORION.'), video.performers);
});

test('saved relative hero scale survives Studio pairing serialization', () => {
  const references = [
    {kind:'image',role:'Лицо героя',owner:'NOVA'},
    {kind:'image',role:'Лицо героя',owner:'ORION'},
  ];
  const video = {kind:'video',role:'Движение и камера',performers:{pairs:[
    {owner:'NOVA',performer:'left cat'},
    {owner:'ORION',performer:'right cat',relative_scale_percent:67},
  ]}};
  assert.equal(performerSpec(video,references,'').pairs[1].relative_scale_percent,67);
});

test('stale Cyrillic numbered name resolves to the current unique hero', () => {
  const refs = [{kind:'image',role:'Лицо героя',owner:'Anna'},
    {kind:'image',role:'Лицо героя',owner:'ADAM'}];
  const video = {kind:'video',role:'Исходное видео',performers:{pairs:[
    {owner:'Анна',performer:'first cat'}, {owner:'Адам 1',performer:'second cat'}]}};
  assert.deepEqual(performerSpec(video,refs,'').pairs,[
    {owner:'Anna',performer:'first cat'}, {owner:'ADAM',performer:'second cat'}]);
});
