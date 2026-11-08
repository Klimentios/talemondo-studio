import test from 'node:test';
import assert from 'node:assert/strict';
import { duplicateFields, placeAfter, minimap, connectRefusal } from './story-canvas-board.mjs';

test('a duplicated shot keeps text, length, direction and switches, never takes or frames', () => {
  const cut = { id: 'a', prompt: 'NOVA jumps', duration: 7, director: { camera: 'dolly' }, directorProfileId: 'p', offRefs: ['r1'],
    shotJobId: 'job', takes: ['job'], startFrame: { source: 'library', asset_id: 'x' } };
  const copy = duplicateFields(cut);
  assert.deepEqual(copy, { prompt: 'NOVA jumps', duration: 7, director: { camera: 'dolly' }, directorProfileId: 'p', offRefs: ['r1'] });
  copy.offRefs.push('r2'); copy.director.camera = 'static';
  assert.deepEqual(cut.offRefs, ['r1']); assert.equal(cut.director.camera, 'dolly');
});

test('placeAfter moves one shot right after another and keeps the rest in order', () => {
  const list = ['a', 'b', 'c', 'd'].map((id) => ({ id }));
  assert.deepEqual(placeAfter(list, 'd', 'a').map((c) => c.id), ['a', 'd', 'b', 'c']);
  assert.deepEqual(placeAfter(list, 'a', 'c').map((c) => c.id), ['b', 'c', 'a', 'd']);
  assert.equal(placeAfter(list, 'x', 'a'), list);
});

test('minimap fits nodes and the visible window, and maps a click back to the board', () => {
  const nodes = [{ x: 0, y: 0, w: 100, h: 100 }, { x: 900, y: 0, w: 100, h: 100 }];
  const geo = minimap(nodes, { x: 0, y: 0, zoom: 1 }, { w: 500, h: 250 }, { w: 200, h: 100 });
  assert.equal(geo.scale, 0.2);
  assert.deepEqual([geo.nodes[1].x, geo.nodes[1].w], [180, 20]);
  assert.deepEqual([geo.seen.x, geo.seen.w], [0, 100]);
  const p = geo.toWorld(geo.nodes[1].x + 10, geo.nodes[1].y + 10);
  assert.deepEqual([Math.round(p.x), Math.round(p.y)], [950, 50]);
});

test('a hand-made link that the render rules refuse says why', () => {
  const hero = { id: 'h', role: 'Лицо героя', owner: 'NOVA' };
  const place = { id: 'p', role: 'Окружение' };
  assert.equal(connectRefusal({ ids: new Set(['h']) }, hero), '');
  assert.match(connectRefusal({ ids: new Set() }, hero), /NOVA.*не назван/);
  assert.match(connectRefusal({ ids: new Set() }, place), /другого места/);
  assert.equal(connectRefusal({ ids: new Set(), error: 'boom' }, place), 'boom');
});

test('a note is added with its time and removed by id; an empty note is refused', async () => {
  const { addNote, removeNote } = await import('./story-canvas-board.mjs');
  const cut = { id: 'a', prompt: 'NOVA jumps' };
  assert.equal(addNote(cut, '   '), cut);
  const one = addNote(cut, ' slower jump ', Date.UTC(2026, 10, 1));
  assert.deepEqual(one.notes.map((n) => n.text), ['slower jump']);
  assert.equal(one.prompt, 'NOVA jumps');
  const two = addNote(one, 'keep take 2', Date.UTC(2026, 10, 1) + 1000);
  assert.deepEqual(removeNote(two, one.notes[0].id).notes.map((n) => n.text), ['keep take 2']);
});
