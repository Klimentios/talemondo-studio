import test from 'node:test';
import assert from 'node:assert/strict';
import { COLLECTIONS, inferredCollection, collectionId, loadFranchiseMemberships, sectionFor } from './library-collections.mjs';

test('a fresh library assigns franchises only from saved choices, never from file names', () => {
  assert.equal(inferredCollection({ original_name: 'hero face.png', role: 'Лицо героя' }).franchise, '');
  assert.deepEqual(inferredCollection({ original_name: 'x.png' }, { franchise: 'Demo', characterName: 'Hero', look: 'Night' }),
    { franchise: 'Demo', characterName: 'Hero', look: 'Night' });
  assert.equal(sectionFor({ kind: 'image' }, { preferredRole: 'Окружение' }), 'Локации');
});

test('collections are the franchises of this Studio', async () => {
  await loadFranchiseMemberships(async path => path === '/api/franchises' ? [{ id: 'f1', name: 'Demo' }] : [], new Map());
  assert.deepEqual(COLLECTIONS, [['f1', 'Demo']]);
  assert.equal(collectionId('Demo'), 'f1');
});

test('a shared asset retains membership in both franchise boards', async () => {
  const api = async (path) => path === '/api/franchises' ? [
    { id: 'one', name: 'IRIS & MIRA' }, { id: 'two', name: 'SAMPLE PACK' },
  ] : [{ type: 'character', title: 'ИРИС · герой', state: 'draft', asset_ids: ['shared'] }];
  const memberships = await loadFranchiseMemberships(api, new Map([['shared', { original_name: 'IRIS_APPROVED.png' }]]));
  assert.deepEqual(memberships.get('shared').map(item => item.franchise), ['IRIS & MIRA', 'SAMPLE PACK']);
  assert.ok(memberships.get('shared').every(item => item.characterName === 'Ирис'));
});

test('franchise portrait and outfit retain distinct roles', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'am', name: 'NOVA & ORION' }]
    : [{ type: 'character', title: 'НОВА · The Brain', state: 'draft', asset_ids: ['face', 'pink', 'halloween'] }];
  const assets = new Map([['face', { original_name: '11111.jpeg' }], ['pink', { original_name: 'nova costume pink.png' }],
    ['halloween', { original_name: 'Нова · Хэллоуин · маг.png' }]]);
  const memberships = await loadFranchiseMemberships(api, assets);
  assert.deepEqual(['face', 'pink', 'halloween'].map(id => [memberships.get(id)[0].preferredRole, memberships.get(id)[0].look]),
    [['Референс', ''], ['Костюм', ''], ['Костюм', 'Хэллоуин']]);
});

test('white-background portraits remain faces while separate outfits and body sheets keep their roles', async () => {
  const api = async path => path === '/api/franchises' ? [
    { id: 'ka', name: 'ALEX & NOVA' }, { id: 'bb', name: 'IRIS & MIRA' },
  ] : path.includes('/ka/') ? [{ type: 'character', title: 'НОВА · эталон', state: 'draft', asset_ids: ['catFace', 'catBody'] }]
    : [{ type: 'character', title: 'ИРИС · герой', state: 'draft', asset_ids: ['iriFace', 'bee'] }];
  const assets = new Map([
    ['catFace', { original_name: 'НОВА 3D · портрет · белый фон.png' }],
    ['catBody', { original_name: 'нова 1.png' }],
    ['iriFace', { original_name: 'ИРИС · портрет · белый фон.png' }],
    ['bee', { original_name: 'ИРИС · Пчела · полный образ.png' }],
  ]);
  const memberships = await loadFranchiseMemberships(api, assets);
  assert.deepEqual(['catFace', 'catBody', 'iriFace', 'bee'].map(id => memberships.get(id)[0].preferredRole),
    ['Лицо героя', 'Референс', 'Лицо героя', 'Костюм']);
});

test('Iris Mira cast separates a portrait, legacy full body and explicit wardrobe without swapping identity', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'bb', name: 'IRIS & MIRA' }]
    : [{ type: 'character', title: 'Учёный-экспериментатор · IRIS & MIRA', state: 'approved',
      asset_ids: ['face', 'body', 'costume', 'alternateFace'] }];
  const assets = new Map([
    ['face', { original_name: 'Учёный-экспериментатор · портрет · белый фон.png', role: 'Лицо героя' }],
    ['body', { original_name: 'IRIS-MIRA-scientist-v2.png', role: 'Концепт персонажа · IRIS & MIRA' }],
    ['costume', { original_name: 'Учёный-экспериментатор · основной костюм.png', role: 'Костюм' }],
    ['alternateFace', { original_name: 'portrait-alt.png', role: 'Лицо героя' }],
  ]);
  const memberships = await loadFranchiseMemberships(api, assets);
  assert.deepEqual(['face', 'body', 'costume', 'alternateFace'].map(id => memberships.get(id)[0].preferredRole),
    ['Лицо героя', 'Референс', 'Костюм', 'Лицо героя']);
  assert.ok([...memberships.values()].every(entries => entries[0].characterName === 'Учёный-экспериментатор'));
  assert.equal(sectionFor(assets.get('body'), memberships.get('body')[0]), 'Учёный-экспериментатор · другие материалы');
});

test('franchises load concurrently and preserve shared-asset order when responses finish out of order', async () => {
  const started = [];
  let releaseFirst;
  const first = new Promise(resolve => { releaseFirst = resolve; });
  const load = loadFranchiseMemberships(async path => {
    if (path === '/api/franchises') return [{ id: 'one', name: 'IRIS & MIRA' }, { id: 'two', name: 'SAMPLE PACK' }];
    started.push(path);
    if (path.includes('/one/')) return first;
    return [{ type: 'character', title: 'Мира', asset_ids: ['shared'] }];
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(started.length >= 3);
  releaseFirst([{ type: 'character', title: 'Ирис', asset_ids: ['shared'] }]);
  assert.deepEqual((await load).get('shared').map(entry => entry.characterName), ['Ирис', 'Мира']);
});

test('generation reference cards form one section with a sub-path per card', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'am', name: 'NOVA & ORION' }]
    : [{ type: 'location', title: 'Референсы для генерации · Машины', state: 'draft', asset_ids: ['car', 'lot'] },
      { type: 'location', title: 'VICE PAWS CITY', asset_ids: ['city'] }];
  const assets = new Map([['car', { kind: 'image', role: 'Предмет' }], ['lot', { kind: 'image', role: 'Окружение' }],
    ['city', { kind: 'image', role: 'Окружение' }]]);
  const memberships = await loadFranchiseMemberships(api, assets);
  const car = memberships.get('car')[0];
  assert.equal(car.category, 'Референсы для генерации / Машины');
  assert.equal(car.preferredRole, 'Предмет');
  assert.equal(car.displayName, 'Машины · 01');
  assert.equal(sectionFor(assets.get('car'), car), 'Референсы для генерации');
  assert.equal(sectionFor(assets.get('city'), memberships.get('city')[0]), 'Локации');
});

test('an idea image retains its idea title and draft state', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'ka', name: 'ALEX & NOVA' }]
    : path.endsWith('/profile') ? { roles: [] }
      : [{ id: 'idea-35', type: 'idea', title: '35 · НОВА И ПРИНТЕР АТАКУЕТ', state: 'draft',
        asset_ids: ['fantasy-35'] }];
  const asset = { asset_id: 'fantasy-35', kind: 'image', role: 'Референс', original_name: 'NOVA_35_real_fantasy.png' };
  const entries = await loadFranchiseMemberships(api, new Map([[asset.asset_id, asset]]));
  const info = entries.get(asset.asset_id)[0];
  assert.equal(info.franchise, 'ALEX & NOVA');
  assert.equal(info.category, 'Идеи / 35 · НОВА И ПРИНТЕР АТАКУЕТ');
  assert.equal(info.state, 'draft');
  assert.equal(sectionFor(asset, info), 'Идеи');
});

test('scene and rule pictures cannot become hero faces through their stored role', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'ka', name: 'ALEX & NOVA' }]
    : path.endsWith('/profile') ? { roles: [] }
      : [{ type: 'scene', title: 'Шот 1', asset_ids: ['shot'] },
        { type: 'rule', title: 'Облик', asset_ids: ['rule'] }];
  const assets = new Map([['shot', { kind: 'image', role: 'Лицо героя', original_name: 'shot.png' }],
    ['rule', { kind: 'image', role: 'Лицо героя', original_name: 'rule.png' }]]);
  const links = await loadFranchiseMemberships(api, assets);
  assert.equal(sectionFor(assets.get('shot'), links.get('shot')[0]), 'Сцены и раскадровки');
  assert.equal(sectionFor(assets.get('rule'), links.get('rule')[0]), 'Правила и эталоны');
});

test('unknown character concept stays outside face and costume sections', async () => {
  const api = async path => path === '/api/franchises' ? [{ id: 'am', name: 'NOVA & ORION' }]
    : path.endsWith('/profile') ? { roles: [] }
      : [{ type: 'character', title: 'ВОЛЬТ · кандидат', asset_ids: ['concept'] }];
  const asset = { kind: 'image', role: 'Концепт персонажа', original_name: 'VOLT-concept-v1.png' };
  const links = await loadFranchiseMemberships(api, new Map([['concept', asset]]));
  assert.equal(links.get('concept')[0].preferredRole, 'Референс');
  assert.equal(sectionFor(asset, links.get('concept')[0]), 'Вольт · другие материалы');
  assert.equal(COLLECTIONS.some(([id]) => id === 'review'), false);
});
