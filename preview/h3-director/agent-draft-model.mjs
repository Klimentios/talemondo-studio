// Agent drafts for the «Создать видео» form and the story canvas: pure helpers for agent-dock.mjs /
// agent-draft.mjs. The agent proposes a clip (composer_draft) or a whole scene (scene_draft) through
// scripts/h3_studio_mcp.py; the user applies it with one click.
import { nearestAspect } from './aspects.mjs?v=20261106-aspects-1';

const toolName = (name) => String(name || '').replace(/^mcp__h3-studio__/, '');
export const isDraftTool = (name) => toolName(name) === 'composer_draft';
export const isSceneTool = (name) => toolName(name) === 'scene_draft';

/** The draft carried by a tool event the Studio accepted (a rejected one is only a tool error), or null. */
export function draftFromEvent(event) {
  if (!event || event.kind !== 'tool' || !isDraftTool(event.name) || event.status !== 'done') return null;
  const draft = event.draft;
  return draft && typeof draft === 'object' && typeof draft.prompt === 'string' && draft.prompt.trim() ? draft : null;
}

/** The scene carried by an accepted scene_draft event, or null. */
export function sceneFromEvent(event) {
  if (!event || event.kind !== 'tool' || !isSceneTool(event.name) || event.status !== 'done') return null;
  const scene = event.draft;
  return scene && Array.isArray(scene.shots) && scene.shots.length ? scene : null;
}

export const sceneSeconds = (scene) => scene.shots.reduce((sum, shot) => sum + (Number(shot.duration) || 0), 0);

/** Board cuts for the story canvas (same shape as cutsFromScenePlan in storyboard-model.mjs). The English
 *  H3 prompt goes to the render; first_frame is what autoKeyframeRequest draws for a «новый план» shot. */
export function sceneCuts(scene, previousCuts = []) {
  const nextId = Math.max(0, ...previousCuts.map((cut) => Number(String(cut.id).split('-')[1]) || 0)) + 1;
  return scene.shots.map((shot, index) => ({
    id: `cut-${nextId + index}`, duration: shot.duration, prompt: shot.prompt.trim(),
    ...(typeof shot.fantasy === 'boolean' ? { fantasy: shot.fantasy } : {}),
    ...(shot.production_spec ? { productionSpec: structuredClone(shot.production_spec) } : {}),
    startFrame: index && shot.start_frame === 'previous_end' ? { source: 'previous', asset_id: null } : { source: 'keyframe', asset_id: null },
    sceneShot: { title: shot.title, purpose: shot.beat || '', action: shot.action || '', first_frame: shot.first_frame || '', framing: shot.framing,
      angle: shot.angle || '', shot_purpose: shot.purpose || '', emotion: shot.emotion || '',
      lens_mm: shot.lens_mm, camera_motion: shot.camera_motion, transition_in: shot.transition_in || 'cut',
      // The move's direction/speed and the staging line (scene_draft) reach the take's prompt at «Собрать сцену»
      // (shot_contract.normalize reads sceneShot); without them the shot was «one slow move» with nobody placed.
      ...(shot.camera_direction ? { camera_direction: shot.camera_direction } : {}),
      ...(shot.camera_speed ? { camera_speed: shot.camera_speed } : {}),
      ...(shot.staging_line ? { staging_line: shot.staging_line } : {}),
      location: shot.location || '', ...(Array.isArray(shot.characters) ? { characters: shot.characters } : {}), intent: shot.intent || '',
      screen_direction: shot.screen_direction || '', eyeline: shot.eyeline || '', axis_side: shot.axis_side || '',
      state_before: shot.state_before || '', state_after: shot.state_after || '' },
    // Director presets of this shot (camera body, lens, light, colour, style, acting…): the scene's set, then the
    // shot's own. Without either the shot keeps the form's current set, as before.
    ...(scene.director || shot.director ? { director: { ...(scene.director || {}), ...(shot.director || {}) } } : {}),
  }));
}

/** Preset ids in a scene that the Studio does not know (presets = DIRECTOR_PRESETS plus custom ones). */
export function unknownDirectorPresets(scene, presets) {
  const known = new Set(presets.map((preset) => `${preset.category}:${preset.id}`));
  const sets = [['сцена', scene.director], ...scene.shots.map((shot, index) => [`шот ${index + 1}`, shot.director])];
  return sets.flatMap(([where, set]) => Object.entries(set || {})
    .filter(([category, id]) => !known.has(`${category}:${id}`)).map(([category, id]) => `${where}: ${category} ${id}`));
}

const FRAMING = { wide: 'общий', full: 'полный рост', medium: 'средний', close: 'крупный', extreme_close: 'деталь',
  insert: 'врезка', two_shot: 'два героя', over_shoulder: 'через плечо', point_of_view: 'POV', extreme_wide: 'дальний',
  medium_full: 'по колено', medium_close: 'по грудь' };

const megapixels = (value) => `${Number(value).toFixed(2).replace('.', ',')} Мп`;
const ANGLE = { low: 'снизу', high: 'сверху', overhead: 'над головой', dutch: 'голландский', worm: 'с земли' };
const MODE = { realistic: 'реализм', animation3d: '3D', animation2d: '2D', replace: 'замена героя' };

/** Scene-wide chips for the scene card: length, format, render scenario, quality and the director set. */
export function sceneChips(scene) {
  const refs = scene.references || [];
  const director = Object.keys(scene.director || {}).length;
  return [`${scene.shots.length} шотов`, `${sceneSeconds(scene)} с`, scene.aspect_ratio || '', MODE[scene.mode] || '',
    typeof scene.quality === 'number' ? megapixels(scene.quality) : '', scene.steps ? `${scene.steps} шагов` : '',
    director ? `режиссура: ${director}` : '', refs.length ? `${refs.length} реф.` : ''].filter(Boolean);
}

/** One line per shot for the scene card. */
export function sceneLines(scene) {
  return scene.shots.map((shot, index) => ({
    head: `${String(index + 1).padStart(2, '0')} · ${shot.title}`,
    meta: [`${shot.duration} с`, FRAMING[shot.framing] || shot.framing, ANGLE[shot.angle] || '', shot.lens_mm ? `${shot.lens_mm} мм` : '', shot.camera_motion,
      index ? `← ${shot.transition_in || 'cut'}` : '', index && shot.start_frame === 'previous_end' ? 'с последнего кадра' : 'новый кадр']
      .filter(Boolean).join(' · '),
    beat: [shot.beat || '', shot.production_spec ? `Постановка: ${shot.production_spec.shot?.beats?.length || 0} действий, версия ${shot.production_spec.shot?.revision || '?'}` : ''].filter(Boolean).join(' · '),
  }));
}

/** An accepted recipe's compiled_profile in «Создать видео» terms. The hero swap runs as «Замена героя» with the
 *  form's automatic style adapter (Character Swap for photo + source video); the duration stays the new source's
 *  own, since a recipe was proven on its original clip. */
export function recipeFormSettings(profile = {}) {
  const out = {};
  if (String(profile.style_adapter || '').startsWith('character_swap')) out.mode = 'replace';
  if (Number.isInteger(profile.steps)) out.steps = profile.steps;
  if (Number.isInteger(profile.seed)) out.seed = profile.seed;
  if (profile.cache_mode) out.cache_mode = profile.cache_mode;
  if (profile.ref_image_size) out.ref_image_size = profile.ref_image_size;
  if (profile.audio) out.use_video_audio = profile.audio === 'original';
  const megapixels = Number(profile.megapixels) || (profile.width && profile.height ? profile.width * profile.height / 1e6 : 0);
  // User decision 2026-11-04: agents render at 0.8 MP (9:16 672x1216) by default; the user changes it by hand.
  if (megapixels) out.quality = 0.8;
  // The recipe's own format (4:3 source → 4:3), the nearest one the GPU lists.
  if (profile.width && profile.height) out.aspect_ratio = nearestAspect(profile.width, profile.height);
  return out;
}

const MODE_LABEL = { realistic: 'реализм', animation3d: '3D', animation2d: '2D', replace: 'замена героя' };

/** Short labels for the draft card. */
export function draftChips(draft) {
  const refs = draft.references || [];
  return [
    draft.recipe_id ? `рецепт ${draft.recipe_id}` : '',
    MODE_LABEL[draft.mode] || '',
    Number.isInteger(draft.duration) ? `${draft.duration} с` : '',
    draft.aspect_ratio || '',
    typeof draft.quality === 'number' ? megapixels(draft.quality) : '',
    Number.isInteger(draft.steps) ? `${draft.steps} шагов` : '',
    refs.some((ref) => ref.performers?.length) ? `замена: ${refs.flatMap((ref) => ref.performers || []).map((pair) => pair.owner).join(', ')}` : '',
    refs.length ? `${refs.length} реф. · ${draft.replace_references === false ? 'добавить к текущим' : 'заменить текущие'}` : '',
  ].filter(Boolean);
}

/** What the agent sees of the form: kept small, no files, no side effects. */
export function screenContext({ prompt = '', references = [], duration, aspect, quality, cuts = [], scene = '' }) {
  return {
    page: 'Создать видео (MiniMax H3) и холст сцены',
    scene,
    board: cuts.slice(0, 12).map((cut, index) => ({ shot: index + 1, title: cut.sceneShot?.title || '', duration: cut.duration,
      rendered: Boolean(cut.shotJobId), prompt: String(cut.prompt || '').slice(0, 300) })),
    prompt: String(prompt).slice(0, 6000),
    settings: { duration: Number(duration) || null, aspect_ratio: aspect || '', quality: Number(quality) || null },
    references: references.map((ref) => ({ asset_id: ref.id, kind: ref.kind, tag: tagOf(ref), role: ref.role || '',
      owner: ref.owner || '', name: ref.name || '' })),
  };
}

function tagOf(ref) {
  const label = { image: 'Picture', video: 'Video', audio: 'Audio' }[ref.kind];
  return label && ref.number ? `<${label} ${ref.number}>` : '';
}
