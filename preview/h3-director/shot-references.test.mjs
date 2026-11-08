import test from 'node:test';
import assert from 'node:assert/strict';
import { namesHero, normalizeLegacyIr, placeNote, placeOff, pruneAbsentHeroMetadata, renumberTags, shotPlace, shotReferences } from './shot-references.mjs';

const refs = [
  { id: 'nova', kind: 'image', role: 'Лицо героя', owner: 'NOVA', number: 1 },
  { id: 'orion', kind: 'image', role: 'Лицо героя', owner: 'ORION', number: 2 },
  { id: 'coat', kind: 'image', role: 'Костюм', owner: 'ORION', number: 3 },
  { id: 'garage', kind: 'image', role: 'Окружение', owner: '', number: 4 },
  { id: 'music', kind: 'audio', role: 'Музыка', owner: '', number: 1 },
];

test('a hero who is not in the shot leaves with their costume; the rest is renumbered', () => {
  const result = shotReferences(refs, 'Close-up: NOVA looks at the money in <Picture 4>.');
  assert.deepEqual(result.references.map((ref) => [ref.id, ref.number]), [['nova', 1], ['garage', 2], ['music', 1]]);
  assert.deepEqual(result.dropped.map((ref) => ref.id), ['orion', 'coat']);
  assert.equal(renumberTags('NOVA in <Picture 4>, face <Picture 1>', result.renumber), 'NOVA in <Picture 2>, face <Picture 1>');
});

test('nothing is dropped when unsure', () => {
  assert.equal(shotReferences(refs, 'The garage is empty.').references, refs, 'no hero named');
  assert.equal(shotReferences(refs.filter((ref) => ref.owner !== 'ORION'), 'NOVA waits').dropped.length, 0, 'one hero only');
  assert.equal(shotReferences(refs, 'NOVA waits, <Picture 2> watches').dropped.length, 0, 'tagged picture stays');
  const voiced = [...refs, { id: 'v', kind: 'audio', role: 'Голос', owner: 'ORION', number: 2 }];
  assert.equal(shotReferences(voiced, 'NOVA waits').dropped.length, 0, 'a voiced hero keeps the face the store needs');
});

test('names match whole words in any case, Cyrillic too', () => {
  assert.equal(namesHero('nova opens the door', 'NOVA'), true);
  assert.equal(namesHero('PARISIAN cafe', 'NOVA'), false);
  assert.equal(namesHero('Марк входит', 'Марк'), true);
  assert.equal(namesHero('Орион входит', 'Марк'), false);
});

test('renumbering is one pass', () => {
  const map = new Map([['<Picture 3>', '<Picture 2>'], ['<Picture 2>', '<Picture 1>']]);
  assert.equal(renumberTags('<Picture 3> and <Picture 2>', map), '<Picture 2> and <Picture 1>');
  assert.equal(renumberTags('Pictures 1-4; Picture 3 and Picture 2', map), 'Pictures 1-4; Picture 2 and Picture 1');
});

test('an explicit production identity reference is retained even when absent from the brief', () => {
 const refs=[{id:'a',kind:'image',role:'Персонаж целиком',owner:'Nova',number:1},
             {id:'b',kind:'image',role:'Персонаж целиком',owner:'Orion',number:2}];
 assert.equal(shotReferences(refs,'Nova watches').references.length,1);
 assert.equal(shotReferences(refs,'Nova watches',['b']).references.length,2);
});

test('declared cast controls every hero reference and rejects contradictory prompt tags', () => {
 const voiced=[...refs,{id:'voice',kind:'audio',role:'Голос',owner:'ORION',number:2}];
 const result=shotReferences(voiced,'A silent reaction',[],['NOVA']);
 assert.deepEqual(result.references.map(ref=>ref.id),['nova','garage','music']);
 assert.deepEqual(result.dropped.map(ref=>ref.id),['orion','coat','voice']);
 assert.deepEqual(shotReferences(refs,'An empty room',[],[]).references.map(ref=>ref.id),['garage','music']);
 assert.throws(()=>shotReferences(refs,'<Picture 2> appears',[],['NOVA']),/ссылается на ORION/);
 assert.throws(()=>shotReferences(refs,'NOVA reacts',[],['UNKNOWN']),/нет фото героев/);
});

test('legacy IR metadata drops absent hero references but action keeps its guard', () => {
  const prompt = 'subject_definitions: <Picture 1> NOVA face; <Picture 2> ORION face; <Picture 4> garage.\n'
    + 'retention_analysis: <Picture 1> fully_preserved; <Picture 2> fully_preserved.\n'
    + 'detailed_description: NOVA looks around.';
  const cleaned = pruneAbsentHeroMetadata(prompt, refs, ['NOVA']);
  assert.equal(cleaned.includes('<Picture 2>'), false);
  assert.deepEqual(shotReferences(refs, cleaned, [], ['NOVA']).dropped.map(ref => ref.id), ['orion', 'coat']);
  assert.throws(() => shotReferences(refs, pruneAbsentHeroMetadata(prompt + ' <Picture 2> enters.', refs, ['NOVA']), [], ['NOVA']), /ссылается/);
});

test('absent hero metadata is removed as whole clauses, without broken sentences', () => {
  const prompt = 'subject_definitions:\nNOVA from <Picture 1>; ORION from <Picture 2>; preserve face from <Picture 2>.\n\n'
    + 'retention_analysis:\nKeep <Picture 1>; keep <Picture 2>.\n\n'
    + 'detailed_description:\nNOVA walks past Picture 4.';
  const cleaned = pruneAbsentHeroMetadata(prompt, refs, ['NOVA']);
  assert.match(cleaned, /subject_definitions:\nNOVA from <Picture 1>/);
  assert.doesNotMatch(cleaned, /ORION from|preserve face from|keep \.|;\s*;/i);
  const cast = shotReferences(refs, cleaned, [], ['NOVA']);
  assert.match(renumberTags(cleaned, cast.renumber), /NOVA walks past Picture 2\./);
  assert.throws(() => shotReferences(refs, 'NOVA watches Picture 2 enter.', [], ['NOVA']), /ссылается/);
});

test('legacy plain prompt drops absent identity tags before shot action', () => {
  const prompt = '[Identity] <Picture 1> NOVA face; <Picture 2> ORION face. [Shot 1] NOVA turns.';
  const cleaned = pruneAbsentHeroMetadata(prompt, refs, ['NOVA']);
  assert.equal(cleaned.includes('<Picture 2>'), false);
  assert.doesNotThrow(() => shotReferences(refs, cleaned, [], ['NOVA']));
  assert.throws(() => shotReferences(refs,
    pruneAbsentHeroMetadata(prompt + ' <Picture 2> enters.', refs, ['NOVA']), [], ['NOVA']), /ссылается/);
});

test('old one-line H3 IR gets ordered sections and task type', () => {
  const prompt = 'subject_definitions: cat. summary: chase. retention_analysis: face. detailed_description: cat runs. overall_soundscape: paws. non_diegetic_music: none.';
  const normalized = normalizeLegacyIr(prompt);
  assert.match(normalized, /^summary:\n\[reference generation\] chase\./m);
  assert.equal([...normalized.matchAll(/^(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music):/gm)].length, 6);
});

test('a material switched off for one shot leaves it and the tags follow the new numbers', () => {
  const cast = shotReferences(refs, 'NOVA and ORION argue in the garage.', [], null, ['coat']);
  assert.deepEqual(cast.references.map((ref) => [ref.id, ref.number]), [['nova', 1], ['orion', 2], ['garage', 3], ['music', 1]]);
  assert.equal(renumberTags('<Picture 4> behind them', cast.renumber), '<Picture 3> behind them');
  assert.deepEqual(cast.dropped.map((ref) => ref.id), ['coat']);
});

test('a switched-off material combines with the absent-hero pass and still blocks a tagged prompt', () => {
  const cast = shotReferences(refs, 'NOVA waits alone in the garage.', [], null, ['garage']);
  assert.deepEqual(cast.references.map((ref) => ref.id), ['nova', 'music']);
  assert.throws(() => shotReferences(refs, 'NOVA in <Picture 4>.', [], null, ['garage']), /выключен для этого шота/);
  assert.throws(() => shotReferences(refs, 'NOVA.', ['garage'], null, ['garage']), /ShotSpec требует/);
});

test('a location picture of another passport place stays out of the shot', () => {
  const places = [{ name: 'Garage', aliases: ['garage', 'гараж'], assets: ['garage'] }];
  const balcony = '<Subject 3> is the environment: Enclosed balcony: thin snow on screen right.';
  assert.equal(shotPlace(balcony), 'Enclosed balcony');
  assert.deepEqual(placeOff(refs, balcony, '', places), ['garage']);  // 31.10 White Planet: no living room on the balcony
  assert.deepEqual(placeOff(refs, 'is the environment: the old garage at night.', '', places), []);
  assert.deepEqual(placeOff(refs, balcony, 'Garage roof', places), []);  // the shot's own location wins
  assert.deepEqual(placeOff(refs, 'NOVA waits.', '', places), []);  // no stated place: nothing is guessed
  assert.deepEqual(placeOff(refs, `${balcony} <Picture 4> stays.`, '', places), []);  // tagged: the Mac gate decides
  assert.deepEqual(placeOff(refs, balcony, '', []), []);  // no passport places: unchanged
  const kept = shotReferences(refs, balcony, [], null, placeOff(refs, balcony, '', places));
  assert.deepEqual(kept.references.filter((ref) => ref.kind === 'image').map((ref) => ref.id), ['nova', 'orion', 'coat']);
});

test('the shot place is checked against the passport before launch', () => {
  const places = [{ name: 'Garage', aliases: ['garage'], assets: ['garage'] }, { name: 'Rooftop', aliases: ['roof'], assets: [] }];
  assert.match(placeNote('is the environment: Enclosed balcony: snow.', '', places), /«Enclosed balcony» не описано в паспорте/);
  assert.match(placeNote('is the environment: the roof at night.', '', places), /«Rooftop» в паспорте нет картинки/);
  assert.equal(placeNote('is the environment: the garage.', '', places), '');
  assert.equal(placeNote('NOVA waits.', '', places), '');
});
