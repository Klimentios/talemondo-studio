// «Сцены»: pure model of the scene pipeline — scene bible, shot board, «Собрать сцену».
// One shot = one cut of project.cuts; per-shot data lives on the cut and survives a reload
// with the project draft:
//   cut.startFrame = { source: 'library'|'previous'|'keyframe', asset_id?, from_job? } | null
//   cut.colorMatch = false to switch colour matching off for this shot (default on)
//   cut.shotJobId  = job id of the latest generation of this shot
// project.storyboard = { running, colorSource: 'first'|'previous'|'off', strength, message,
//                        linkPrevious, autoKeyframes, keyframeCandidates, lastFrames: {jobId: assetId}, lastConcat }
// linkPrevious (default on): a shot that is not a continuation (a cut) still gets the previous
// shot's last frame as «Окружение», so location, light and props carry over across the cut.
// project.sceneStyle = one line of style for every shot (prompt text).
import { productionFrameRequest } from './production-frame.mjs?v=20261029-establish-1';
import { placeOff } from './shot-references.mjs?v=20261101-breakdown-1';

export const DEFAULT_STRENGTH = 0.6;
export const MAX_IMAGES = 9; // H3 Ref2VA limit, app/reference_manifest.py
// Community practice with H3 keyframe continuation: after 2–3 «last frame → next shot» hops
// identity and colour drift; the next shot should start from a fresh keyframe.
export const MAX_CONTINUATIONS = 3;
export const MIN_SECONDS = 4; // official H3 README: output duration 4–15 s
export const MAX_SECONDS = 15;

const QUEUED = new Set(['queued_local', 'uploading', 'queued_gpu']);
const RUNNING = new Set(['running', 'stopping', 'downloading', 'needs_reconciliation']);
const FAILED = new Set(['failed', 'cancelled', 'interrupted']);
const COLOR_SOURCES = new Set(['first', 'previous', 'off']);

export const STATUS_LABELS = {
  idle: 'Не запускался', queued: 'В очереди', running: 'Генерируется', done: 'Готов',
  failed: 'Ошибка', stale: 'Устарел', missing: 'Ролик удалён',
};
export const LINK_LABELS = {
  none: 'Свободный шот', library: 'С картинки', previous: 'Продолжение', keyframe: 'Ключевой кадр',
};

export function storyboardState(project) {
  const value = project?.storyboard || {};
  const strength = Number(value.strength);
  return {
    running: Boolean(value.running),
    colorSource: COLOR_SOURCES.has(value.colorSource) ? value.colorSource : 'first',
    strength: Number.isFinite(strength) && strength >= 0.2 && strength <= 1 ? strength : DEFAULT_STRENGTH,
    message: typeof value.message === 'string' ? value.message : '',
    linkPrevious: value.linkPrevious !== false,
    motionContext: value.motionContext === true,
    autoKeyframes: value.autoKeyframes !== false,
    // The generated start frame is checked before video generation.
    keyframesFirst: value.keyframesFirst !== false,
    // One keyframe per shot, drawn by Codex: the choice is the user's «✓ Принять кадр» (or Claude's check).
    keyframeCandidates: 1,
    lastFrames: value.lastFrames && typeof value.lastFrames === 'object' ? value.lastFrames : {},
  };
}

export function jobStatus(job) {
  if (!job) return 'missing';
  if (job.state === 'completed') return 'done';
  if (QUEUED.has(job.state)) return 'queued';
  if (RUNNING.has(job.state)) return 'running';
  if (FAILED.has(job.state)) return 'failed';
  return 'queued';
}

export function jobProgress(job) {
  return job?.state === 'running' && job.sampling_max > 0 ? Math.floor(100 * job.sampling_value / job.sampling_max) : null;
}

// A finished continuation is stale once the previous shot was regenerated: its first frame
// no longer matches the clip before it.
export function shotStatus(cuts, index, jobsById) {
  const cut = cuts[index];
  if (!cut?.shotJobId) return 'idle';
  const status = jobStatus(jobsById.get(cut.shotJobId));
  if (status === 'done' && cut.productionSpecChanged) return 'stale';
  if (status !== 'done' || cut.startFrame?.source !== 'previous' || index === 0) return status;
  return cut.startFrame.from_job && cut.startFrame.from_job === cuts[index - 1]?.shotJobId ? 'done' : 'stale';
}

/** The user's acceptance of this shot's start frame, still valid for the frame and staging the shot has now. */
const referenceBindings = (references) => references.map((ref) => [ref.id || ref.asset_id || '', ref.role || '', ref.owner || '']);
export function frameAccepted(cut, references) {
  const accepted = cut?.frameAcceptance;
  if (references && accepted) {
    if (JSON.stringify(accepted.reference_ids || []) !== JSON.stringify(references.map(ref => ref.id || ref.asset_id))) return false;
    if (accepted.reference_bindings && JSON.stringify(accepted.reference_bindings) !== JSON.stringify(referenceBindings(references))) return false;
  }
  return Boolean(accepted?.asset_id && accepted.asset_id === cut?.startFrame?.asset_id && !cut.productionFrameChanged
    && (accepted.spec_revision ?? null) === (cut.productionSpec?.shot?.revision ?? null));
}

/** What gets recorded when the user accepts a start frame: the frame, the staging version and the references. */
export function frameAcceptance(cut, references, now = new Date().toISOString()) {
  return { asset_id: cut.startFrame?.asset_id || null, spec_revision: cut.productionSpec?.shot?.revision ?? null,
    reference_ids: (references || []).map((ref) => ref.id || ref.asset_id), reference_bindings: referenceBindings(references || []),
    keyframe_job_ids: cut.keyframeJobIds || [], accepted_at: now };
}

export function linkMode(cut) {
  return LINK_LABELS[cut?.startFrame?.source] ? cut.startFrame.source : 'none';
}

export function shotCardDescription(cut) {
  const technical = /^(?:subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music):/i;
  const humanText = [cut?.sceneShot?.action, cut?.sceneShot?.purpose]
    .map((value) => String(value || '').trim())
    .find((value) => value && !technical.test(value));
  if (humanText) return humanText.replace(/\s+/g, ' ').slice(0, 110);
  const prompt = String(cut?.prompt || '').trim();
  const summary = prompt.match(/(?:^|\n)summary:\s*\n+([\s\S]*?)(?=\n(?:retention_analysis|detailed_description|overall_soundscape|non_diegetic_music):|$)/i)?.[1]
    ?.replace(/^\[reference generation\]\s*/i, '').trim();
  if (summary) return summary.replace(/\s+/g, ' ').slice(0, 110);
  if (technical.test(prompt))
    return 'Сценарий сохранён · открой Inspector для просмотра';
  const prose = String(cut?.sceneShot?.action || cut?.sceneShot?.purpose || prompt || 'Добавь действие').trim();
  return prose.replace(/\s+/g, ' ').slice(0, 110);
}

// How many «продолжение» shots in a row end at this one (1 = the first continuation).
export function continuationStreak(cuts, index) {
  let streak = 0;
  for (let at = index; at > 0 && linkMode(cuts[at]) === 'previous'; at -= 1) streak += 1;
  return streak;
}

export function continuationWarning(cuts, index) {
  const streak = continuationStreak(cuts, index);
  if (streak < MAX_CONTINUATIONS) return '';
  return streak === MAX_CONTINUATIONS
    ? `${streak}-е продолжение подряд — это предел: следующий шот начни с нового ключевого кадра.`
    : `${streak} продолжения подряд: лицо, костюм и цвет начнут «плыть». Начни этот шот с нового ключевого кадра.`;
}

export function chainWarnings(cuts) {
  return cuts.flatMap((cut, index) => (continuationStreak(cuts, index) > MAX_CONTINUATIONS ? [index + 1] : []));
}

function lastFrameOf(board, jobId) { return jobId ? board.lastFrames[jobId] || null : null; }

/** The scene's establishing frame for a new-plan keyframe: the nearest earlier start frame the user accepted.
 *  Keyframes drawn from it keep one room (desk, window, door, light) instead of each inventing its own. */
export function establishingFrame(cuts, index) {
  for (let at = index - 1; at >= 0; at -= 1) if (frameAccepted(cuts[at])) return cuts[at].startFrame.asset_id;
  return null;
}

// The colour sample of a shot. Default «first»: the scene's reference frame — shot 1's start
// frame, else the last frame of shot 1 — so colour does not drift from neighbour to neighbour.
export function colorSample(cuts, index, board) {
  const cut = cuts[index];
  if (index === 0 || board.colorSource === 'off' || cut?.colorMatch === false) return { assetId: null };
  if (board.colorSource === 'previous') {
    const previous = cuts[index - 1];
    if (linkMode(cut) === 'previous') return { assetId: null, sameAsStart: true };
    const asset = lastFrameOf(board, previous.shotJobId);
    return asset ? { assetId: asset } : { extractFrom: previous.shotJobId };
  }
  const first = cuts[0];
  if (first.startFrame?.asset_id && linkMode(first) !== 'previous') return { assetId: first.startFrame.asset_id };
  const asset = lastFrameOf(board, first.shotJobId);
  return asset ? { assetId: asset } : { extractFrom: first.shotJobId };
}

// The location sample of a cut: the previous shot's last frame as «Окружение». A continuation
// already starts from that frame; a shot whose previous one is not generated yet goes without it
// (the chain never gets there: it runs shots in order).
export function environmentSample(cuts, index, jobsById, board) {
  if (!board.linkPrevious || index === 0 || linkMode(cuts[index]) === 'previous') return { assetId: null };
  const previous = cuts[index - 1];
  if (!previous?.shotJobId || shotStatus(cuts, index - 1, jobsById) !== 'done') return { assetId: null };
  const asset = lastFrameOf(board, previous.shotJobId);
  return asset ? { assetId: asset } : { extractFrom: previous.shotJobId };
}

// Link of a newly added shot: continue from the end of the previous one, unless that would be
// one continuation too many — then a cut (which still keeps the location via linkPrevious).
export function defaultStartFrame(cuts) {
  if (!cuts.length) return null;
  return continuationStreak([...cuts, { startFrame: { source: 'previous' } }], cuts.length) > MAX_CONTINUATIONS
    ? null : { source: 'previous', asset_id: null };
}

// «Собрать сцену» makes a missing keyframe itself (Qwen-Image-Edit, max 3 photos for 2511).
// Picture 1 = the place: the previous shot's last frame («Окружение»: same location, light and
// props, new camera from the text), or for the first shot the scene's location / frame picture.
// The rest = heroes of the scene, those named in the shot text first.
export const KEYFRAME_MAX_IMAGES = 3;
const PLACE_ROLES = ['Кадр сцены', 'Окружение', 'Композиция'];
const HERO_ROLES = ['Лицо героя', 'Персонаж целиком', 'Костюм'];

const FIRST_PICK = ['Персонаж целиком', 'Лицо героя', 'Костюм'];

/** Heroes' pictures in rounds: each owner's full body (else face, else costume) first, then the rest in file order. */
export function fairHeroOrder(heroes) {
  const owners = [...new Set(heroes.map((ref) => String(ref.owner || '').toLowerCase()))];
  const first = owners.map((owner) => heroes.filter((ref) => String(ref.owner || '').toLowerCase() === owner)
    .sort((a, b) => FIRST_PICK.indexOf(a.role) - FIRST_PICK.indexOf(b.role))[0]);
  return [...first, ...heroes.filter((ref) => !first.includes(ref))];
}

export function autoKeyframeRequest(cuts, index, board, references, { sceneStyle = '', aspect = '16:9', maxImages = KEYFRAME_MAX_IMAGES } = {}) {
  const cut = cuts[index];
  const fromPrevious = index > 0 ? lastFrameOf(board, cuts[index - 1]?.shotJobId) : null;
  const established = fromPrevious ? null : establishingFrame(cuts, index);
  if (cut.productionSpec) return productionFrameRequest(cut, references || [], { sceneStyle, aspect, maxImages,
    previousFrame: fromPrevious || established });
  const images = (references || []).filter((ref) => ref.kind === 'image');
  const place = fromPrevious || established ? null : images.find((ref) => PLACE_ROLES.includes(ref.role));
  const text = String(cut?.sceneShot?.first_frame || cut?.sceneShot?.action || cut?.prompt || '').trim();
  const castText = `${text} ${cut?.sceneShot?.purpose || ''}`.toLowerCase();
  const named = (ref) => ref.owner && castText.includes(ref.owner.toLowerCase());
  const cast = images.filter((ref) => HERO_ROLES.includes(ref.role) && ref.id !== place?.id);
  // Heroes named in the shot only; a shot that names nobody gets the scene's heroes in order.
  const hasCast = Array.isArray(cut?.sceneShot?.characters);
  const declared = hasCast ? cut.sceneShot.characters.map((name) => String(name).toLowerCase()) : [];
  const heroes = hasCast ? cast.filter((ref) => declared.includes(String(ref.owner || '').toLowerCase()))
    : cast.some(named) ? cast.filter(named) : cast;
  const picked = [];
  if (fromPrevious || established) picked.push({ id: fromPrevious || established, role: 'Окружение' });
  else if (place) picked.push({ id: place.id, role: place.role === 'Композиция' ? 'Композиция' : 'Окружение' });
  // Every hero gets one picture before anyone gets a second: two heroes with a face and a body each filled all
  // three slots with the first hero, and the second was drawn from a guess (01.11).
  for (const ref of fairHeroOrder(heroes)) if (picked.length < maxImages && !picked.some((item) => item.id === ref.id)) picked.push({ id: ref.id, role: ref.role });
  if (!picked.length && images[0]) picked.push({ id: images[0].id, role: images[0].role });
  if (!picked.length) return { ok: false, message: `Шот ${index + 1}: для ключевого кадра нужно хотя бы одно фото в материалах сцены (герой или локация)` };
  const camera = [cut?.sceneShot?.framing, cut?.sceneShot?.lens_mm ? `${cut.sceneShot.lens_mm} mm lens` : '', cut?.sceneShot?.camera_motion]
    .filter(Boolean).join(', ');
  const prompt = [
    `First frame of the shot: ${text.slice(0, 2400)}`,
    cut?.sceneShot?.purpose ? `Upcoming action this frame must physically support: ${String(cut.sceneShot.purpose).slice(0, 500)}. Show any hero or prop needed for the opening beat, without staging later arrivals early.` : '',
    camera ? `Camera: ${camera}.` : '',
    fromPrevious ? 'This is the next shot of the same scene: the same place, lighting and props as image 1, seen from the new camera position described above.' : '',
    established ? 'Same room as image 1, the accepted establishing frame of this scene: keep the furniture, its positions, the window, the door and the light; only the camera moves to the position described above.' : '',
    sceneStyle ? `Scene style: ${String(sceneStyle).slice(0, 300)}.` : '',
  ].filter(Boolean).join('\n');
  return { ok: true, prompt: prompt.slice(0, 3800), asset_ids: picked.map((item) => item.id), roles: picked.map((item) => item.role),
    size: aspect === '9:16' ? '768x1344' : aspect === '1:1' ? '1024x1024' : '1344x768',
    role: 'Кадр сцены', keep_identity: true, framing: 'center' };
}

// Keyframe jobs a shot waits for (keyframeJobId is the single-job form of older drafts).
export function keyframeJobsOf(cut) {
  if (Array.isArray(cut?.keyframeJobIds) && cut.keyframeJobIds.length) return cut.keyframeJobIds;
  return cut?.keyframeJobId ? [cut.keyframeJobId] : [];
}

// Everything one shot needs before it can be enqueued:
//   { ok:false, message } | { ok:true, extractFrom }
//   | { ok:true, startFrameAssetId, colorMatchAssetId, environmentAssetId, startFrame }
export function shotPlan(cuts, index, jobsById, board) {
  const cut = cuts[index];
  const label = `Шот ${index + 1}`;
  // A continuation starts from the previous shot's last frame: this shot's staging does not make it stale.
  if (cut?.productionFrameChanged && cut.startFrame?.asset_id && linkMode(cut) !== 'previous')
    return { ok: false, message: `${label}: постановка изменилась — создай новый опорный кадр или явно выбери подходящий заново` };
  if (!cut?.prompt?.trim()) return { ok: false, message: `${label}: нет описания` };
  const duration = Number(cut.duration);
  if (!Number.isInteger(duration) || duration < MIN_SECONDS || duration > MAX_SECONDS)
    return { ok: false, message: `${label}: длительность от ${MIN_SECONDS} до ${MAX_SECONDS} секунд` };
  const mode = linkMode(cut);
  let startFrameAssetId = null;
  let startFrame = cut.startFrame || null;
  if (mode === 'library') {
    if (!cut.startFrame.asset_id) return { ok: false, message: `${label}: выбери стартовый кадр — загрузи или возьми из медиатеки` };
    startFrameAssetId = cut.startFrame.asset_id;
  } else if (mode === 'keyframe') {
    if (!cut.startFrame.asset_id) return { ok: false, message: `${label}: ключевой кадр ещё не готов — сделай его кнопкой ✨ или загрузи картинку` };
    startFrameAssetId = cut.startFrame.asset_id;
  } else if (mode === 'previous') {
    if (index === 0) return { ok: false, message: `${label}: у первого шота нет предыдущего — выбери картинку или ключевой кадр` };
    const previous = cuts[index - 1];
    if (!previous.shotJobId || shotStatus(cuts, index - 1, jobsById) !== 'done')
      return { ok: false, message: `${label}: сначала дождись готового шота ${index} — этот начнётся с его последнего кадра` };
    startFrameAssetId = lastFrameOf(board, previous.shotJobId);
    if (!startFrameAssetId) return { ok: true, extractFrom: previous.shotJobId };
    startFrame = { source: 'previous', asset_id: startFrameAssetId, from_job: previous.shotJobId };
  }
  const colour = colorSample(cuts, index, board);
  if ('extractFrom' in colour) {
    const source = cuts.findIndex((item) => item.shotJobId && item.shotJobId === colour.extractFrom);
    if (source < 0 || shotStatus(cuts, source, jobsById) !== 'done')
      return { ok: false, message: `${label}: образец цвета ещё не готов — сначала нужен готовый шот ${board.colorSource === 'previous' ? index : 1} (или выключи подгонку цвета у этого шота)` };
    return { ok: true, extractFrom: colour.extractFrom };
  }
  const place = environmentSample(cuts, index, jobsById, board);
  if (place.extractFrom) return { ok: true, extractFrom: place.extractFrom };
  return { ok: true, startFrameAssetId, startFrame, colorMatchAssetId: colour.sameAsStart ? startFrameAssetId : colour.assetId,
    colorMatchStrength: board.strength, environmentAssetId: place.assetId };
}

export function acceptedShot(cut) {
  const job = cut?.shotJobId;
  const qc = cut?.shotQc;
  const review = cut?.takeReview;
  return Boolean(job && !cut.productionSpecChanged
    && (cut.selectedTakeId === undefined ? job : cut.selectedTakeId) === job
    && cut.shotQcReview === 'accepted' && qc?.job_id === job
    && ['accept', 'adapt_next', 'manual_review'].includes(qc.decision)
    && (!review || Object.keys(review).length === 0
      || (review.state === 'accepted' && review.job_id === job)));
}

// Next step of «Собрать сцену»: shots strictly in order; a finished shot is skipped (resume),
// a stale continuation is regenerated, the first failure stops the chain.
// A keyframe sees the materials its own shot sends to H3: not the ones switched off for it on the canvas (offRefs),
// not a location picture of another passport place (otherwise a balcony keyframe gets drawn from the
// living-room photo, and the video starts from it). `places`: scene kit places (scene_runs passes them).
export function keyframeReferences(cut, references = [], places = []) {
  const off = new Set([...(cut?.offRefs || []), ...placeOff(references, cut?.prompt || '', cut?.sceneShot?.location, places || [])]);
  return off.size ? references.filter((ref) => !off.has(ref.id)) : references;
}

export function chainStep(cuts, jobsById, board, references = [], keyframeOptions = {}) {
  const awaiting = [];  // keyframesFirst: shots whose start frame waits for the user's acceptance
  for (const [index, cut] of cuts.entries()) {
    const status = shotStatus(cuts, index, jobsById);
    if (index > 0 && board.qcGate) {
      const previous = cuts[index - 1];
      if (shotStatus(cuts, index - 1, jobsById) === 'done' && !acceptedShot(previous)) {
        return { action: 'qc-wait', cutId: previous.id, index: index - 1,
          message: `Шот ${index} завершён. Проверь весь MP4, действие, героев и последний кадр; прими дубль перед следующим шотом.` };
      }
    }
    if (status === 'done') continue;
    // After an unaccepted frame only the keyframes of later new-plan shots are made; videos keep their order.
    if (awaiting.length && linkMode(cut) !== 'keyframe') continue;
    if (status === 'queued' || status === 'running') return { action: 'wait', cutId: cut.id, index };
    if (status === 'failed') {
      const error = jobsById.get(cut.shotJobId)?.error;
      return { action: 'stop', cutId: cut.id, index,
        message: `Шот ${index + 1} не получился${error ? `: ${String(error).slice(0, 200)}` : ''}. Исправь и нажми ↻ или «Собрать сцену» снова.` };
    }
    if (status === 'missing') return { action: 'stop', cutId: cut.id, index,
      message: `Ролик шота ${index + 1} удалён из истории. Нажми ↻ у шота или «Собрать сцену» заново.` };
    if (linkMode(cut) === 'keyframe' && (!cut.startFrame.asset_id || cut.productionFrameChanged) && board.autoKeyframes) {
      const pending = keyframeJobsOf(cut);
      if (pending.length) return { action: 'keyframe-wait', cutId: cut.id, index, keyframeIds: pending };
      const previous = cuts[index - 1];
      if (previous?.shotJobId && shotStatus(cuts, index - 1, jobsById) === 'done' && !lastFrameOf(board, previous.shotJobId))
        return { action: 'extract', cutId: cut.id, index, fromJobId: previous.shotJobId };
      // Composition first: shot 1's accepted frame fixes the room, later keyframes are drawn from it.
      if (board.keyframesFirst && index > 0 && awaiting.length && !establishingFrame(cuts, index)
        && !lastFrameOf(board, cuts[index - 1]?.shotJobId)) continue;
      const request = autoKeyframeRequest(cuts, index, board, keyframeReferences(cut, references, keyframeOptions.places), keyframeOptions);
      if (!request.ok) return { action: 'stop', cutId: cut.id, index, message: request.message };
      return { action: 'keyframe', cutId: cut.id, index, request, candidates: board.keyframeCandidates };
    }
    if (board.keyframesFirst && linkMode(cut) === 'keyframe' && cut.startFrame?.asset_id && !frameAccepted(cut, references)) {
      awaiting.push(index + 1); continue;
    }
    if (awaiting.length) continue;
    const plan = shotPlan(cuts, index, jobsById, board);
    if (!plan.ok) return { action: 'stop', cutId: cut.id, index, message: plan.message };
    if (plan.extractFrom) return { action: 'extract', cutId: cut.id, index, fromJobId: plan.extractFrom };
    return { action: 'enqueue', cutId: cut.id, index, plan };
  }
  if (awaiting.length) return { action: 'stop', awaiting, cutId: cuts[awaiting[0] - 1].id, index: awaiting[0] - 1,
    message: `Опорные кадры готовы: ${awaiting.map((n) => `шот ${n}`).join(', ')}. Проверь композицию, пространство, лица и контакты, `
      + 'прими каждый кадр («✓ Принять кадр») или замени его — затем «Собрать сцену» снимет видео от принятых кадров.'
      + (cuts.some((cut, at) => at > awaiting[0] - 1 && linkMode(cut) === 'keyframe' && !cut.startFrame?.asset_id)
        ? ' Кадры следующих шотов нарисуются от принятого общего плана — так комната останется одной.' : '') };
  if (board.qcGate && cuts.length) {
    const last = cuts.at(-1);
    if (last.shotJobId && !acceptedShot(last))
      return { action: 'qc-wait', cutId: last.id, index: cuts.length - 1,
        message: `Последний шот завершён. Проверь весь MP4 и прими дубль перед склейкой.` };
  }
  return { action: 'done', message: `Сцена собрана: ${cuts.length} шот(а/ов) готовы. Можно склеить.` };
}

// A press of «Собрать сцену» restarts failed shots and shots whose clip was deleted
// (an automatic resume after a reload does not: it stops on them with a message).
export function resetFailed(cuts, jobsById) {
  return cuts.map((cut, index) => (['failed', 'missing'].includes(shotStatus(cuts, index, jobsById))
    ? { ...cut, shotJobId: null } : cut));
}

export function pendingShots(cuts, jobsById) {
  return cuts.map((cut, index) => [index, shotStatus(cuts, index, jobsById)])
    .filter(([, status]) => status !== 'done').map(([index]) => index + 1);
}

// Options for window.h3Studio.enqueueCut.
export function enqueueOptions(plan) {
  return { startFrameAssetId: plan.startFrameAssetId || null, colorMatchAssetId: plan.colorMatchAssetId || null,
    environmentAssetId: plan.environmentAssetId || null,
    colorMatchStrength: plan.colorMatchStrength || DEFAULT_STRENGTH };
}

// The QC result and any manual override belong to one exact take, never to an older take.
export function nativeContinuationSource(plan, previous) {
  const source = plan.startFrame?.from_job;
  if (!source || !previous || previous.shotJobId !== source)
    throw new Error('Предыдущий дубль изменился — перестрой продолжение');
  if (!acceptedShot(previous))
    throw new Error('Для продолжения со звуком сначала проверь и прими предыдущий шот');
  return source;
}

// References for one shot sent with its own start frame. Other «Кадр сцены» pictures become
// «Окружение» (only this shot is anchored at frame 0); the start frame keeps its place if it is
// already a reference, otherwise it is appended as the next consecutive <Picture N>. A colour
// sample that is not the start frame joins as «Окружение» (ColorMatchV2 reads it from the job).
// The previous shot's last frame (environmentRef) joins as «Окружение» too, but only while there
// is room: it is a continuity aid, never a reason to fail the shot.
export function referencesWithStartFrame(references, startFrame, colorSampleRef = null, environmentRef = null) {
  let refs = references.map((ref) => (ref.role === 'Кадр сцены' && ref.id !== startFrame?.id ? { ...ref, role: 'Окружение' } : ref));
  const append = (item, role) => {
    const images = refs.filter((ref) => ref.kind === 'image');
    if (images.length >= MAX_IMAGES) throw new Error(`${role === 'Кадр сцены' ? 'Стартовый кадр' : 'Образец цвета'} не помещается: у H3 не больше ${MAX_IMAGES} фото в задании`);
    const number = Math.max(0, ...images.map((ref) => ref.number || 0)) + 1;
    refs = [...refs, { id: item.id, kind: 'image', name: item.name || 'Кадр сцены', role, number }];
    return number;
  };
  const guideFrames = [];
  if (startFrame?.id) {
    const existing = refs.find((ref) => ref.id === startFrame.id);
    if (existing && existing.kind !== 'image') throw new Error('Стартовый кадр должен быть изображением');
    if (existing) refs = refs.map((ref) => (ref.id === startFrame.id ? { ...ref, role: 'Кадр сцены', owner: '' } : ref));
    guideFrames.push([existing ? existing.number : append(startFrame, 'Кадр сцены'), 0]);
  }
  if (colorSampleRef?.id && colorSampleRef.id !== startFrame?.id) {
    const existing = refs.find((ref) => ref.id === colorSampleRef.id);
    if (existing && !['Окружение', 'Кадр сцены'].includes(existing.role))
      throw new Error(`Образец цвета уже прикреплён как «${existing.role}»; для подгонки цвета нужна роль «Окружение»`);
    if (!existing) append(colorSampleRef, 'Окружение');
  }
  if (environmentRef?.id && !refs.some((ref) => ref.id === environmentRef.id)
      && refs.filter((ref) => ref.kind === 'image').length < MAX_IMAGES) append(environmentRef, 'Окружение');
  return { references: refs, guideFrames };
}

// «Стиль сцены» is one line added to every shot's prompt text. The official six-section
// format (from «Сценарист») already carries style inside its sections and is left untouched.
export function withSceneStyle(prompt, style) {
  const line = String(style || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  if (!line || /^\s*subject_definitions:/.test(prompt)) return prompt;
  return `${prompt}\n\nScene style, identical in every shot of this scene: ${line}.`;
}

export function moveCut(cuts, id, delta) {
  const index = cuts.findIndex((cut) => cut.id === id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= cuts.length) return cuts;
  const next = [...cuts];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function finishedJobIds(cuts, jobsById) {
  const missing = cuts.map((cut, index) => [index, shotStatus(cuts, index, jobsById)])
    .filter(([index, status]) => status !== 'done' || !acceptedShot(cuts[index]));
  if (missing.length) throw new Error(`Не готовы или не приняты шоты: ${missing.map(([index]) => index + 1).join(', ')}`);
  return cuts.map((cut) => cut.shotJobId);
}

export function sceneSummary(cuts, jobsById) {
  const done = cuts.filter((cut, index) => ['done', 'stale'].includes(shotStatus(cuts, index, jobsById))).length;
  const seconds = cuts.reduce((sum, cut) => sum + (Number(cut.duration) || 0), 0);
  return { shots: cuts.length, done, seconds };
}

// Voices of the scene bible: audio references with the role «Голос», S1, S2… in order.
export function bibleVoices(references) {
  return references.filter((ref) => ref.kind === 'audio' && ref.role === 'Голос').map((ref, index) => ({ ...ref, speaker: `S${index + 1}` }));
}


// --- server scene run (local_studio/scene_runs.py) ------------------------------------------
// «Собрать сцену» hands the scene to the Mac: the page compiles each pending shot's job once, with stand-ins for
// the pictures that only exist later (a keyframe still to be drawn, the last frame of a shot still to be rendered),
// and the Mac runs chainStep itself (scripts/scene_run_step.mjs) — the tab may be closed.
// A stand-in names its source, so one source gives one stand-in and one reference, exactly as on the page:
//   studio-run:start:<cutId>  that shot's start frame      studio-run:last:<cutId>  that shot's last frame
//   studio-run:tail:<cutId>   the end of that shot as a video for a continuation with motion and sound
export const RUN_TOKEN = 'studio-run:';
const token = (kind, cutId) => `${RUN_TOKEN}${kind}:${cutId}`;

/** Pictures of shot `index` as the run resolves them later: options of h3Studio.enqueueCut with stand-ins. */
export function runSources(cuts, index, board) {
  const cut = cuts[index];
  const previous = cuts[index - 1];
  const mode = linkMode(cut);
  let start = null;
  let tail = null;
  if (mode === 'library') start = cut.startFrame.asset_id || null;
  else if (mode === 'keyframe') start = token('start', cut.id);
  else if (mode === 'previous' && previous) {
    if (board.motionContext) tail = token('tail', previous.id);
    else start = token('last', previous.id);
  }
  let color = null;
  if (index > 0 && board.colorSource !== 'off' && cut.colorMatch !== false) {
    if (board.colorSource === 'previous') color = token('last', previous.id);  // a continuation: its own start frame
    else {
      const first = cuts[0];
      color = first.startFrame?.source === 'library' && first.startFrame.asset_id ? first.startFrame.asset_id
        : linkMode(first) === 'keyframe' ? token('start', first.id) : token('last', first.id);
    }
  }
  const environment = board.linkPrevious && index > 0 && mode !== 'previous' ? token('last', previous.id) : null;
  return { mode, startFrameAssetId: start, colorMatchAssetId: color, environmentAssetId: environment,
    continuationAssetId: tail, colorMatchStrength: board.strength || DEFAULT_STRENGTH };
}

/** Stand-ins used by a compiled job, so the run knows what to fetch before sending it. */
export function runTokens(value) {
  return [...new Set(JSON.stringify(value).match(new RegExp(`${RUN_TOKEN}(?:start|last|tail):[\\w-]+`, 'g')) || [])];
}
