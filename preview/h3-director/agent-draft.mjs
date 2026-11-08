// Applies an agent's composer_draft to the «Создать видео» form, or its scene_draft to the story canvas,
// through window.h3Studio and the form's own controls (their listeners save the draft and re-render),
// so nothing bypasses the page. Neither starts a keyframe or a render.
import { recipeFormSettings, sceneCuts, sceneSeconds, unknownDirectorPresets } from './agent-draft-model.mjs?v=20261106-aspects-1';
import { selectAspect } from './aspects.mjs?v=20261106-aspects-1';
import { DIRECTOR_PRESETS } from './director-presets.mjs?v=20261031-lighting-15-1';
const $ = (selector) => document.querySelector(selector);

function set(selector, value, ...events) {
  const node = $(selector);
  if (!node) return false;
  node.value = String(value);
  for (const name of events) node.dispatchEvent(new Event(name, { bubbles: true }));
  return true;
}

// The format select is filled from the GPU list asynchronously: add the agent's format if it is not there yet.
function setAspect(value) {
  if (!selectAspect($('#aspect'), value)) return false;
  $('#aspect').dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function writable(studio) {
  if (!studio?.canWriteDraft?.()) throw new Error('Studio ещё загружается или проект изменён в другой вкладке — обнови страницу');
}

async function resolveReferences(refs) {
  const response = await fetch('/api/assets');
  if (!response.ok) throw new Error(`Библиотека не ответила: HTTP ${response.status}`);
  const library = new Map((await response.json()).map((asset) => [asset.asset_id, asset]));
  const missing = refs.filter((ref) => !library.has(ref.asset_id));
  if (missing.length) throw new Error(`Нет в библиотеке: ${missing.map((ref) => ref.asset_id.slice(0, 8)).join(', ')}`);
  return library;
}

function setReferences(studio, refs, replace, library) {
  if (replace) for (const ref of [...studio.references()]) studio.removeReference(ref.id);
  for (const ref of refs) {
    studio.addReferenceAsset(library.get(ref.asset_id), ref.role, ref.owner || '');
    // The agent names the owner as explicitly as the form's owner field does (app-composer.mjs): with several
    // heroes the request keeps it instead of guessing from the prompt text.
    const live = ref.owner && studio.references().find((item) => item.id === ref.asset_id);
    if (live) live.manualOwner = true;
  }
}

/** The render settings an accepted recipe proved (GET /api/recipes/accepted/{id} → compiled_profile), in form terms. */
async function recipeSettings(recipeId) {
  const response = await fetch(`/api/recipes/accepted/${encodeURIComponent(recipeId)}`);
  const recipe = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`Принятый рецепт ${recipeId} не найден`);
  return { ...recipeFormSettings(recipe.compiled_profile || {}), recipeTitle: recipe.title || recipeId };
}

function setChecked(selector, value) {
  const node = $(selector);
  if (!node) return false;
  node.checked = Boolean(value);
  node.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

export async function applyDraft(draft, studio = window.h3Studio) {
  writable(studio);
  const done = [];
  // A recipe's proven settings win over the agent's own fields: that is what makes the result repeatable.
  const settings = { ...draft, ...(draft.recipe_id ? await recipeSettings(draft.recipe_id) : {}) };
  const refs = draft.references || [];
  const library = refs.length ? await resolveReferences(refs) : null;
  // The scenario first: switching it resets the style adapter and the roles the form offers.
  if (settings.mode && set('#modeSelect', settings.mode, 'change')) done.push(`сценарий ${settings.mode}`);
  // The source video's own sound before the prompt: with it off, <Audio N> of a soundtrack is an unknown tag
  // and the form rewrites it to ⟦неизвестный Audio N⟧.
  if (typeof settings.use_video_audio === 'boolean' && setChecked('#useVideoAudio', settings.use_video_audio))
    done.push(settings.use_video_audio ? 'родной звук видео' : 'без звука видео');
  if (refs.length) {
    setReferences(studio, refs, draft.replace_references !== false, library);
    done.push(`референсы: ${refs.length}`);
  }
  // «Исходное видео»: who in the source becomes which hero; everyone else stays as filmed.
  for (const ref of refs.filter((item) => item.performers?.length)) {
    const live = studio.references().find((item) => item.id === ref.asset_id);
    if (!live) continue;
    live.performers = { pairs: ref.performers.map(({ owner, performer }) => ({ owner, performer })), keep_others: true };
    studio.updateReference(live.id, {});  // re-render the material with its pairs
    done.push(`замена: ${ref.performers.map((pair) => `${pair.performer} → ${pair.owner}`).join(', ')}`);
  }
  // After the references: removing one rewrites the prompt field from the shot.
  set('#mainPrompt', draft.prompt, 'input');
  done.push('промпт');
  // Only 'input': 'change' on #duration is the «длинная сцена» hand-off for more than 15 s.
  if (Number.isInteger(draft.duration) && set('#duration', draft.duration, 'input')) done.push(`${draft.duration} с`);
  if (settings.aspect_ratio && setAspect(settings.aspect_ratio)) done.push(settings.aspect_ratio);
  // The slider's own listener picks the matching preset in the select.
  if (typeof settings.quality === 'number' && set('#quality', settings.quality.toFixed(2), 'input')) done.push(`${settings.quality.toFixed(2)} Мп`);
  if (Number.isInteger(settings.steps) && set('#steps', settings.steps, 'input')) done.push(`${settings.steps} шагов`);
  if (Number.isInteger(settings.seed) && set('#seed', settings.seed, 'input')) done.push(`seed ${settings.seed}`);
  if (settings.cache_mode && set('#cacheMode', settings.cache_mode, 'change')) done.push(`кеш ${settings.cache_mode}`);
  if (settings.sampling_profile && set('#samplingProfile', settings.sampling_profile, 'change')) done.push(`сэмплинг ${settings.sampling_profile}`);
  if (settings.ref_image_size && set('#refImageSize', settings.ref_image_size, 'change')) done.push(`референсы ${settings.ref_image_size}`);
  if (settings.recipeTitle) done.push(`по рецепту «${settings.recipeTitle}»`);
  await studio.saveDraft?.();
  return done;
}

/** The whole scene onto the canvas: its shared references, style and shots. A scene that already has
 *  work keeps it — the agent's scene becomes a new scene; an empty one is filled in place (its old board
 *  stays in sceneDirector.previousCuts, as when the AI Director applies a plan). */
export async function applySceneDraft(scene, studio = window.h3Studio, { inPlace = false } = {}) {
  writable(studio);
  // Each scene has its own franchise: scenes of other franchises in the project are fine; only the scene this
  // draft lands in must not belong to another one (a used one then makes way for a new scene below).
  const { scenes, activeSceneId } = studio.sceneState();
  if (inPlace && studio.cuts().some((cut) => cut.shotJobId || cut.keyframeJobId || cut.keyframeJobIds?.length))
    throw new Error('У сцены уже есть задания шотов или опорных кадров. Сверь их статусы перед заменой плана');
  // A new agent draft must not silently create another copy of an existing story.
  // Scene-bound agents may revise their own scene in place, but not recreate another scene's story.
  if (scene.title?.trim()) {
    const title = scene.title.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
    const existing = scenes.find((item) => item.id !== (inPlace ? activeSceneId : '')
      && item.title?.trim().replace(/\s+/g, ' ').toLocaleLowerCase() === title);
    if (existing) throw new Error(`Сцена «${scene.title}» уже есть на холсте (${existing.id}). Открой её для правки вместо создания копии`);
  }
  const owner = studio.project().sceneDirector?.agentScene?.franchise_id
    || scenes.find((item) => item.id === activeSceneId)?.franchiseSource?.franchise_id || '';
  const foreign = Boolean(owner && scene.franchise_id && owner !== scene.franchise_id);
  if (foreign && inPlace) throw new Error('Сцена этого чата принадлежит другой франшизе — открой для неё новую сцену');
  const unknown = unknownDirectorPresets(scene, [...DIRECTOR_PRESETS, ...(studio.project().customDirectorPresets || [])]);
  if (unknown.length) throw new Error(`Нет таких пресетов режиссуры: ${unknown.slice(0, 4).join('; ')}`);
  // Resolve every asset before creating a scene or replacing its references.
  const library = scene.references?.length ? await resolveReferences(scene.references) : null;
  for (const shot of scene.shots) {
    if (!shot.production_spec) continue;
    if (shot.production_spec.franchise_id !== scene.franchise_id || Number(shot.production_spec.shot?.duration) !== Number(shot.duration))
      throw new Error('ShotSpec не совпадает с франшизой или длительностью шота');
    const response = await fetch('/api/production/spec/validate', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(shot.production_spec) });
    const result = await response.json();
    if (!response.ok || !result.valid) throw new Error('Некорректная постановка ShotSpec — холст не изменён');
    const selected = new Set((scene.references || []).map(ref => ref.asset_id));
    if ((shot.production_spec.characters || []).some(actor => (actor.reference_asset_ids || []).some(id => !selected.has(id))))
      throw new Error('В материалы сцены не включены все референсы ShotSpec');
  }
  writable(studio);
  // inPlace: the agent of this scene rebuilds its own scene; otherwise a scene with work is kept and a new one opens.
  const used = !inPlace && (foreign || studio.cuts().some((cut) => String(cut.prompt || '').trim() || cut.shotJobId || cut.startFrame?.asset_id));
  if (used) studio.createScene();
  if (scene.title) studio.renameScene(studio.sceneState().activeSceneId, scene.title);
  if (library) setReferences(studio, scene.references, true, library);
  // Render settings of the whole scene through the form's own controls (their listeners save and re-render).
  if (scene.aspect_ratio) setAspect(scene.aspect_ratio);
  if (scene.mode) set('#modeSelect', scene.mode, 'change');
  if (typeof scene.quality === 'number') set('#quality', scene.quality.toFixed(2), 'input');
  if (Number.isInteger(scene.steps)) set('#steps', scene.steps, 'input');
  const project = studio.project();
  const state = project.sceneDirector || {};
  project.sceneDirector = { ...state, previousCuts: structuredClone(studio.cuts()) };
  studio.editCuts((cuts) => sceneCuts(scene, cuts));
  if (scene.style) project.sceneStyle = String(scene.style).slice(0, 600);
  project.sceneDirector = { ...project.sceneDirector, idea: scene.idea || state.idea || '', targetSeconds: sceneSeconds(scene),
    shotCount: scene.shots.length, plan: null, agentScene: { title: scene.title || '', franchise_id: scene.franchise_id || '',
      appliedAt: new Date().toISOString() },
    appliedAt: new Date().toISOString() };
  if (scene.fantasy_world?.description) project.storyboard = { ...(project.storyboard || {}),
    fantasyWorld: { description: scene.fantasy_world.description, baseId: scene.fantasy_world.base_asset_id || null } };
  const first = studio.cuts()[0];
  if (first) studio.selectCut(first.id);
  await studio.saveDraft?.();
  window.h3Storyboard?.refresh();
  window.dispatchEvent(new CustomEvent('h3:scenes-changed'));
  const director = new Set([...Object.keys(scene.director || {}), ...scene.shots.flatMap((shot) => Object.keys(shot.director || {}))]);
  return [used ? 'новая сцена' : 'текущая сцена', `шотов: ${scene.shots.length}`, `${sceneSeconds(scene)} с`, `референсы: ${scene.references?.length || 0}`,
    ...(scene.mode ? [`сценарий ${scene.mode}`] : []), ...(typeof scene.quality === 'number' ? [`${scene.quality.toFixed(2)} Мп`] : []),
    ...(director.size ? [`режиссура: ${[...director].join(', ')}`] : [])];
}
