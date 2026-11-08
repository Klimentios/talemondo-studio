import test from 'node:test';
import assert from 'node:assert/strict';
import { bibleCards, emptyProfile, newRole, profileBody, splitList, trialShot } from './franchise-profile-model.mjs';

test('body drops unnamed roles and empty defaults', () => {
  const profile = { ...emptyProfile(), director_defaults: { lighting: { scheme: 'rembrandt', key: '' }, color: {} },
    camera: { ...emptyProfile().camera, lens_min: '24', lens_max: '' },
    roles: [{ ...newRole('costume'), name: ' Hoodie ', owner: ' ALEX ' }, newRole()] };
  const body = profileBody(profile);
  assert.deepEqual(body.director_defaults, { lighting: { scheme: 'rembrandt' } });
  assert.equal(body.camera.lens_min, 24);
  assert.equal(body.camera.lens_max, null);
  assert.deepEqual(body.roles.map((role) => [role.name, role.owner, role.with_owner]), [['Hoodie', 'ALEX', true]]);
});

test('bible cards with pictures resolve unless a role covers them', () => {
  const cards = [{ id: 'a', type: 'character', asset_ids: ['x'] }, { id: 'b', type: 'rule', asset_ids: ['y'] },
    { id: 'c', type: 'location', asset_ids: [] }, { id: 'd', type: 'location', asset_ids: ['z'] }];
  const profile = { ...emptyProfile(), roles: [{ ...newRole(), entity_id: 'd' }] };
  assert.deepEqual(bibleCards(cards, profile).map((card) => card.id), ['a']);
});

test('trial shot and lists', () => {
  assert.deepEqual(splitList(' a, ,b '), ['a', 'b']);
  assert.deepEqual(trialShot('ALEX, NOVA', ' Nova jumps '), { characters: ['ALEX', 'NOVA'], title: 'Nova jumps' });
});
