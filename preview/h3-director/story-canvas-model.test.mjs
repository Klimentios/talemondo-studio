import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultLayout, mergeLayout, structureKey, takeList, nodeState, filmPlan, plural, SHOT_W, shotWidth } from './story-canvas-model.mjs';

const jobs = (...list) => new Map(list.map((job) => [job.job_id, job]));
const cut = (id, extra = {}) => ({ id, prompt: 'x', duration: 15, ...extra });

test('shots form one left-to-right strip after the materials', () => {
  const layout = defaultLayout([cut('a'), cut('b'), cut('c')], [{ id: 'r1', kind: 'image' }]);
  assert.equal(layout['m:r1'].x, 0);
  assert.ok(layout['s:a'].x > 0);
  assert.equal(layout['s:b'].x - layout['s:a'].x, layout['s:c'].x - layout['s:b'].x);
  assert.ok(layout['s:b'].x - layout['s:a'].x >= SHOT_W, 'shots never overlap');
  const tall = defaultLayout([cut('a'), cut('b')], [], { aspect: '9:16' });
  assert.ok(tall['s:b'].x - tall['s:a'].x >= shotWidth('9:16'));
  assert.ok(tall['m:+'], 'an empty scene shows the add-material card');
  assert.ok(layout.film.x > layout['s:c'].x);
});

test('saved positions survive, new shots get a default place', () => {
  const merged = mergeLayout({ 's:a': { x: 999, y: 5 } }, [cut('a'), cut('b')], []);
  assert.deepEqual(merged['s:a'], { x: 999, y: 5 });
  assert.ok(Number.isFinite(merged['s:b'].x));
});

test('structure key ignores prompt and job progress, follows order and links', () => {
  const a = structureKey('s1', [cut('a'), cut('b')], []);
  assert.equal(a, structureKey('s1', [cut('a', { prompt: 'другое' }), cut('b', { shotJobId: 'j' })], []));
  assert.notEqual(a, structureKey('s1', [cut('b'), cut('a')], []));
  assert.notEqual(a, structureKey('s1', [cut('a'), cut('b', { startFrame: { source: 'previous' } })], []));
});

test('cancelled jobs are not takes and do not count as a shot result', () => {
  const list = jobs({ job_id: 'c', state: 'cancelled' }, { job_id: 'd', state: 'completed' }, { job_id: 'f', state: 'failed' });
  const shot = cut('a', { shotJobId: 'c', takes: [{ jobId: 'c' }, { jobId: 'd' }, { jobId: 'f' }] });
  assert.deepEqual(takeList(shot, list).map((take) => [take.jobId, take.status]), [['d', 'done'], ['f', 'failed']]);
  assert.equal(nodeState([shot], 0, list).status, 'idle');
});

test('film plan glues the ready shots in order and names the missing ones', () => {
  const list = jobs({ job_id: 'j1', state: 'completed' }, { job_id: 'j3', state: 'completed' }, { job_id: 'j2', state: 'running' });
  const accepted = (id, duration) => cut(id, { shotJobId: id, duration,
    shotQcReview: 'accepted', shotQc: { job_id: id, decision: 'accept' } });
  const plan = filmPlan([accepted('j1', 15), cut('b', { shotJobId: 'j2' }), accepted('j3', 6), cut('d')], list);
  assert.deepEqual(plan.ids, ['j1', 'j3']);
  assert.deepEqual(plan.missing, [1, 3]);
  assert.equal(plan.seconds, 21);
});

test('russian plural forms', () => {
  assert.equal(plural(1, 'сцена', 'сцены', 'сцен'), '1 сцена');
  assert.equal(plural(3, 'шот', 'шота', 'шотов'), '3 шота');
  assert.equal(plural(11, 'шот', 'шота', 'шотов'), '11 шотов');
});

test('auto note tells what «Собрать сцену» did with a shot', async () => {
  const { autoNote } = await import('./story-canvas-model.mjs');
  assert.equal(autoNote({}), '');
  assert.equal(autoNote({ keyframeJobIds: ['a', 'b'] }), '✨ ключевой кадр готовится · 2 варианта');
  assert.equal(autoNote({ keyframePick: { candidates: ['a', 'b'], reason: 'тот же свет' }, shotJobId: 'j', shotQc: { job_id: 'j', decision: 'accept' }, autoRetakes: 1 }),
    '✨ AI выбрал лучший из 2: тот же свет · ◇ AI-проверка: принято · ↻ AI переснял');
  assert.equal(autoNote({ keyframePick: { candidates: ['a', 'b'], fallback: true } }), '✨ 2 варианта, AI-выбор недоступен — взят первый');
  assert.equal(autoNote({ shotQcError: 'GPU' }), '◇ AI-проверка недоступна');
});

test('the canvas links a shot only to the materials the render will send', async () => {
  const { shotMaterials, materialGroup } = await import('./story-canvas-model.mjs');
  const materials = [
    { id: 'alex', kind: 'image', role: 'Персонаж целиком', owner: 'ALEX', number: 1 },
    { id: 'nova', kind: 'image', role: 'Персонаж целиком', owner: 'NOVA', number: 2 },
    { id: 'room', kind: 'image', role: 'Окружение', owner: '', number: 3 },
  ];
  const aloneAris = shotMaterials(cut('1', { prompt: 'NOVA stares at the ceiling.' }), materials);
  assert.deepEqual([...aloneAris.ids], ['nova', 'room']);
  const both = shotMaterials(cut('2', { prompt: 'ALEX lifts NOVA.', offRefs: ['room'] }), materials);
  assert.deepEqual([...both.ids], ['alex', 'nova']);
  assert.ok(both.off.has('room'));
  const broken = shotMaterials(cut('3', { prompt: 'ALEX in <Picture 3>.', offRefs: ['room'] }), materials);
  assert.match(broken.error, /выключен/);
  assert.deepEqual([materialGroup(materials[0]), materialGroup(materials[2]), materialGroup({ role: 'Свет' })], ['hero', 'place', 'look']);
});
