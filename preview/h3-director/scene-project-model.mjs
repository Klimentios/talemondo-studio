// Scene-level state layered over the legacy active-scene project fields. The
// generation pipeline continues to consume project.cuts, while drafts retain
// every scene independently.
// Draft state is JSON data. Use JSON cloning so the board works in older
// embedded WebKit builds that do not expose structuredClone.
const copy = (value) => JSON.parse(JSON.stringify(value));

// Every scene owns its reference state (materials, voices, colour match): scenes are separate spaces, often of
// different franchises, and must never see each other's heroes. Legacy scenes that shared one state get their
// own copy of it once (normalizeSceneProject); a new scene starts empty.
const REFERENCE_FIELDS = ['references','referenceCounters','colorMatch','guideFrames','useVideoAudio',
  'actingMode','actingOwner','actingVoice','actingTimbre','actingVoices','actingTexture','actingAccent','actingEmotion','actingPace'];
const RENDER_FIELDS = ['mode','aspect','quality','megapixels','steps','refImageSize','styleAdapter','cacheMode','samplingProfile'];
const captureRenderState = (project) => Object.fromEntries(RENDER_FIELDS.filter(key => project[key] !== undefined).map(key => [key, copy(project[key])]));
const legacyRenderState = (project, scene) => {
  const state = captureRenderState(project);
  const style = String(scene.sceneStyle || '');
  if (/photoreal|live.action|real cats|real humans/i.test(style)) state.mode = 'realistic';
  else if (/stylized|3d animation|animated feature/i.test(style)) state.mode = 'animation3d';
  return state;
};
const restoreRenderState = (project, scene) => {
  for (const key of RENDER_FIELDS) if (Object.hasOwn(scene.renderState || {}, key)) project[key] = copy(scene.renderState[key]);
};
export function captureReferenceState(project) {
  return Object.fromEntries(REFERENCE_FIELDS.filter(key => project[key] !== undefined).map(key => [key, copy(project[key])]));
}
const emptyReferenceState = () => ({ references: [], referenceCounters: { image: 0, video: 0, audio: 0 } });

export function restoreReferenceState(project, scene) {
  const state = scene.referenceState || project.sharedReferenceState;
  if (!state) return;
  for (const key of REFERENCE_FIELDS) {
    if (Object.hasOwn(state, key)) project[key] = copy(state[key]); else delete project[key];
  }
}

export function russianShotCount(count) {
  const value = Math.abs(Math.trunc(Number(count) || 0));
  const lastTwo = value % 100;
  const last = value % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? 'шотов'
    : last === 1 ? 'шот'
      : last >= 2 && last <= 4 ? 'шота' : 'шотов';
  return `${value} ${noun}`;
}

export function normalizeSceneProject(project) {
  const current = project.activeSceneId;
  let scenes = Array.isArray(project.scenes) ? project.scenes.filter((scene) => scene && scene.id) : [];
  if (!scenes.length) {
    const id = current || 'scene-1';
    project.scenes = [{ id, title: project.sceneTitle || 'Сцена 01', cuts: copy(project.cuts || []),
      sceneStyle: project.sceneStyle || '', seed: project.seed || '', storyboard: copy(project.storyboard || {}), sceneDirector: copy(project.sceneDirector || {}), renderState: captureRenderState(project) }];
    project.activeSceneId = id;
    scenes = project.scenes;
  } else if (!scenes.some((scene) => scene.id === current)) project.activeSceneId = scenes[0].id;
  project.scenes = scenes.map((scene, index) => ({ ...scene, title: scene.title || `Сцена ${String(index + 1).padStart(2, '0')}`,
    cuts: Array.isArray(scene.cuts) && scene.cuts.length ? scene.cuts : [{ id: `${scene.id}-shot-1`, duration: 5, prompt: '' }],
    sceneStyle: scene.sceneStyle || '', seed: scene.seed || '', storyboard: scene.storyboard || {}, sceneDirector: scene.sceneDirector || {} }));
  const active = project.scenes.find((scene) => scene.id === project.activeSceneId);
  // One-time split of the old shared materials: each legacy scene keeps what it showed, from now on as its own.
  const shared = project.sharedReferenceState || (active.referenceState ? null : captureReferenceState(project));
  for (const scene of project.scenes) if (!scene.referenceState) scene.referenceState = shared ? copy(shared) : emptyReferenceState();
  for (const scene of project.scenes) if (!scene.renderState) scene.renderState = legacyRenderState(project, scene);
  delete project.sharedReferenceState;
  project.cuts = copy(active.cuts); project.sceneStyle = active.sceneStyle; project.seed = active.seed; project.storyboard = copy(active.storyboard); project.sceneDirector = copy(active.sceneDirector);
  restoreRenderState(project, active);
  restoreReferenceState(project, active);
  return project;
}

export function saveActiveScene(project) {
  const scenes = project.scenes || [];
  const at = scenes.findIndex((scene) => scene.id === project.activeSceneId);
  if (at < 0) return project;
  scenes[at].referenceState = captureReferenceState(project);
  scenes[at] = { ...scenes[at], cuts: copy(project.cuts), sceneStyle: project.sceneStyle || '', seed: project.seed || '', storyboard: copy(project.storyboard || {}), sceneDirector: copy(project.sceneDirector || {}), renderState: captureRenderState(project) };
  return project;
}

export function switchScene(project, id) {
  saveActiveScene(project);
  const target = project.scenes.find((scene) => scene.id === id);
  if (!target) return project;
  project.activeSceneId = id; project.cuts = copy(target.cuts); project.sceneStyle = target.sceneStyle;
  project.seed = target.seed; project.storyboard = copy(target.storyboard); project.sceneDirector = copy(target.sceneDirector || {});
  restoreRenderState(project, target);
  restoreReferenceState(project, target);
  return project;
}

export function addScene(project) {
  saveActiveScene(project);
  const number = project.scenes.length + 1;
  const id = `scene-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${number}`}`;
  const scene = { id, title: `Сцена ${String(number).padStart(2, '0')}`, cuts: [{ id: `${id}-shot-1`, duration: 5, prompt: '' }], sceneStyle: '', seed: '', storyboard: {}, sceneDirector: {},
    renderState: captureRenderState(project),
    referenceState: emptyReferenceState() };  // a new scene starts with no materials of its own scene neighbours
  project.scenes.push(scene); project.activeSceneId = id; project.cuts = copy(scene.cuts); project.sceneStyle = ''; project.seed = ''; project.storyboard = {}; project.sceneDirector = {};
  restoreReferenceState(project, scene);
  return project;
}

export function renameScene(project, id, title) {
  const scene = project.scenes.find((item) => item.id === id);
  if (scene) scene.title = String(title).trim().slice(0, 100) || scene.title;
  return project;
}

/** «Сбросить сцену»: the active scene back to one empty shot — no shots, materials, style, storyboard or director
 *  plan. Its reference state becomes its own (empty), so scenes that shared materials with it keep theirs. Finished
 *  renders stay in the queue and the library. */
export function resetScene(project) {
  saveActiveScene(project);
  const scene = project.scenes.find((item) => item.id === project.activeSceneId);
  if (!scene) return false;
  for (const key of REFERENCE_FIELDS) delete project[key];
  project.references = [];
  const fresh = { id: `${scene.id}-shot-${Date.now()}`, duration: 5, prompt: '' };
  Object.assign(scene, { cuts: [fresh], sceneStyle: '', seed: '', storyboard: {}, sceneDirector: {},
    referenceState: captureReferenceState(project) });
  project.cuts = copy(scene.cuts); project.sceneStyle = ''; project.seed = ''; project.storyboard = {}; project.sceneDirector = {};
  return true;
}

export function deleteScene(project, id) {
  saveActiveScene(project);
  const index = project.scenes.findIndex((scene) => scene.id === id);
  if (index < 0) return false;
  project.scenes.splice(index, 1);
  if (!project.scenes.length) {
    const freshId = `scene-${globalThis.crypto?.randomUUID?.() || Date.now()}`;
    project.scenes.push({ id: freshId, title: 'Сцена 01', cuts: [{ id: `${freshId}-shot-1`, duration: 5, prompt: '' }],
      sceneStyle: '', seed: '', storyboard: {}, sceneDirector: {} });
  }
  const next = project.scenes[Math.min(index, project.scenes.length - 1)];
  project.activeSceneId = next.id;
  project.cuts = copy(next.cuts); project.sceneStyle = next.sceneStyle; project.seed = next.seed;
  project.storyboard = copy(next.storyboard); project.sceneDirector = copy(next.sceneDirector || {});
  restoreReferenceState(project, next);
  return true;
}

export function groupShots(project, ids, title) {
  const selected = new Set(ids);
  const indexes = project.cuts.map((cut, index) => selected.has(cut.id) ? index : -1).filter((index) => index >= 0);
  const matches = indexes.map((index) => project.cuts[index]);
  if (matches.length < 2 || indexes[indexes.length - 1] - indexes[0] + 1 !== indexes.length || matches.some((cut) => cut.groupId)) return null;
  const id = `block-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  project.storyboard ||= {};
  project.storyboard.shotGroups ||= {};
  project.storyboard.shotGroups[id] = { id, title: String(title || '').trim().slice(0, 80) || `Блок ${Object.keys(project.storyboard.shotGroups).length + 1}` };
  project.cuts = project.cuts.map((cut) => selected.has(cut.id) ? { ...cut, groupId: id } : cut);
  return id;
}

export function renameShotGroup(project, id, title) {
  const group = project.storyboard?.shotGroups?.[id];
  if (!group) return false;
  group.title = String(title || '').trim().slice(0, 80) || group.title;
  return true;
}

export function ungroupShots(project, id) {
  if (!project.storyboard?.shotGroups?.[id]) return false;
  project.cuts = project.cuts.map((cut) => cut.groupId === id ? { ...cut, groupId: undefined } : cut);
  delete project.storyboard.shotGroups[id];
  return true;
}
