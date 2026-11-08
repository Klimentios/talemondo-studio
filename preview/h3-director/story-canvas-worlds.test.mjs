import test from 'node:test';
import assert from 'node:assert/strict';
import { isFantasy, worldDraft, worldPrompt, bindWorld, worldSize } from './story-canvas-worlds.mjs';

const shot = (id, line, extra = {}) => ({ id, prompt: `<Subject 3> is the environment: Enclosed balcony.\n[reference generation] ${line}\n[Shot 1] ...`, ...extra });
const cuts = [
  shot('a', 'At the enclosed balcony threshold, NOVA discovers snow while ALEX opens the door.'),
  shot('b', 'NOVA imagines the snowy balcony as an icy planet and chooses to explore.'),
  shot('c', 'On the imagined icy planet NOVA crosses a real shallow snow ridge.'),
  shot('d', "NOVA returns to ALEX's warm room; a snowflake exposes how cold his tiny expedition was."),
];

test('fantasy shots are read from the story line, a hand flag wins', () => {
  assert.deepEqual(cuts.map(isFantasy), [false, true, true, false]);
  assert.equal(isFantasy({ ...cuts[0], fantasy: true }), true);
  assert.equal(isFantasy({ ...cuts[1], fantasy: false }), false);
});

test('the world draft comes from the fantasy shots only, and the request keeps the real layout and nobody in it', () => {
  const draft = worldDraft(cuts);
  assert.match(draft, /icy planet and chooses/);
  assert.doesNotMatch(draft, /warm room|threshold/);
  const prompt = worldPrompt(draft);
  assert.match(prompt, /icy planet/);
  assert.match(prompt, /no people, no animals/);
  assert.doesNotMatch(prompt, /[А-Яа-яЁё]/);
  assert.deepEqual(['9:16', '16:9', '1:1'].map(worldSize), ['768x1344', '1344x768', '1024x1024']);
});

test('the world picture goes only into fantasy shots; the real place leaves them; other switches stay', () => {
  const refs = [{ id: 'hero', kind: 'image', role: 'Лицо героя' }, { id: 'flat', kind: 'image', role: 'Окружение' }, { id: 'world', kind: 'image', role: 'Окружение' }];
  const bound = bindWorld(cuts.map((cut, i) => (i === 3 ? { ...cut, offRefs: ['hero', 'flat'] } : cut)), refs, 'world');
  assert.deepEqual(bound.map((cut) => [...(cut.offRefs || [])].sort()), [['world'], ['flat'], ['flat'], ['hero', 'world']]);
  assert.equal(bindWorld(bound, refs, 'world')[1], bound[1]);
  assert.equal(bindWorld(cuts, refs, ''), cuts);
});
