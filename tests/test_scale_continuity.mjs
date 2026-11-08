import assert from 'node:assert/strict';
import { addReferenceRoleDirections, addScaleContinuityDirection } from '../preview/h3-director/model.mjs';

const input = 'A cat sits beside its owner.';
const output = addScaleContinuityDirection(input);
assert(output.startsWith(input));
assert.match(output, /cat remains clearly cat-sized and visibly smaller than its adult owner/i);
assert.match(output, /about 95%/);
assert.equal(addScaleContinuityDirection(output), output, 'the scale contract must not duplicate on retries');
assert.equal(addScaleContinuityDirection('  '), '');

const withMusicRole = addReferenceRoleDirections('A cat moves to the music.', [
  { kind: 'audio', number: 1, role: 'Музыка' },
]);
assert.match(withMusicRole, /<Audio 1> provides music style and rhythm only/);
assert.equal(addReferenceRoleDirections(withMusicRole, [
  { kind: 'audio', number: 1, role: 'Музыка' },
]), withMusicRole, 'reference role directions must not duplicate');

console.log('scale continuity and reference-role prompt contracts: OK');
