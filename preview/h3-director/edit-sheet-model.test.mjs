import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSheet, planNotes, sheetRows, sheetSettings } from './edit-sheet-model.mjs';

const cut = (id, job, sceneShot = {}) => ({ id, duration: 5, shotJobId: job, sceneShot,
  shotQcReview: 'accepted', shotQc: { job_id: job, decision: 'accept' } });
const done = (...ids) => new Map(ids.map((id) => [id, { state: 'completed' }]));

test('seam takes user choice, then incoming transition_in, then outgoing transition_out', () => {
  const scenes = [{ title: 'Кухня', cuts: [cut('a', 'ja', { transition_out: 'whip_pan', transition_in: 'dissolve' }),
    cut('b', 'jb'), cut('c', 'jc', { transition_in: 'morph' })] }, { title: 'Двор', cuts: [cut('d', 'jd')] }];
  const rows = sheetRows(scenes, sheetSettings({ editSheet: { transitions: { d: { type: 'fade', seconds: 0.4 } } } }));
  assert.deepEqual(rows.map((row) => row.transition), ['cut', 'whip_pan', 'morph', 'fade']);
  assert.deepEqual(rows.map((row) => row.auto), [true, true, true, false]);
  assert.equal(rows[3].seconds, 0.4);
  assert.equal(rows[3].card.location, 'Двор');
});

test('film opening keeps only a fade in', () => {
  const rows = sheetRows([{ cuts: [cut('a', 'ja', { transition_in: 'fade' })] }], sheetSettings({}));
  assert.equal(rows[0].transition, 'fade');
});

test('sheet waits for every chosen take', () => {
  const scenes = [{ title: 'Кухня', cuts: [cut('a', 'ja'), cut('b', 'jb')] }];
  assert.equal(buildSheet(scenes, done('ja')).ready, false);
  const { ready, body } = buildSheet(scenes, done('ja', 'jb'),
    sheetSettings({ editSheet: { music_asset_id: 'm', aspect_ratio: '16:9' } }));
  assert.ok(ready);
  assert.deepEqual(body.clips.map((clip) => clip.job_id), ['ja', 'jb']);
  assert.equal(body.aspect_ratio, '16:9');
  assert.deepEqual(body.music, { asset_id: 'm', volume: 0.35, duck: true });
  assert.equal(body.target_seconds, 10);
});

test('shot cards carry only schema-valid values', () => {
  const [row] = sheetRows([{ title: 'K', cuts: [cut('a', 'ja', { framing: 'close', purpose: 'Событие шота',
    lens_mm: '85', axis_side: 'Z', characters: ['ALEX', 3], camera_motion: 'slow push in' })] }]);
  assert.equal(row.card.framing, 'close');
  assert.equal(row.card.purpose, undefined);
  assert.equal(row.card.axis_side, undefined);
  assert.equal(row.card.lens_mm, 85);
  assert.deepEqual(row.card.characters, ['ALEX']);
});

test('plan notes put errors first and skip tips', () => {
  const notes = planNotes({ warnings: ['Хронометраж'], shot_review: { findings: [
    { severity: 'tip', shot: 1, message: 't' }, { severity: 'error', shot: 2, message: 'ось', fix: 'поменяй' }] } });
  assert.deepEqual(notes.map((note) => note.level), ['error', 'warning']);
  assert.equal(notes[0].text, 'Шот 2: ось — поменяй');
});

test('seam look, accent, freeze frame and finishing reach the sheet', () => {
  const scenes = [{ title: 'Улица', cuts: [cut('a', 'ja'), cut('b', 'jb', { transition_in: 'smash_cut' })] }];
  const settings = sheetSettings({ editSheet: { film_look: true, transitions: {
    a: { effect: 'zoomin' }, b: { effect: 'fadewhite', accent: 'shake', freeze: 1.5 } } } });
  const { body } = buildSheet(scenes, done('ja', 'jb'), settings);
  assert.equal(body.clips[0].transition_effect, undefined);  // the opening has no seam
  assert.equal(body.clips[1].transition_effect, 'fadewhite');
  assert.deepEqual(body.clips[1].accents, [{ type: 'shake' }]);
  assert.equal(body.clips[1].freeze_end, 1.5);
  assert.equal(body.auto_sfx, true);
  assert.equal(body.loudness, -14);
  assert.equal(body.grain, 0.35);
  const quiet = buildSheet(scenes, done('ja', 'jb'), sheetSettings({ editSheet: { auto_sfx: false, loudness: false } })).body;
  assert.equal(quiet.auto_sfx, false);
  assert.equal(quiet.loudness, null);
});

test('titles become graphics: the name card waits for the freeze frame', () => {
  const scenes = [{ title: 'Улица', cuts: [cut('a', 'ja'), cut('b', 'jb')] }];
  const settings = sheetSettings({ editSheet: { transitions: {
    a: { graphic: { template: 'KineticTitle', text: 'ONE RAINY NIGHT' } },
    b: { freeze: 1.5, graphic: { template: 'NameCard', text: 'ALEX', line: 'Hacker.' } } } } });
  const { body } = buildSheet(scenes, done('ja', 'jb'), settings);
  assert.deepEqual(body.graphics, [
    { template: 'KineticTitle', clip: 1, offset: 0.2, duration: 2.2, props: { text: 'ONE RAINY NIGHT' } },
    { template: 'NameCard', clip: 2, anchor: 'freeze', duration: 1.5, props: { name: 'ALEX', line: 'Hacker.' } }]);
  const empty = buildSheet(scenes, done('ja', 'jb'), sheetSettings({ editSheet: { transitions: { a: { graphic: { template: 'LowerThird', text: ' ' } } } } }));
  assert.equal(empty.body.graphics, undefined);
});

test('colour: film look, shot matching and a shot exposure reach the sheet', () => {
  const scenes = [{ title: 'Улица', cuts: [cut('a', 'ja'), cut('b', 'jb')] }];
  const settings = sheetSettings({ editSheet: { look: 'teal_orange', look_intensity: 0.5, transitions: { b: { exposure: 0.4 } } } });
  const { body } = buildSheet(scenes, done('ja', 'jb'), settings);
  assert.equal(body.look, 'teal_orange');
  assert.equal(body.look_intensity, 0.5);
  assert.equal(body.match_color, true);
  assert.deepEqual(body.clips[1].grade, { exposure: 0.4 });
  assert.equal(body.clips[0].grade, undefined);
  const off = buildSheet(scenes, done('ja', 'jb'), sheetSettings({ editSheet: { match_color: false } })).body;
  assert.equal(off.match_color, undefined);
  assert.equal(off.look, undefined);
});
