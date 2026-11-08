import test from 'node:test';
import assert from 'node:assert/strict';
import * as model from './model.mjs';
const { addCut, compileBrief, createProject, removeCut } = model;
const { addReference, findMentionQuery, insertReferenceMention, mediaKind, mentionedReferences, referenceDisplayTag, referenceTag, removeReference } = model;

test('realistic scene never inherits the previous cartoon LoRA', () => {
  assert.equal(model.effectiveStyleAdapter('realistic', 'style3d'), 'none');
  assert.equal(model.effectiveStyleAdapter('animation3d', 'none'), 'style3d');
});

test('voice direction keeps separate casting for multiple named characters', () => {
  const text = model.addActingVoiceDirection('NOVA: Hello. ORION: Wait.', { actingVoices: {
    NOVA: { voice: 'child', texture: 'bright', emotion: 'joyful' },
    ORION: { voice: 'adult_male', texture: 'low', accent: 'neutral Russian' },
  } });
  assert.match(text, /VOICE CAST FOR NOVA:.*child voice/);
  assert.match(text, /VOICE CAST FOR ORION:.*adult male voice/);
  assert.match(text, /neutral Russian/);
  assert.equal(model.addActingVoiceDirection(text, { actingVoices: {
    NOVA: { voice: 'child', texture: 'bright', emotion: 'joyful' },
    ORION: { voice: 'adult_male', texture: 'low', accent: 'neutral Russian' },
  } }), text);
});

test('explicit story voice wins over a conflicting saved age and uses the reference subject name', () => {
  const story = '<Picture 1> defines NOVA.\n<Picture 2> defines ORION.\n\nORION speaks in a low, controlled male voice with a slightly rough texture.\n\nORION: "Wait."';
  const text = model.addActingVoiceDirection(story, {
    references: [{ kind: 'image', owner: '', number: 1 }, { kind: 'image', owner: 'Орион', number: 2 }],
    actingOwner: 'Орион', actingVoice: 'child', actingTimbre: 'хриплый',
  });
  assert.match(text, /VOICE CAST FOR ORION:/);
  assert.doesNotMatch(text, /child voice|never an adult voice/);
  assert.match(text, /low, controlled male voice/);
  assert.ok(text.startsWith(story));
});

test('updating voice settings replaces the generated casting line', () => {
  const old = model.addActingVoiceDirection('NOVA: Hello.', { actingVoices: { NOVA: { voice: 'child' } } });
  const updated = model.addActingVoiceDirection(old, { actingVoices: { NOVA: { voice: 'adult_male' } } });
  assert.match(updated, /stable adult male voice/);
  assert.doesNotMatch(updated, /child voice/);
});

test('style adapter submission follows only adapters confirmed by GPU health', () => {
  assert.equal(model.canSubmitStyleAdapter('none', []), true);
  assert.equal(model.canSubmitStyleAdapter('style3d', []), false);
  assert.equal(model.canSubmitStyleAdapter('style3d', ['style3d']), true);
  assert.equal(model.canSubmitStyleAdapter('cartoon2d', ['cartoon2d']), true);
  assert.equal(model.canSubmitStyleAdapter('cartoon2d', []), false);
  assert.equal(model.canSubmitStyleAdapter('cel2d', ['cel2d']), false);
  assert.equal(model.canSubmitStyleAdapter('sketch2d', ['style3d']), false);
});

test('3D mode keeps its LoRA binding and other modes do not force unverified adapters', () => {
  assert.equal(model.defaultStyleAdapterForMode('animation3d'), 'style3d');
  assert.equal(model.effectiveStyleAdapter('animation3d', 'none'), 'style3d');
  assert.equal(model.effectiveStyleAdapter('animation2d', 'none'), 'cartoon2d');
  assert.equal(model.effectiveStyleAdapter('realistic', 'none'), 'none');
  assert.equal(model.effectiveStyleAdapter('animation2d', 'sketch2d'), 'cartoon2d');
  assert.equal(model.effectiveStyleAdapter('replace', 'characterswap'), 'none');
});

test('H3 size presets show the exact node geometry', () => {
  assert.deepEqual(model.effectiveResolution('9:16', 'compact'), { width: 544, height: 960, pixels: 522240, selectorMp: 0.5 });
  assert.deepEqual(model.effectiveResolution('16:9', 'quick'), { width: 1216, height: 672, pixels: 817152, selectorMp: 0.8 });
  assert.equal(model.effectiveResolution('16:9', 'balanced').width, 1280);
  assert.equal(model.effectiveResolution('9:16', 'max').height, 1344);
  assert.throws(() => model.effectiveResolution('16:9', 'custom', 1), /Недопустимое/);
});

test('visible duration uses the same H3 frame grid as the GPU request', () => {
  assert.equal(model.effectiveFrameCount(5), 124);
  assert.equal(model.effectiveFrameCount(14), 345);
  assert.equal(model.effectiveFrameCount(4), 107);
  assert.throws(() => model.effectiveFrameCount(3), /4–15/);
});

test('a new project starts with one editable cut and BF16 reference mode', () => {
  const project = createProject();
  assert.equal(project.mode, 'replace');
  assert.equal(project.profile, 'bf16');
  assert.equal('motionControl' in project, false);
  assert.equal(project.styleAdapter, 'none');
  assert.equal(project.cacheMode, 'safe');
  assert.equal(project.useVideoAudio, false);
  assert.equal(project.cuts.length, 1);
  assert.equal(project.cuts[0].duration, 5);
});

test('adding and removing cuts keeps a non-empty timeline', () => {
  let project = createProject();
  project = addCut(project);
  assert.equal(project.cuts.length, 2);
  assert.notEqual(project.cuts[0].id, project.cuts[1].id);
  project = removeCut(project, project.cuts[0].id);
  assert.equal(project.cuts.length, 1);
  project = removeCut(project, project.cuts[0].id);
  assert.equal(project.cuts.length, 1);
});

test('compiled brief assigns picture and video tags in visible order', () => {
  const project = createProject();
  project.references = [
    { id: 'a', kind: 'image', name: 'face.png', role: 'Лицо героя' },
    { id: 'b', kind: 'video', name: 'walk.mp4', role: 'Движение' },
    { id: 'c', kind: 'image', name: 'street.png', role: 'Окружение' },
  ];
  project.cuts[0].prompt = 'Герой входит в кадр.';
  assert.equal(
    compileBrief(project),
    'Режим: Замена персонажа\nМодель: MiniMax H3 Ref2VA BF16\nФормат: 9:16\n\nРеференсы:\n<Picture 1> Лицо героя — face.png\n<Video 1> Движение — walk.mp4\n<Picture 2> Окружение — street.png\n\nCUT 1 · 5 сек\nГерой входит в кадр.'
  );
});

test('draft numbers close gaps and next import reuses the first free local label', () => {
  let project = createProject();
  project = addReference(project, { id: 'a', kind: 'image', name: 'first.png', role: 'Лицо героя' });
  project = addReference(project, { id: 'b', kind: 'image', name: 'second.png', role: 'Костюм' });
  project = removeReference(project, 'a');
  project = addReference(project, { id: 'c', kind: 'image', name: 'third.png', role: 'Окружение' });
  assert.equal(referenceTag(project.references[0]), '<Picture 1>');
  assert.equal(referenceTag(project.references[1]), '<Picture 2>');
  assert.match(compileBrief(project), /<Picture 1> Костюм — second\.png\n<Picture 2> Окружение — third\.png/);
});

test('environment display labels use their own sequence while H3 tags stay official', () => {
  let project = createProject();
  project = addReference(project, { id: 'hero', kind: 'image', name: 'hero.png', role: 'Лицо героя' });
  project = addReference(project, { id: 'room-a', kind: 'image', name: 'room-a.png', role: 'Окружение' });
  project = addReference(project, { id: 'costume', kind: 'image', name: 'coat.png', role: 'Костюм' });
  project = addReference(project, { id: 'room-b', kind: 'image', name: 'room-b.png', role: 'Окружение' });
  assert.equal(referenceDisplayTag(project.references[1], project.references), '<Picture 2>');
  assert.equal(referenceDisplayTag(project.references[3], project.references), '<Picture 4>');
  assert.equal(referenceDisplayTag({ kind: 'video', number: 1, role: 'Продолжение' }, []), '↳ Продолжение · без тега');
  assert.equal(referenceTag(project.references[1]), '<Picture 2>');
  assert.equal(referenceTag(project.references[3]), '<Picture 4>');
});

test('deleted referenced picture is marked missing and surviving picture keeps its identity', () => {
  let project = createProject();
  for (const id of ['a', 'b', 'c']) project = addReference(project, { id, kind: 'image', name: `${id}.png`, role: 'Окружение' });
  project.cuts[0].prompt = '<Picture 2> then <Picture 3>';
  project = removeReference(project, 'b');
  assert.deepEqual(project.references.map(referenceTag), ['<Picture 1>', '<Picture 2>']);
  assert.equal(project.cuts[0].prompt, '⟦удалённый Picture 2⟧ then <Picture 2>');
  assert.deepEqual(mentionedReferences(project.cuts[0].prompt, project.references).map((ref) => ref.id), ['c']);
});

test('removing the last image resets its next local label to one', () => {
  let project = addReference(createProject(), { id: 'old', kind: 'image', name: 'old.png' });
  project = removeReference(project, 'old');
  project = addReference(project, { id: 'new', kind: 'image', name: 'new.png', number: 40 });
  assert.equal(referenceTag(project.references[0]), '<Picture 1>');
});

test('old library number nine migrates to draft picture one without changing its asset identity', () => {
  const project = createProject();
  project.references = [{ id: 'asset-nine', kind: 'image', number: 9, name: 'nova.png' }];
  project.cuts[0].prompt = 'Use <Picture 9> for Nova.';
  const migrated = model.renumberReferences(project);
  assert.equal(migrated.references[0].id, 'asset-nine');
  assert.equal(referenceTag(migrated.references[0]), '<Picture 1>');
  assert.equal(migrated.cuts[0].prompt, 'Use <Picture 1> for Nova.');
});

test('attaching the missing third picture reconnects its unknown tag in the saved prompt', () => {
  let project = createProject();
  for (const id of ['nova', 'alex']) project = addReference(project, { id, kind: 'image', name: `${id}.png` });
  project.cuts[0].prompt = 'Keep ⟦неизвестный Picture 3⟧ as the bedroom reference.';
  project = addReference(project, { id: 'bedroom', kind: 'image', name: 'bedroom.png' });
  assert.equal(project.cuts[0].prompt, 'Keep <Picture 3> as the bedroom reference.');
  assert.equal(project.references[2].id, 'bedroom');
  assert.equal(model.renumberReferences(project).cuts[0].prompt, project.cuts[0].prompt);
});

test('rehydrating a draft reconnects an unknown tag once the matching picture is present', () => {
  const project = createProject();
  project.references = ['nova', 'alex', 'bedroom'].map((id, index) => ({ id, kind: 'image', number: index + 1 }));
  project.cuts[0].prompt = 'Room: ⟦неизвестный Picture 3⟧. Missing: ⟦неизвестный Picture 4⟧.';
  assert.equal(model.renumberReferences(project).cuts[0].prompt,
    'Room: <Picture 3>. Missing: ⟦неизвестный Picture 4⟧.');
});

test('audio reference has its own numbering and is included in the brief', () => {
  let project = createProject();
  project = addReference(project, { id: 'a', kind: 'image', name: 'face.png', role: 'Лицо героя' });
  project = addReference(project, { id: 'b', kind: 'audio', name: 'voice.wav', role: 'Голос' });
  assert.equal(referenceTag(project.references[1]), '<Audio 1>');
  assert.match(compileBrief(project), /<Audio 1> Голос — voice\.wav/);
});

test('source-video audio gets the first audio tag before standalone audio', () => {
  let project = createProject();
  project.useVideoAudio = false;
  project = addReference(project, { id: 'video', kind: 'video', name: 'clip.mp4' });
  project = addReference(project, { id: 'voice', kind: 'audio', name: 'voice.wav' });
  project.cuts[0].prompt = '<Audio 1> says hello';
  const previous = project.references;
  project.useVideoAudio = true;
  project = model.renumberReferences(project, previous, false);
  assert.equal(referenceTag(project.references[1]), '<Audio 2>');
  assert.equal(project.cuts[0].prompt, '<Audio 2> says hello');
});

test('supported clipboard media is classified while arbitrary files are rejected', () => {
  assert.equal(mediaKind({ type: 'image/png', name: 'cat.png' }), 'image');
  assert.equal(mediaKind({ type: 'video/mp4', name: 'walk.mp4' }), 'video');
  assert.equal(mediaKind({ type: 'audio/wav', name: 'voice.wav' }), 'audio');
  assert.equal(mediaKind({ type: 'application/pdf', name: 'notes.pdf' }), null);
});

test('selecting a mention replaces only the typed at-query and leaves adjacent prompt intact', () => {
  const prompt = 'Герой с @pic входит в кадр.';
  const caret = prompt.indexOf(' входит');
  assert.deepEqual(findMentionQuery(prompt, caret), { start: 8, query: 'pic' });
  assert.deepEqual(insertReferenceMention(prompt, caret, caret, 8, '<Picture 2>'), {
    text: 'Герой с <Picture 2> входит в кадр.',
    caret: 19,
  });
});

test('at-sign in an email address does not open the reference picker', () => {
  assert.equal(findMentionQuery('name@example.com', 16), null);
});

test('another at-sign immediately after a selected reference opens the picker again', () => {
  assert.deepEqual(findMentionQuery('<Picture 1>@', 12), { start: 11, query: '' });
});

test('selected reference thumbnails follow prompt mentions without duplicating a file', () => {
  let project = createProject();
  project = addReference(project, { id: 'a', kind: 'image', name: 'hero.png', role: 'Лицо героя' });
  project = addReference(project, { id: 'b', kind: 'image', name: 'room.png', role: 'Окружение' });
  project = addReference(project, { id: 'c', kind: 'video', name: 'walk.mp4', role: 'Движение' });
  assert.deepEqual(
    mentionedReferences('Камера <Video 1>, герой <Picture 2> и снова <Picture 2>.', project.references).map((ref) => ref.id),
    ['c', 'b']
  );
});

test('video jobs: no voice casting for a silent scene or for heroes absent from the job', () => {
  const voices = { NOVA: { voice: 'cat' }, ORION: { voice: 'adult_male', timbre: 'хриплый' } };
  const refs = [{ kind: 'image', owner: 'NOVA', number: 1 }, { kind: 'video', number: 1 }];
  const silent = model.addActingVoiceDirection('NOVA dances. No talking cats.', { actingVoices: voices, references: refs });
  assert.doesNotMatch(silent, /VOICE CAST/);
  const text = model.addActingVoiceDirection('NOVA dances and laughs.', { actingVoices: voices, references: refs });
  assert.match(text, /VOICE CAST FOR NOVA:/);
  assert.doesNotMatch(text, /VOICE CAST FOR ORION/);
  // Picture-only jobs are unchanged (picture-only golden graph).
  const pictures = model.addActingVoiceDirection('No talking.', { actingVoices: voices, references: [refs[0]] });
  assert.match(pictures, /VOICE CAST FOR ORION/);
});
