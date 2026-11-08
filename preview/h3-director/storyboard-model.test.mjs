import test from 'node:test';
import assert from 'node:assert/strict';
import {
  frameAccepted, frameAcceptance, establishingFrame,
  bibleVoices, chainStep, chainWarnings, colorSample, continuationStreak, continuationWarning,
  nativeContinuationSource, enqueueOptions, finishedJobIds, jobStatus, moveCut, pendingShots, referencesWithStartFrame, resetFailed,
  sceneSummary, shotPlan, shotStatus, storyboardState, withSceneStyle, shotCardDescription,
  environmentSample, defaultStartFrame, MAX_IMAGES, autoKeyframeRequest,
} from './storyboard-model.mjs';

const jobs = (...list) => new Map(list.map(([job_id, state, extra = {}]) => [job_id, { job_id, state, ...extra }]));
const shot = (id, extra = {}) => ({ id, prompt: `shot ${id}`, duration: 5, ...extra });
const board = (extra = {}) => storyboardState({ storyboard: extra });

test('job states map to board statuses', () => {
  assert.deepEqual(['queued_local', 'uploading', 'queued_gpu', 'running', 'downloading', 'completed', 'failed', 'cancelled'].map((state) => jobStatus({ state })),
    ['queued', 'queued', 'queued', 'running', 'running', 'done', 'failed', 'failed']);
  assert.equal(jobStatus(undefined), 'missing');
});

test('shot card summarizes a structured H3 script instead of exposing its technical preamble', () => {
  const cut=shot('a',{sceneShot:{title:'Дуэль'},prompt:'subject_definitions:\n<Subject 1> is NOVA.\n\nsummary:\n\n[reference generation] NOVA blocks MARK inside the factory and counters.\n\nretention_analysis:\nKeep both characters.'});
  assert.equal(shotCardDescription(cut),'NOVA blocks MARK inside the factory and counters.');
  assert.equal(shotCardDescription(shot('b',{prompt:'subject_definitions:\n<Subject 1> is NOVA.'})),'Сценарий сохранён · открой Inspector для просмотра');
  assert.equal(shotCardDescription(shot('c',{sceneShot:{action:'subject_definitions:\n<Subject 1> is NOVA.'},prompt:'subject_definitions:\nsummary:\nNOVA wins the duel.\n\ndetailed_description:\nLong technical prompt.'})),'NOVA wins the duel.');
});

test('defaults: colour by the scene reference frame (shot 1), strength 0.6', () => {
  assert.deepEqual(board(), { running: false, colorSource: 'first', strength: 0.6, message: '', linkPrevious: true, motionContext: false, autoKeyframes: true, keyframesFirst: true, keyframeCandidates: 1,
    lastFrames: {} });
  assert.equal(board({ colorSource: 'bogus', strength: 7 }).colorSource, 'first');
  assert.equal(board({ strength: 7 }).strength, 0.6);
});

test('reorder moves one shot and keeps the rest', () => {
  const cuts = [shot('a'), shot('b'), shot('c')];
  assert.deepEqual(moveCut(cuts, 'c', -1).map((c) => c.id), ['a', 'c', 'b']);
  assert.deepEqual(moveCut(cuts, 'a', -1).map((c) => c.id), ['a', 'b', 'c']);
  assert.deepEqual(moveCut(cuts, 'a', 1).map((c) => c.id), ['b', 'a', 'c']);
});

test('chain runs in order: first shot enqueued, then waits', () => {
  const cuts = [shot('a'), shot('b', { startFrame: { source: 'previous' } })];
  assert.equal(chainStep(cuts, jobs(), board()).action, 'enqueue');
  assert.equal(chainStep(cuts, jobs(), board()).cutId, 'a');
  const running = [{ ...cuts[0], shotJobId: 'j1' }, cuts[1]];
  assert.deepEqual(chainStep(running, jobs(['j1', 'running']), board()).action, 'wait');
});

test('continuation: last frame of the previous shot is extracted, then used as start frame and colour sample', () => {
  const cuts = [shot('a', { shotJobId: 'j1' }), shot('b', { startFrame: { source: 'previous' } })];
  const done = jobs(['j1', 'completed']);
  assert.deepEqual(chainStep(cuts, done, board()), { action: 'extract', cutId: 'b', index: 1, fromJobId: 'j1' });
  const step = chainStep(cuts, done, board({ lastFrames: { j1: 'frame1' } }));
  assert.equal(step.action, 'enqueue');
  assert.deepEqual(enqueueOptions(step.plan), { startFrameAssetId: 'frame1', colorMatchAssetId: 'frame1', environmentAssetId: null, colorMatchStrength: 0.6 });
  assert.deepEqual(step.plan.startFrame, { source: 'previous', asset_id: 'frame1', from_job: 'j1' });
});

test('colour sample defaults to shot 1, not to the neighbour (no drift)', () => {
  const cuts = [shot('a', { shotJobId: 'j1', startFrame: { source: 'keyframe', asset_id: 'key1' } }), shot('b', { shotJobId: 'j2' }),
    shot('c', { startFrame: { source: 'library', asset_id: 'pic' } })];
  const all = jobs(['j1', 'completed'], ['j2', 'completed']);
  const plan = shotPlan(cuts, 2, all, board({ lastFrames: { j2: 'end2' } }));
  assert.equal(plan.colorMatchAssetId, 'key1');
  assert.equal(plan.startFrameAssetId, 'pic');
  assert.equal(plan.environmentAssetId, 'end2', 'a cut keeps the location of the shot before it');
  // «по предыдущему шоту» needs shot 2's last frame first.
  assert.deepEqual(shotPlan(cuts, 2, all, board({ colorSource: 'previous' })), { ok: true, extractFrom: 'j2' });
  assert.equal(shotPlan(cuts, 2, all, board({ colorSource: 'off', linkPrevious: false })).colorMatchAssetId, null);
  assert.equal(shotPlan([cuts[0], cuts[1], { ...cuts[2], colorMatch: false }], 2, all, board({ linkPrevious: false })).colorMatchAssetId, null);
  // Without a start frame on shot 1 its last frame is the reference.
  assert.deepEqual(colorSample([shot('a', { shotJobId: 'j1' }), shot('b')], 1, board()), { extractFrom: 'j1' });
  assert.deepEqual(colorSample([shot('a', { shotJobId: 'j1' }), shot('b')], 1, board({ lastFrames: { j1: 'f1' } })), { assetId: 'f1' });
  assert.deepEqual(colorSample([shot('a')], 0, board()), { assetId: null });
});

test('resume skips finished shots; a failure stops the chain with the error', () => {
  const cuts = [shot('a', { shotJobId: 'j1' }), shot('b', { shotJobId: 'j2' }), shot('c')];
  const step = chainStep(cuts, jobs(['j1', 'completed'], ['j2', 'failed', { error: 'GPU OOM' }]), board());
  assert.equal(step.action, 'stop');
  assert.match(step.message, /Шот 2 не получился: GPU OOM/);
  const resumed = resetFailed(cuts, jobs(['j1', 'completed'], ['j2', 'failed']));
  assert.equal(resumed[0].shotJobId, 'j1');
  assert.equal(resumed[1].shotJobId, null);
  assert.equal(chainStep(resumed, jobs(['j1', 'completed']), board()).cutId, 'b');
  assert.deepEqual(pendingShots(resumed, jobs(['j1', 'completed'])), [2, 3]);
});

test('a deleted clip stops an automatic resume instead of re-rendering', () => {
  const step = chainStep([shot('a', { shotJobId: 'gone' })], jobs(), board());
  assert.equal(step.action, 'stop');
  assert.match(step.message, /удалён/);
});

test('rerunning a shot makes the continuation after it stale, and the chain regenerates it', () => {
  const cuts = [shot('a', { shotJobId: 'new' }), shot('b', { shotJobId: 'j2', startFrame: { source: 'previous', asset_id: 'f-old', from_job: 'old' } })];
  const all = jobs(['new', 'completed'], ['j2', 'completed']);
  assert.equal(shotStatus(cuts, 1, all), 'stale');
  assert.deepEqual(chainStep(cuts, all, board({ lastFrames: { old: 'f-old' } })), { action: 'extract', cutId: 'b', index: 1, fromJobId: 'new' });
  assert.equal(chainStep(cuts, all, board({ lastFrames: { new: 'f-new' } })).plan.startFrameAssetId, 'f-new');
});

test('done when every shot is finished', () => {
  const cuts = [shot('a', { shotJobId: 'j1' }), shot('b', { shotJobId: 'j2' })];
  assert.equal(chainStep(cuts, jobs(['j1', 'completed'], ['j2', 'completed']), board()).action, 'done');
});

test('clear refusals for shots that cannot start', () => {
  assert.match(shotPlan([shot('a', { prompt: ' ' })], 0, jobs(), board()).message, /нет описания/);
  assert.match(shotPlan([shot('a', { duration: 3 })], 0, jobs(), board()).message, /от 4 до 15/);
  assert.equal(shotPlan([shot('a', { duration: 4 })], 0, jobs(), board()).ok, true);
  assert.match(shotPlan([shot('a', { startFrame: { source: 'previous' } })], 0, jobs(), board()).message, /нет предыдущего/);
  assert.match(shotPlan([shot('a', { startFrame: { source: 'keyframe' } })], 0, jobs(), board()).message, /ключевой кадр ещё не готов/);
  assert.match(shotPlan([shot('a', { startFrame: { source: 'library' } })], 0, jobs(), board()).message, /загрузи/);
  assert.match(shotPlan([shot('a'), shot('b', { startFrame: { source: 'previous' } })], 1, jobs(), board()).message, /дождись готового шота 1/);
});

test('no more than three continuations in a row without a warning', () => {
  const cuts = [shot('a'), ...['b', 'c', 'd', 'e'].map((id) => shot(id, { startFrame: { source: 'previous' } }))];
  assert.deepEqual(cuts.map((c, i) => continuationStreak(cuts, i)), [0, 1, 2, 3, 4]);
  assert.equal(continuationWarning(cuts, 2), '');
  assert.match(continuationWarning(cuts, 3), /предел/);
  assert.match(continuationWarning(cuts, 4), /ключевого кадра/);
  assert.deepEqual(chainWarnings(cuts), [5]);
  const fresh = [...cuts.slice(0, 3), shot('k', { startFrame: { source: 'keyframe', asset_id: 'k' } }), cuts[4]];
  assert.equal(continuationStreak(fresh, 4), 1);
});

test('per-shot start frame: appended as the next consecutive picture, anchored at frame 0', () => {
  const refs = [{ id: 'hero', kind: 'image', number: 1, role: 'Лицо героя' }, { id: 'voice', kind: 'audio', number: 1, role: 'Голос' },
    { id: 'room', kind: 'image', number: 2, role: 'Окружение' }];
  const out = referencesWithStartFrame(refs, { id: 'frame', name: 'f.png' });
  assert.deepEqual(out.guideFrames, [[3, 0]]);
  assert.deepEqual(out.references.map((r) => [r.id, r.number, r.role]),
    [['hero', 1, 'Лицо героя'], ['voice', 1, 'Голос'], ['room', 2, 'Окружение'], ['frame', 3, 'Кадр сцены']]);
  assert.equal(refs.length, 3, 'project references are not mutated');
});

test('per-shot start frame: another shot\'s «Кадр сцены» is demoted, an existing one keeps its number', () => {
  const refs = [{ id: 'old', kind: 'image', number: 1, role: 'Кадр сцены' }, { id: 'hero', kind: 'image', number: 2, role: 'Персонаж целиком', owner: 'ALEX' }];
  assert.deepEqual(referencesWithStartFrame(refs, null).references.map((r) => r.role), ['Окружение', 'Персонаж целиком']);
  assert.deepEqual(referencesWithStartFrame(refs, null).guideFrames, []);
  const reuse = referencesWithStartFrame(refs, { id: 'old' });
  assert.deepEqual(reuse.guideFrames, [[1, 0]]);
  assert.equal(reuse.references.length, 2);
});

test('colour sample joins as «Окружение» after the start frame; wrong role refused; nine pictures max', () => {
  const refs = [{ id: 'hero', kind: 'image', number: 1, role: 'Лицо героя' }];
  const out = referencesWithStartFrame(refs, { id: 'frame' }, { id: 'ref1' });
  assert.deepEqual(out.references.map((r) => [r.id, r.number, r.role]), [['hero', 1, 'Лицо героя'], ['frame', 2, 'Кадр сцены'], ['ref1', 3, 'Окружение']]);
  assert.equal(referencesWithStartFrame(refs, { id: 'frame' }, { id: 'frame' }).references.length, 2);
  assert.throws(() => referencesWithStartFrame(refs, null, { id: 'hero' }), /Окружение/);
  const nine = Array.from({ length: 9 }, (_, i) => ({ id: `p${i}`, kind: 'image', number: i + 1, role: 'Предмет' }));
  assert.throws(() => referencesWithStartFrame(nine, { id: 'frame' }), /не больше 9/);
});

test('scene style goes into free prompts only', () => {
  assert.match(withSceneStyle('A cat walks.', ' warm  pastel 3D '), /Scene style, identical in every shot of this scene: warm pastel 3D\.$/);
  assert.equal(withSceneStyle('A cat walks.', ''), 'A cat walks.');
  assert.equal(withSceneStyle('subject_definitions:\n...', 'warm'), 'subject_definitions:\n...');
});

test('voices of the bible get speaker labels S1, S2', () => {
  const refs = [{ id: 'a', kind: 'audio', number: 1, role: 'Голос', owner: 'Аня' }, { id: 'm', kind: 'audio', number: 2, role: 'Музыка' },
    { id: 'b', kind: 'audio', number: 3, role: 'Голос', owner: 'Боря' }];
  assert.deepEqual(bibleVoices(refs).map((r) => [r.id, r.speaker]), [['a', 'S1'], ['b', 'S2']]);
});

test('glue and summary need every shot finished', () => {
  const accepted = (id, job, duration) => shot(id, { shotJobId: job, duration,
    shotQcReview: 'accepted', shotQc: { job_id: job, decision: 'accept' } });
  const cuts = [accepted('a', 'j1', 5), accepted('b', 'j2', 4)];
  assert.throws(() => finishedJobIds(cuts, jobs(['j1', 'completed'], ['j2', 'running'])), /Не готовы или не приняты шоты: 2/);
  assert.deepEqual(finishedJobIds(cuts, jobs(['j1', 'completed'], ['j2', 'completed'])), ['j1', 'j2']);
  assert.deepEqual(sceneSummary(cuts, jobs(['j1', 'completed'])), { shots: 2, done: 1, seconds: 9 });
});

test('a cut takes the previous shot\'s last frame as «Окружение»; a continuation does not need it', () => {
  const list = [shot('a', { shotJobId: 'j1' }), shot('b')];
  const done = jobs(['j1', 'completed']);
  assert.deepEqual(environmentSample(list, 1, done, board({ colorSource: 'off' })), { extractFrom: 'j1' });
  assert.deepEqual(environmentSample(list, 1, done, board({ colorSource: 'off', lastFrames: { j1: 'f1' } })), { assetId: 'f1' });
  assert.deepEqual(environmentSample(list, 1, done, board({ linkPrevious: false, lastFrames: { j1: 'f1' } })), { assetId: null });
  assert.deepEqual(environmentSample(list, 0, done, board()), { assetId: null });
  assert.deepEqual(environmentSample([list[0], { ...list[1], startFrame: { source: 'previous' } }], 1, done, board({ lastFrames: { j1: 'f1' } })), { assetId: null });
  assert.deepEqual(environmentSample(list, 1, jobs(['j1', 'running']), board()), { assetId: null }, 'a manual run without a ready previous shot goes without it');
  const plan = shotPlan(list, 1, done, board({ colorSource: 'off', lastFrames: { j1: 'f1' } }));
  assert.equal(plan.environmentAssetId, 'f1');
  assert.equal(enqueueOptions(plan).environmentAssetId, 'f1');
  assert.deepEqual(shotPlan(list, 1, done, board({ colorSource: 'off' })), { ok: true, extractFrom: 'j1' });
});

test('chain extracts the previous last frame for a cut before sending it', () => {
  const list = [shot('a', { shotJobId: 'j1' }), shot('b')];
  assert.deepEqual(chainStep(list, jobs(['j1', 'completed']), board({ colorSource: 'off' })),
    { action: 'extract', cutId: 'b', index: 1, fromJobId: 'j1' });
});

test('new shots continue from the previous one, but never more than the continuation limit in a row', () => {
  assert.equal(defaultStartFrame([]), null);
  const cont = { startFrame: { source: 'previous' } };
  assert.deepEqual(defaultStartFrame([shot('a')]), { source: 'previous', asset_id: null });
  assert.deepEqual(defaultStartFrame([shot('a'), cont, cont]), { source: 'previous', asset_id: null });
  assert.equal(defaultStartFrame([shot('a'), cont, cont, cont]), null);
});

test('the environment frame is appended as «Окружение» only while there is room', () => {
  const env = { id: 'f1', name: 'Последний кадр' };
  const { references } = referencesWithStartFrame([{ id: 'hero', kind: 'image', role: 'Лицо героя', number: 1 }], null, null, env);
  assert.deepEqual(references.at(-1), { id: 'f1', kind: 'image', name: 'Последний кадр', role: 'Окружение', number: 2 });
  const full = Array.from({ length: MAX_IMAGES }, (_, i) => ({ id: `r${i}`, kind: 'image', role: 'Предмет', number: i + 1 }));
  assert.equal(referencesWithStartFrame(full, null, null, env).references.length, MAX_IMAGES);
  const same = referencesWithStartFrame([], null, env, env).references;
  assert.equal(same.length, 1, 'colour sample and environment frame are the same picture');
});

test('«Собрать сцену» makes a missing keyframe itself: place from the previous last frame, only heroes named in the shot', () => {
  const refs = [
    { id: 'loc', kind: 'image', role: 'Окружение' }, { id: 'nova', kind: 'image', role: 'Лицо героя', owner: 'NOVA' },
    { id: 'orion', kind: 'image', role: 'Лицо героя', owner: 'ORION' }, { id: 'voice', kind: 'audio', role: 'Голос' },
  ];
  const kf = { startFrame: { source: 'keyframe', asset_id: null } };
  const first = autoKeyframeRequest([shot('a', { ...kf, sceneShot: { action: 'ORION lights a cigar', framing: 'close', lens_mm: 85 } })], 0, board(), refs);
  assert.deepEqual(first.asset_ids, ['loc', 'orion']);
  assert.deepEqual(first.roles, ['Окружение', 'Лицо героя']);
  assert.match(first.prompt, /ORION lights a cigar/);
  const separate = autoKeyframeRequest([shot('a', { ...kf, sceneShot: { first_frame: 'NOVA stands beside the closed door', action: 'NOVA runs through the open door', characters: ['NOVA'] } })], 0, board(), refs);
  assert.match(separate.prompt, /NOVA stands beside the closed door/);
  assert.doesNotMatch(separate.prompt, /NOVA runs through the open door/);
  assert.deepEqual(separate.asset_ids, ['loc', 'nova']);
  const emptyCast = autoKeyframeRequest([shot('a', { ...kf, sceneShot: { first_frame: 'An empty room', characters: [] } })], 0, board(), refs);
  assert.deepEqual(emptyCast.asset_ids, ['loc']);
  const actionContext = autoKeyframeRequest([shot('a', { ...kf, sceneShot: { action: 'NOVA reaches toward the glass', purpose: 'ORION catches NOVA paw at the counter' } })], 0, board(), refs, { maxImages: 8 });
  assert.match(actionContext.prompt, /ORION catches NOVA paw/);
  assert.ok(actionContext.asset_ids.includes('orion'), 'the first frame receives a hero needed by the shot purpose');
  assert.match(first.prompt, /close, 85 mm lens/);
  const cuts = [shot('a', { shotJobId: 'j1' }), shot('b', kf)];
  const next = autoKeyframeRequest(cuts, 1, board({ lastFrames: { j1: 'end1' } }), refs, { aspect: '9:16' });
  assert.deepEqual(next.asset_ids, ['end1', 'nova', 'orion'], 'nobody named: the scene heroes in order');
  assert.equal(next.roles[0], 'Окружение', 'the last frame gives the place, not the old camera angle');
  assert.equal(next.size, '768x1344');
  assert.match(next.prompt, /same place, lighting and props as image 1/);
  assert.equal(autoKeyframeRequest([shot('a', kf)], 0, board(), []).ok, false);
});

test('chain: extract the previous end, order the keyframe, wait for it, then send the shot', () => {
  const refs = [{ id: 'nova', kind: 'image', role: 'Лицо героя', owner: 'NOVA' }];
  const kf = { source: 'keyframe', asset_id: null };
  const done = jobs(['j1', 'completed']);
  const cuts = [shot('a', { shotJobId: 'j1' }), shot('b', { startFrame: kf })];
  assert.deepEqual(chainStep(cuts, done, board({ colorSource: 'off' }), refs), { action: 'extract', cutId: 'b', index: 1, fromJobId: 'j1' });
  const ordered = chainStep(cuts, done, board({ colorSource: 'off', lastFrames: { j1: 'end1' } }), refs);
  assert.equal(ordered.action, 'keyframe');
  assert.deepEqual(ordered.request.asset_ids, ['end1', 'nova']);
  const waiting = [cuts[0], { ...cuts[1], keyframeJobId: 'k1' }];
  assert.deepEqual(chainStep(waiting, done, board(), refs), { action: 'keyframe-wait', cutId: 'b', index: 1, keyframeIds: ['k1'] });
  assert.deepEqual(chainStep([cuts[0], { ...cuts[1], keyframeJobIds: ['k1', 'k2'] }], done, board(), refs).keyframeIds, ['k1', 'k2']);
  assert.equal(ordered.candidates, 1, "one keyframe per shot: the user accepts it, no Qwen pick");
  assert.equal(board({ keyframeCandidates: 3 }).keyframeCandidates, 1);
  const ready = [cuts[0], { ...cuts[1], startFrame: { source: 'keyframe', asset_id: 'key2' } }];
  const step = chainStep(ready, done, board({ colorSource: 'off', lastFrames: { j1: 'end1' }, keyframesFirst: false }), refs);
  assert.equal(step.action, 'enqueue');
  assert.equal(step.plan.startFrameAssetId, 'key2');
  assert.equal(chainStep(cuts, done, board({ autoKeyframes: false, lastFrames: { j1: 'end1' } }), refs).action, 'stop', 'switched off: the old manual stop');
});

test('scene run waits for the exact finished take to be accepted before continuing', () => {
  const first = shot('a', { shotJobId: 'j1' });
  const second = shot('b', { startFrame: { source: 'previous' } });
  const finished = jobs(['j1', 'completed']);
  const scene = { ...board({ colorSource: 'off', lastFrames: { j1: 'end1' } }), qcGate: true };
  assert.equal(chainStep([first, second], finished, scene).action, 'qc-wait');
  assert.equal(chainStep([{ ...first, shotQcReview: 'accepted', shotQc: { job_id: 'old' } }, second], finished, scene).action, 'qc-wait');
  assert.equal(chainStep([{ ...first, shotQcReview: 'accepted', shotQc: { job_id: 'j1', decision: 'accept' } }, second], finished, scene).action, 'enqueue');
  assert.equal(chainStep([{ ...first, shotQcReview: 'accepted', shotQc: { job_id: 'j1', decision: 'manual_review' },
    takeReview: { state: 'flagged' } }, second], finished, scene).action, 'qc-wait');
});

test('native video continuation accepts only QC of the exact previous take', () => {
  const plan = {startFrame:{source:'previous',from_job:'old'}};
  assert.throws(() => nativeContinuationSource(plan,{shotJobId:'new'}));
  assert.throws(() => nativeContinuationSource(plan,{shotJobId:'old',shotQcReview:'accepted',shotQc:{job_id:'another',decision:'regenerate'}}));
  assert.throws(() => nativeContinuationSource(plan,{shotJobId:'old',shotQc:{job_id:'old',decision:'accept'}}));
  assert.equal(nativeContinuationSource(plan,{shotJobId:'old',shotQcReview:'accepted',shotQc:{job_id:'old',decision:'accept'}}),'old');
  assert.equal(nativeContinuationSource(plan,{shotJobId:'old',shotQcReview:'accepted',shotQc:{job_id:'old',decision:'manual_review'}}),'old');
});

test('a staging edit does not block a continuation: its start frame is the previous shot\'s last frame', () => {
  const cuts = [shot('a', { shotJobId: 'j1' }),
    shot('b', { startFrame: { source: 'previous', asset_id: 'frame1' }, productionFrameChanged: true })];
  assert.equal(shotPlan(cuts, 1, jobs(['j1', 'completed']), board({ lastFrames: { j1: 'frame1' } })).ok, true);
  const keyframed = [cuts[0], { ...cuts[1], startFrame: { source: 'keyframe', asset_id: 'key' } }];
  assert.match(shotPlan(keyframed, 1, jobs(['j1', 'completed']), board()).message, /постановка изменилась/);
});

test('composition first: every keyframe is made, video waits for the accepted frame of its shot', () => {
  const refs = [{ id: 'yard', kind: 'image', role: 'Окружение' }, { id: 'iris', kind: 'image', role: 'Лицо героя', owner: 'IRIS' }];
  const cuts = [shot('a', { startFrame: { source: 'keyframe', asset_id: 'k1' } }),
    shot('b', { startFrame: { source: 'previous' } }),
    shot('c', { startFrame: { source: 'keyframe', asset_id: null } })];
  // Shot 1 has a frame nobody accepted yet: no video, and shot 3's keyframe waits for it — shot 1's accepted
  // frame fixes the room; shot 2 continues shot 1's video, so it waits too.
  const first = chainStep(cuts, jobs(), board({ keyframesFirst: true }), refs);
  assert.equal(first.action, 'stop');
  assert.deepEqual(first.awaiting, [1]);
  assert.match(first.message, /Принять кадр/);
  assert.match(first.message, /от принятого общего плана/);
  // Accepted: the record holds the frame, the staging version and the references.
  const one = [{ ...cuts[0], frameAcceptance: frameAcceptance(cuts[0], refs, 't') }, cuts[1], cuts[2]];
  assert.deepEqual(one[0].frameAcceptance, { asset_id: 'k1', spec_revision: null, reference_ids: ['yard', 'iris'], reference_bindings: [['yard', 'Окружение', ''], ['iris', 'Лицо героя', 'IRIS']], keyframe_job_ids: [], accepted_at: 't' });
  assert.equal(establishingFrame(one, 2), 'k1');
  assert.equal(frameAccepted(one[0], [{ ...refs[0] }, { ...refs[1], owner: 'MIRA' }]), false);
  assert.equal(frameAccepted(one[0], [{ ...refs[0] }, { ...refs[1], id: 'replacement' }]), false);
  // Video of shot 1 goes first (strict order); with shot 1 rendered, shot 3's keyframe is drawn from shot 1's frame.
  assert.equal(chainStep(one, jobs(), board(), refs).action, 'enqueue');
  const drawn = autoKeyframeRequest(one, 2, board(), refs);
  assert.deepEqual(drawn.asset_ids, ['k1', 'iris']);
  assert.deepEqual(drawn.roles, ['Окружение', 'Лицо героя']);
  assert.match(drawn.prompt, /accepted establishing frame/);
  const framed = [one[0], cuts[1], { ...cuts[2], startFrame: { source: 'keyframe', asset_id: 'k3' } }];
  const stop = chainStep(framed, jobs(), board(), refs);
  assert.equal(stop.action, 'enqueue', 'shot 1 is accepted: its video goes; shot 3 waits for its own acceptance later');
  const accepted = framed.map((cut) => cut.startFrame.asset_id ? { ...cut, frameAcceptance: frameAcceptance(cut, refs, 't') } : cut);
  assert.equal(chainStep(accepted, jobs(), board(), refs).action, 'enqueue');
  // A new frame or a staging edit makes the old acceptance void.
  assert.equal(frameAccepted({ ...accepted[0], startFrame: { source: 'keyframe', asset_id: 'k1b' } }), false);
  assert.equal(frameAccepted({ ...accepted[0], productionSpec: { shot: { revision: 2 } } }), false);
  assert.equal(chainStep(framed, jobs(), board({ keyframesFirst: false }), refs).action, 'enqueue', 'switched off: video right away');
});

test('scene run: stand-ins name the source, so one source is one reference', async () => {
  const { runSources, runTokens, storyboardState } = await import('./storyboard-model.mjs');
  const board = storyboardState({ storyboard: { colorSource: 'first' } });
  const cuts = [{ id: 'a', startFrame: { source: 'keyframe', asset_id: null } },
    { id: 'b', startFrame: { source: 'previous', asset_id: null } },
    { id: 'c', startFrame: null }];
  assert.deepEqual(runSources(cuts, 0, board), { mode: 'keyframe', startFrameAssetId: 'studio-run:start:a', colorMatchAssetId: null,
    environmentAssetId: null, continuationAssetId: null, colorMatchStrength: 0.6 });
  const second = runSources(cuts, 1, board);
  assert.equal(second.startFrameAssetId, 'studio-run:last:a');
  assert.equal(second.colorMatchAssetId, 'studio-run:start:a');  // colour of the scene's first accepted frame
  assert.equal(second.environmentAssetId, null);                   // a continuation already starts there
  const third = runSources(cuts, 2, board);
  assert.equal(third.startFrameAssetId, null);
  assert.equal(third.environmentAssetId, 'studio-run:last:b');
  const motion = runSources(cuts, 1, { ...board, motionContext: true });
  assert.equal(motion.startFrameAssetId, null);
  assert.equal(motion.continuationAssetId, 'studio-run:tail:a');
  assert.deepEqual(runTokens({ ids: ['x', 'studio-run:last:b', 'studio-run:last:b'] }), ['studio-run:last:b']);
});

test('a keyframe gets only its own shot materials and place', async () => {
  const { keyframeReferences, autoKeyframeRequest } = await import('./storyboard-model.mjs');
  const refs = [{ id: 'apt', kind: 'image', role: 'Окружение', number: 3 },
    { id: 'k', kind: 'image', role: 'Лицо героя', owner: 'ALEX', number: 1 },
    { id: 'a', kind: 'image', role: 'Персонаж целиком', owner: 'NOVA', number: 2 }];
  const places = [{ name: 'Apartment', aliases: ['apartment'], assets: ['apt'] }];
  const balcony = { id: 'c1', prompt: '<Subject 3> is the environment: Enclosed balcony: snow.',
    sceneShot: { characters: ['ALEX', 'NOVA'], first_frame: 'NOVA at the threshold' }, startFrame: { source: 'keyframe' } };
  const request = autoKeyframeRequest([balcony], 0, {}, keyframeReferences(balcony, refs, places), {});
  assert.deepEqual(request.asset_ids, ['k', 'a']);  // 31.10 «White Planet»: no living room in the balcony keyframe
  assert.deepEqual(keyframeReferences({ ...balcony, offRefs: ['a'] }, refs, []).map((ref) => ref.id), ['apt', 'k']);
  assert.equal(keyframeReferences({ prompt: 'is the environment: the apartment.' }, refs, places).length, 3);
});

test('a two-hero keyframe gives each hero a picture before either gets a second', () => {
  const refs = [
    { id: 'lane', kind: 'image', role: 'Окружение' },
    { id: 'b-face', kind: 'image', role: 'Лицо героя', owner: 'IRIS' }, { id: 'b-body', kind: 'image', role: 'Персонаж целиком', owner: 'IRIS' },
    { id: 'o-face', kind: 'image', role: 'Лицо героя', owner: 'MIRA' }, { id: 'o-body', kind: 'image', role: 'Персонаж целиком', owner: 'MIRA' },
  ];
  const cut = shot('a', { startFrame: { source: 'keyframe', asset_id: null },
    sceneShot: { first_frame: 'IRIS and MIRA at the gate', characters: ['IRIS', 'MIRA'] } });
  assert.deepEqual(autoKeyframeRequest([cut], 0, board(), refs).asset_ids, ['lane', 'b-body', 'o-body']);
  assert.deepEqual(autoKeyframeRequest([cut], 0, board(), refs, { maxImages: 5 }).asset_ids, ['lane', 'b-body', 'o-body', 'b-face', 'o-face']);
});
