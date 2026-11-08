import test from 'node:test';
import assert from 'node:assert/strict';
import { templateFromScene, cutsFromTemplate, BUILTIN_TEMPLATES } from './story-canvas-templates.mjs';

test('a template keeps shots and materials, never takes, frames or notes', () => {
  const cuts = [
    { id: 'a', prompt: 'NOVA enters', duration: 6, director: { lens: '35mm' }, offRefs: ['r2'], shotJobId: 'job', takes: [{ jobId: 'job' }], notes: [{ text: 'x' }], startFrame: { source: 'library', asset_id: 'f' } },
    { id: 'b', prompt: 'NOVA sits', duration: 5, startFrame: { source: 'previous', asset_id: null } },
  ];
  const refs = [{ id: 'r1', kind: 'image', name: 'nova.png', role: 'Лицо героя', owner: 'NOVA', number: 1, previs: true }];
  const t = templateFromScene({ title: ' Вход ', cuts, references: refs }, 1000);
  assert.equal(t.title, 'Вход');
  assert.deepEqual(t.cuts[0], { prompt: 'NOVA enters', duration: 6, director: { lens: '35mm' }, directorProfileId: '', offRefs: ['r2'], link: false });
  assert.equal(t.cuts[1].link, true);
  assert.deepEqual(t.references, [{ id: 'r1', kind: 'image', name: 'nova.png', role: 'Лицо героя', owner: 'NOVA' }]);
  assert.ok(!JSON.stringify(t).includes('job') && !JSON.stringify(t).includes('"f"'));
});

test('a scene from a template gets continue links and drops switches of materials gone from the library', () => {
  const t = templateFromScene({ cuts: [{ prompt: 'a', duration: 5, offRefs: ['gone', 'kept'] }, { prompt: 'b', duration: 7, startFrame: { source: 'previous' } }] });
  const fields = cutsFromTemplate(t, new Set(['kept']));
  assert.deepEqual(fields[0].offRefs, ['kept']);
  assert.equal(fields[0].startFrame, null);
  assert.deepEqual(fields[1].startFrame, { source: 'previous', asset_id: null });
});

test('built-in templates are English placeholder drafts with valid lengths', () => {
  for (const t of BUILTIN_TEMPLATES) {
    assert.ok(t.cuts.length >= 3);
    for (const cut of t.cuts) {
      assert.match(cut.prompt, /\[/);
      assert.doesNotMatch(cut.prompt, /[А-Яа-яЁё]/);
      assert.ok(cut.duration >= 4 && cut.duration <= 15);
    }
  }
});
