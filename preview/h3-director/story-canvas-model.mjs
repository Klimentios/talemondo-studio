// Pure model of the Story canvas (story-canvas.mjs): layout, node keys, takes and the film plan.
// No DOM here, so node --test covers it.
import { jobStatus, jobProgress, keyframeJobsOf, shotStatus, shotCardDescription, acceptedShot } from './storyboard-model.mjs?v=20261102-take-admission-1';
import { normalizeLegacyIr, placeOff, pruneAbsentHeroMetadata, shotReferences } from './shot-references.mjs?v=20261101-breakdown-1';

export const MATERIAL_W = 170;
export const FILM_W = 240;
const GAP_X = 130;
const MATERIAL_GAP_Y = 70;
const MATERIAL_H = 220;

// Card size follows the clip format: vertical clips get tall cards, horizontal ones wide cards.
export function shotWidth(aspect) { return aspect === '9:16' ? 230 : 360; }
export const SHOT_W = shotWidth('16:9');

// Default positions: materials stacked on the left, shots as one left-to-right strip, the film last.
export function defaultLayout(cuts, references, { aspect = '16:9' } = {}) {
  const positions = {};
  const width = shotWidth(aspect);
  const pictures = canvasMaterials(references);
  const column = pictures.length ? pictures.map((ref) => `m:${ref.id}`) : ['m:+'];
  column.forEach((key, index) => { positions[key] = { x: 0, y: index * (MATERIAL_H + MATERIAL_GAP_Y) }; });
  const x0 = MATERIAL_W + 190;
  cuts.forEach((cut, index) => { positions[`s:${cut.id}`] = { x: x0 + index * (width + GAP_X), y: 0 }; });
  positions.film = { x: x0 + cuts.length * (width + GAP_X), y: 0 };
  return positions;
}

// Saved positions win; anything new (a shot added since) gets its default place.
export function mergeLayout(saved, cuts, references, options) {
  const base = defaultLayout(cuts, references, options);
  const result = {};
  for (const [key, value] of Object.entries(base)) {
    const own = saved?.[key];
    result[key] = own && Number.isFinite(own.x) && Number.isFinite(own.y) ? { x: own.x, y: own.y } : value;
  }
  return result;
}

export function canvasMaterials(references = []) {
  return references.filter((ref) => ref && ['image', 'video', 'audio'].includes(ref.kind));
}

const HERO_ROLES = new Set(['Лицо героя', 'Персонаж целиком', 'Костюм', 'Голос']);
const PLACE_ROLES = new Set(['Окружение', 'Кадр сцены', 'Композиция']);
export const materialGroup = (ref) => (HERO_ROLES.has(ref?.role) ? 'hero' : PLACE_ROLES.has(ref?.role) ? 'place' : 'look');

// What one shot really sends to H3: the same pass as the render (app.mjs) — heroes absent from the shot
// text/cast leave, materials switched off for this shot (cut.offRefs) leave. The canvas draws only these links.
// `places`: the franchise passport places (scene-places.mjs); a location picture of another place stays out (`place`).
export function shotMaterials(cut, references = [], places = []) {
  const off = cut?.offRefs || [];
  const cast = cut?.sceneShot?.characters;
  const required = (cut?.productionSpec?.characters || []).flatMap((actor) => actor.reference_asset_ids || []);
  try {
    const prompt = pruneAbsentHeroMetadata(normalizeLegacyIr(cut?.prompt || ''), references, cast);
    const place = placeOff(references, prompt, cut?.sceneShot?.location, places);
    const kept = shotReferences(references, prompt, required, cast, [...off, ...place]).references;
    return { ids: new Set(kept.map((ref) => ref.id)), off: new Set(off), place: new Set(place), error: '' };
  } catch (error) {
    return { ids: new Set(references.filter((ref) => !off.includes(ref.id)).map((ref) => ref.id)), off: new Set(off), place: new Set(), error: error.message };
  }
}

// Rebuild the DOM only when this changes; job progress, takes and text are patched in place.
export function structureKey(sceneId, cuts, references, aspect = '') {
  return JSON.stringify([
    sceneId || '', aspect,
    cuts.map((cut) => [cut.id, cut.startFrame?.source || '', cut.startFrame?.asset_id || '']),
    canvasMaterials(references).map((ref) => [ref.id, ref.kind, ref.role || '', ref.owner || '']),
  ]);
}

export const selectedJobId = (cut) => cut?.selectedTakeId || cut?.shotJobId || null;

// Takes worth showing: finished ones and failures (marked ✕). Cancelled or deleted jobs are not takes.
export function takeList(cut, jobsById) {
  const ids = [...new Set([...(cut?.takes || []).map((take) => take.jobId || take.id), cut?.shotJobId].filter(Boolean))];
  return ids.map((jobId) => {
    const job = jobsById.get(jobId);
    if (!job || job.state === 'cancelled' || job.hidden) return null;
    return { jobId, status: jobStatus(job), progress: jobProgress(job) };
  }).filter(Boolean);
}

export function nodeState(cuts, index, jobsById) {
  const cut = cuts[index];
  const job = jobsById.get(cut?.shotJobId);
  // A cancelled or vanished current job means the shot simply has no take yet.
  const status = !cut?.shotJobId || !job || job.state === 'cancelled' ? 'idle' : shotStatus(cuts, index, jobsById);
  return { status, progress: jobProgress(job), jobId: status === 'idle' ? null : cut.shotJobId, error: job?.error || '' };
}

export const STATUS_TEXT = {
  idle: 'Черновик', queued: 'В очереди', running: 'Генерируется', done: 'Готов',
  failed: 'Ошибка', stale: 'Устарел', missing: 'Ролик удалён',
};

export function shotLabel(cut) {
  return shotCardDescription(cut);
}

export const isTechnicalPrompt = (text) => /^\s*(?:subject_definitions|summary|retention_analysis|detailed_description):/i.test(String(text || ''));

// Film of this scene: the selected take of every ready shot, in order.
export function filmPlan(cuts, jobsById) {
  const ready = [];
  const missing = [];
  cuts.forEach((cut, index) => {
    const { status } = nodeState(cuts, index, jobsById);
    if (status === 'done' && acceptedShot(cut)) ready.push({ index, jobId: cut.shotJobId, seconds: Number(cut.duration) || 0 });
    else missing.push(index);
  });
  return { ids: ready.map((item) => item.jobId), ready, missing, seconds: ready.reduce((sum, item) => sum + item.seconds, 0) };
}

export function plural(n, one, few, many) {
  const mod10 = n % 10; const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} ${one}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} ${few}`;
  return `${n} ${many}`;
}

export function bounds(positions, sizes) {
  const boxes = Object.entries(positions).map(([key, p]) => ({ ...p, w: sizes[key]?.w || SHOT_W, h: sizes[key]?.h || 300 }));
  if (!boxes.length) return { x: 0, y: 0, w: 1, h: 1 };
  const x = Math.min(...boxes.map((b) => b.x)); const y = Math.min(...boxes.map((b) => b.y));
  const x2 = Math.max(...boxes.map((b) => b.x + b.w)); const y2 = Math.max(...boxes.map((b) => b.y + b.h));
  return { x, y, w: x2 - x, h: y2 - y };
}

// One line about what «Собрать сцену» did with this shot automatically: keyframe, AI choice,
// AI check, automatic retake. Empty when nothing happened.
const QC_TEXT = { accept: 'AI-проверка: принято', adapt_next: 'AI-проверка: следующий шот подстроится',
  regenerate: 'AI-проверка: брак', manual_review: 'AI-проверка: нужен твой взгляд' };
export function autoNote(cut) {
  const waiting = keyframeJobsOf(cut).length;
  if (waiting) return `✨ ключевой кадр готовится${waiting > 1 ? ` · ${waiting} варианта` : ''}`;
  const parts = [];
  const pick = cut?.keyframePick;
  if (pick?.candidates?.length > 1) parts.push(pick.fallback ? `✨ ${pick.candidates.length} варианта, AI-выбор недоступен — взят первый`
    : `✨ AI выбрал лучший из ${pick.candidates.length}${pick.abWinner ? ` (A/B: ${pick.abWinner === 'lora' ? 'LoRA ракурса' : 'база'})` : ''}${pick.reason ? `: ${pick.reason}` : ''}`);
  if (cut?.shotQc?.job_id && cut.shotQc.job_id === cut.shotJobId && QC_TEXT[cut.shotQc.decision]) parts.push(`◇ ${QC_TEXT[cut.shotQc.decision]}`);
  else if (cut?.shotQcPending) parts.push('◇ ждёт AI-проверки');
  else if (cut?.shotQcError) parts.push('◇ AI-проверка недоступна');
  if (Number(cut?.autoRetakes) > 0) parts.push('↻ AI переснял');
  return parts.join(' · ');
}
