// Application composition root. The page's parts live in app-*.mjs modules (Create stage,
// direction, references, composer, library, story, settings, generation, queue, startup);
// this file builds the window.h3Studio facade for side panels and installs the parts in order.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { selectAspect } from './aspects.mjs?v=20261106-aspects-1';
import { importFranchisePacket } from './franchise-packet.mjs?v=20261106-refmatch';
import { installFranchiseHandoff } from './franchise-handoff.mjs?v=20261106-refmatch';
import { prepareReferenceExcerpts } from './reference-excerpts.mjs?v=20261029-scene-run-1';
import { sceneRequests } from './scene-queue.mjs?v=20261031-timecode-1';
import { RUN_TOKEN, defaultStartFrame, referencesWithStartFrame, withSceneStyle } from './storyboard-model.mjs?v=20261102-take-admission-1';
import { normalizeLegacyIr, placeOff, pruneAbsentHeroMetadata, renumberTags, shotReferences } from './shot-references.mjs?v=20261101-breakdown-1';
import { loadScenePlaces, sceneFranchiseId } from './scene-places.mjs?v=20261101-place-1';
import { DIRECTOR_PRESETS } from './director-presets.mjs?v=20261031-lighting-15-1';
import { colorGradeForState, filmLookForState, loadImagePixels, presetPhotoReferences } from './director-references.mjs';
import { getLocale } from './locale.mjs?v=20261108-i18n-1';
import { installAppI18n } from './app-i18n.mjs?v=20261101-studio-names-1';
import { referenceOwnerLabels } from './reference-owners.mjs?v=20261029-scene-run-2';
import { addReference, createProject, removeReference, renumberReferences } from './model.mjs?v=20261106-refmatch';
import { addScene as addProjectScene, deleteScene as deleteProjectScene, groupShots as groupProjectShots, normalizeSceneProject, renameScene, renameShotGroup, resetScene as resetProjectScene, saveActiveScene, switchScene, ungroupShots as ungroupProjectShots } from './scene-project-model.mjs?v=20261031-render-state-1';
import { apiReady, bootstrap, hydrating } from './app-bootstrap.mjs?v=20261106-refmatch';
import { installOptionalControls, syncSceneReferenceControls } from './app-controls.mjs?v=20261106-refmatch';
import { activeCut, activeDirector, directorPresets, installDirectionControls, presetForm, renderDirectorGrid, renderDirectorProfiles, renderDirectorStrip, selectCut } from './app-direction.mjs?v=20261106-refmatch';
import { draftStale, installDraftSync, persistDraft, reportDraftFailure, scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { enqueue, settingsSnapshot } from './app-generation.mjs?v=20261108-api-1';
import { addFiles, installLibraryControls, renderLibrary, renderLibraryTypes } from './app-library.mjs?v=20261108-clean';
import { installVideoBackend } from './video-backend.mjs?v=20261108-api-1';
import { installQueue, renderJobs } from './app-queue.mjs?v=20261106-refmatch';
import { installReferenceScroll, renderBrief, renderCuts, renderEffectiveSize, renderReferences } from './app-references.mjs?v=20261108-clean';
import { $, allAssets, apiJson, audioRoles, files, imageRoles, videoRoles } from './app-shared.mjs?v=20261106-refmatch';
import { renderStage } from './app-stage.mjs?v=20261106-refmatch';
import { addScene, installStoryControls, installTimelineControls } from './app-story.mjs?v=20261106-refmatch';
import { installVideoWorkflow } from './app-workflow.mjs?v=20261108-clean';
import { installComposer } from './app-composer.mjs?v=20261106-refmatch';
import { installProjectSettings } from './app-settings.mjs?v=20261106-refmatch';

// Side panels use this narrow facade instead of importing the composition root.
appState.project = normalizeSceneProject(createProject());
// Read-only handle for side panels (scenarist.mjs) that need the current references.
// A deleted or reset scene: its build on the Mac stops and is forgotten (local_studio/scene_runs.py).
function forgetSceneRun(sceneId) {
  if (sceneId) void fetch(`/api/scene-runs/${encodeURIComponent(sceneId)}`, { method: 'DELETE' }).catch(() => {});
}

window.h3Studio = {
  project: () => appState.project,
  async contextSnapshot() {
    const prompt = $('#mainPrompt').value;
    const settings = structuredClone(settingsSnapshot());
    const state = structuredClone(activeDirector());
    const presets = structuredClone([...DIRECTOR_PRESETS, ...directorPresets()]);
    let references = structuredClone(appState.project.references);
    if (!activeCut().referenceWindow) references = await prepareReferenceExcerpts(references, settings.duration, apiJson);
    references.push(...presetPhotoReferences(state, presets, references, (id) => allAssets.has(id)));
    const owners = referenceOwnerLabels(references, prompt);
    const refs = references.map(ref => ({ asset_id: ref.id, kind: ref.kind, number: ref.number,
      role: ref.role || '', owner: ref.manualOwner ? (ref.owner || '') : (owners.get(ref.id) || ref.owner || ''),
      performer: ref.performers?.pairs?.length === 1 ? (ref.performers.pairs[0].performer || '') : '' }));
    const colorGrade = await colorGradeForState(state, presets, loadImagePixels);
    const filmLook = filmLookForState(state, presets, Number.isInteger(settings.seed) ? settings.seed : 0);
    return { brief: prompt, settings: { ...settings, ...(colorGrade ? { color_grade: colorGrade } : {}),
      ...(filmLook ? { film_look: filmLook } : {}) }, references: refs };
  },
  cuts: () => appState.project.cuts,
  setCutPrompt(id, text) {
    const cut = appState.project.cuts.find((item) => item.id === id);
    if (!cut) return;
    cut.prompt = text;
    if (activeCut().id === id) $('#mainPrompt').value = text;
    renderCuts(); scheduleDraft();
  },
  setCutSceneAction(id, text) {
    const cut = appState.project.cuts.find((item) => item.id === id);
    if (!cut) return;
    cut.sceneShot = { ...(cut.sceneShot || {}), action:String(text).slice(0,700) };
    renderCuts(); scheduleDraft();
  },
  // Sends one cut with its own duration and direction (scenes.mjs «Все кадры»).
  // storyboard.mjs passes startFrameAssetId (null = none): only this shot starts from that
  // picture («Кадр сцены» + guide_frames [[n,0]]) and matches colour to it, nothing is kept.
  // environmentAssetId: the previous shot's last frame, sent as «Окружение» across a cut.
  // compile: return the job body instead of sending it; ids starting with RUN_TOKEN are stand-ins for pictures the
  // scene run on the Mac fetches later (storyboard-model.mjs runSources).
  async enqueueCut(id, { withSceneFrame = true, startFrameAssetId, colorMatchAssetId = null, colorMatchStrength = 0.6,
    environmentAssetId = null, continuationAssetId = null, compile = false } = {}) {
    selectCut(id);
    let request = sceneRequests([activeCut()], settingsSnapshot())[0];
    if (startFrameAssetId !== undefined) {
      const standIn = (assetId, kind) => (compile && String(assetId || '').startsWith(RUN_TOKEN)
        ? { asset_id: assetId, kind, original_name: kind === 'video' ? 'Продолжение' : 'Кадр сцены' } : null);
      const image = (assetId, label) => {
        const asset = assetId && (standIn(assetId, 'image') || allAssets.get(assetId));
        if (assetId && (!asset || asset.kind !== 'image')) throw new Error(`${label} не найден в библиотеке`);
        return asset ? { id: asset.asset_id, name: asset.original_name } : null;
      };
      const start = image(startFrameAssetId, 'Стартовый кадр');
      const sample = image(colorMatchAssetId, 'Образец цвета');
      const place = image(environmentAssetId, 'Последний кадр предыдущего шота');
      const continuationAsset = continuationAssetId ? standIn(continuationAssetId, 'video') || allAssets.get(continuationAssetId) : null;
      if (continuationAssetId && (!continuationAsset || continuationAsset.kind !== 'video'))
        throw new Error('Видео продолжения отсутствует в библиотеке');
      if (continuationAssetId && appState.project.references.some(ref => ref.kind === 'video' && ref.role === 'Продолжение'))
        throw new Error('В материалах уже есть видео продолжения; выбери один источник');
      const saved = { references: appState.project.references, colorMatch: appState.project.colorMatch, guideFrames: appState.project.guideFrames };
      // Heroes who are not in this shot and materials switched off for it on the canvas stay out (shot-references.mjs); tags follow the new numbers.
      const preparedPrompt = pruneAbsentHeroMetadata(normalizeLegacyIr(request.prompt), appState.project.references, activeCut()?.sceneShot?.characters);
      // A location picture of another passport place stays out of this shot (scene-places.mjs).
      const places = await loadScenePlaces(sceneFranchiseId(appState.project));
      const cast = shotReferences(appState.project.references, preparedPrompt,
        (request.production_spec?.characters || []).flatMap(actor => actor.reference_asset_ids || []),
        activeCut()?.sceneShot?.characters, [...(activeCut()?.offRefs || []),
          ...placeOff(appState.project.references, preparedPrompt, activeCut()?.sceneShot?.location, places)]);
      const prompt = renumberTags(preparedPrompt, cast.renumber);
      appState.project.references = referencesWithStartFrame(cast.references, start, sample, place).references;
      if (continuationAssetId) {
        const tail = continuationAsset;
        const number = Math.max(0, ...appState.project.references.filter(ref => ref.kind === 'video').map(ref => ref.number || 0)) + 1;
        appState.project.references.push({ id:tail.asset_id, kind:'video', name:tail.original_name, role:'Продолжение', number });
      }
      appState.project.colorMatch = sample ? { asset_id: sample.id, strength: Number(colorMatchStrength) || 0.6 } : null;
      appState.project.guideFrames = null;
      try {
        // Settings from the shot's own references: the start frame anchored at frame 0 (guide_frames) and the colour
        // sample (color_match). Read before the patch they missed both.
        request = { ...sceneRequests([activeCut()], settingsSnapshot())[0], prompt };
        if (continuationAssetId) request.settings = { ...request.settings, guide_frames:[], continuation_trim_frames:22 };
        return await enqueue(withSceneStyle(request.prompt, appState.project.sceneStyle), appState.project.references.map((ref) => ref.id), request.settings, undefined, true, request.production_spec, { compile });
      }
      finally { appState.project.references = saved.references; appState.project.colorMatch = saved.colorMatch; appState.project.guideFrames = saved.guideFrames; }
    }
    if (withSceneFrame) return enqueue(request.prompt, appState.project.references.map((ref) => ref.id), request.settings, undefined, true, request.production_spec);
    // Later cuts continue the story, not the same opening frame: the previous clip's last frame
    // stays as the location reference («Окружение»), numbering unchanged, no frame-0 anchor.
    const frames = appState.project.references.filter((ref) => ref.role === 'Кадр сцены');
    frames.forEach((ref) => { ref.role = 'Окружение'; });
    try {
      return await enqueue(request.prompt, appState.project.references.map((ref) => ref.id), { ...request.settings, guide_frames: [] }, undefined, true, request.production_spec);
    } finally {
      frames.forEach((ref) => { ref.role = 'Кадр сцены'; });
    }
  },
  addReferenceAsset(asset, role, owner = '') {
    allAssets.set(asset.asset_id, asset);
    files.set(asset.asset_id, { url: `/api/assets/${encodeURIComponent(asset.asset_id)}/file` });
    appState.project = addReference(appState.project, { id: asset.asset_id, kind: asset.kind, name: asset.original_name, role, owner });
    renderReferences(); scheduleDraft();
    return appState.project.references.find((ref) => ref.id === asset.asset_id);
  },
  // «Цвет как в предыдущем кадре» (ColorMatchV2): colours follow this asset; null switches it off.
  setColorMatch(value) {
    appState.project.colorMatch = value && value.asset_id ? { asset_id: value.asset_id, strength: Number(value.strength) || 0.6 } : null;
    scheduleDraft();
    return appState.project.colorMatch;
  },
  // storyboard.mjs: the board edits cuts (order, add, remove, start frames, job ids).
  ready: () => apiReady && !hydrating,
  rememberAsset(asset) { allAssets.set(asset.asset_id, asset); },
  editCuts(change) {
    const next = change(appState.project.cuts.map((cut) => ({ ...cut })));
    if (!Array.isArray(next) || !next.length) return appState.project.cuts;
    appState.project.cuts = next;
    if (!next.some((cut) => cut.id === appState.selectedCutId)) appState.selectedCutId = next[0].id;
    selectCut(appState.selectedCutId); scheduleDraft();
    return appState.project.cuts;
  },
  // A new shot continues from the end of the previous one by default (see defaultStartFrame).
  addShot({ link = true } = {}) {
    const startFrame = link ? defaultStartFrame(appState.project.cuts) : null;
    addScene(false); const cut = appState.project.cuts.at(-1);
    cut.startFrame = startFrame;
    window.dispatchEvent(new CustomEvent('h3:shot-added', { detail: { cutId: cut.id } })); return cut; },
  activeSceneId: () => appState.project.activeSceneId,
  activeCutId: () => appState.selectedCutId,
  sceneState() { saveActiveScene(appState.project); return { scenes: appState.project.scenes, activeSceneId: appState.project.activeSceneId }; },
  selectScene(id) {
    if (!appState.project.scenes?.some((scene) => scene.id === id) || id === appState.project.activeSceneId) return;
    switchScene(appState.project, id); appState.selectedCutId = appState.project.cuts[0]?.id || null;
    $('#modeSelect').value = appState.project.mode;
    selectAspect($('#aspect'), appState.project.aspect);
    selectCut(appState.selectedCutId); $('#mainPrompt').value = activeCut()?.prompt || '';
    syncSceneReferenceControls(); renderReferences(); renderCuts(); renderDirectorStrip(); scheduleDraft();
    window.h3Storyboard?.refresh(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.dispatchEvent(new CustomEvent('h3:scene-active-changed'));
  },
  createScene() {
    addProjectScene(appState.project); appState.selectedCutId = appState.project.cuts[0].id;
    selectCut(appState.selectedCutId); $('#mainPrompt').value = '';
    syncSceneReferenceControls(); renderReferences(); renderCuts(); renderDirectorStrip(); scheduleDraft();
    window.h3Storyboard?.refresh(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.dispatchEvent(new CustomEvent('h3:scene-active-changed'));
  },
  // «Сбросить сцену»: one empty shot, no materials, style, storyboard or plan (scene-project-model.mjs).
  resetScene() {
    const sceneId = appState.project.activeSceneId;
    if (!resetProjectScene(appState.project)) return false;
    forgetSceneRun(sceneId);
    appState.selectedCutId = appState.project.cuts[0].id;
    selectCut(appState.selectedCutId); $('#mainPrompt').value = '';
    syncSceneReferenceControls(); renderReferences(); renderCuts(); renderDirectorStrip(); scheduleDraft();
    window.h3Storyboard?.refresh(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.dispatchEvent(new CustomEvent('h3:scene-active-changed'));
    return true;
  },
  renameScene(id, title) { renameScene(appState.project, id, title); scheduleDraft(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); },
  deleteScene(id) {
    if (!deleteProjectScene(appState.project, id)) return false;
    forgetSceneRun(id);
    appState.selectedCutId = appState.project.cuts[0]?.id || null;
    selectCut(appState.selectedCutId); $('#mainPrompt').value = activeCut()?.prompt || '';
    syncSceneReferenceControls(); renderReferences(); renderCuts(); renderDirectorStrip(); scheduleDraft();
    window.h3Storyboard?.refresh(); window.dispatchEvent(new CustomEvent('h3:scenes-changed'));
    window.dispatchEvent(new CustomEvent('h3:scene-active-changed'));
    return true;
  },
  groupShots(ids, title) {
    const id = groupProjectShots(appState.project, ids, title);
    if (!id) return false;
    scheduleDraft(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.h3Storyboard?.refresh(); return id;
  },
  renameShotGroup(id, title) { const changed = renameShotGroup(appState.project, id, title); if (changed) { scheduleDraft(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.h3Storyboard?.refresh(); } return changed; },
  ungroupShots(id) { const changed = ungroupProjectShots(appState.project, id); if (changed) { scheduleDraft(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.h3Storyboard?.refresh(); } return changed; },
  selectCut(id) { selectCut(id); },
  // Callers often fire-and-forget (`void saveDraft()`); a failed save is reported once here
  // and the returned promise still rejects for callers that await it.
  saveDraft: () => reportDraftFailure(persistDraft()),
  canWriteDraft: () => apiReady && !hydrating && !draftStale,
  async importFranchiseScene(packet) {
    if (!apiReady || hydrating || draftStale) throw new Error('Обнови Studio: текущая вкладка ещё загружается или проект изменён в другой вкладке.');
    for (const ref of packet.references) {
      const asset = allAssets.get(ref.id);
      const roles = ref.kind === 'image' ? imageRoles : ref.kind === 'video' ? videoRoles : audioRoles;
      if (!asset || asset.kind !== ref.kind || !roles.includes(ref.role)) throw new Error('Материал или его роль недоступны в Studio: ' + (ref.name || ref.id));
    }
    saveActiveScene(appState.project);
    const before = JSON.stringify(appState.project);
    await persistDraft();
    if (draftStale || JSON.stringify(appState.project) !== before) throw new Error('Проект изменился во время сохранения. Проверь его и повтори передачу сцены.');
    appState.project = importFranchisePacket(appState.project, packet); appState.selectedCutId = appState.project.cuts[0].id;
    selectCut(appState.selectedCutId); $('#mainPrompt').value = activeCut().prompt;
    syncSceneReferenceControls();
    renderReferences(); renderCuts(); renderDirectorStrip();
    window.h3Storyboard?.refresh(); window.dispatchEvent(new CustomEvent('h3:scenes-changed')); window.dispatchEvent(new CustomEvent('h3:scene-active-changed'));
    await persistDraft();
    if (draftStale) throw new Error('Проект изменён в другой вкладке. Обнови Studio и проверь сохранённую сцену.');
    return appState.project.activeSceneId;
  },
  // «Библия сцены»: the shared references, the same pictures and order in every shot.
  referenceRoles: () => ({ image: imageRoles, video: videoRoles, audio: audioRoles }),
  references: () => appState.project.references,
  referenceUrl: (id) => files.get(id)?.url || `/api/assets/${encodeURIComponent(id)}/file`,
  updateReference(id, { role, owner } = {}) {
    const ref = appState.project.references.find((item) => item.id === id);
    if (!ref) return;
    if (role !== undefined) {
      ref.role = role;
      if (!['Лицо героя', 'Персонаж целиком', 'Костюм', 'Актёрская игра', 'Движение', 'Движение и камера', 'Голос'].includes(ref.role)) ref.owner = '';
    }
    if (owner !== undefined) ref.owner = String(owner).trim().slice(0, 80);
    renderReferences(); renderBrief(); scheduleDraft();
  },
  moveReference(id, delta) {
    const list = [...appState.project.references];
    const index = list.findIndex((ref) => ref.id === id);
    if (index < 0) return;
    // Numbers are per kind (<Picture N>, <Audio N>): swap with the neighbour of the same kind.
    const same = list.flatMap((ref, at) => (ref.kind === list[index].kind ? [at] : []));
    const target = same[same.indexOf(index) + Math.sign(delta)];
    if (target === undefined) return;
    [list[index], list[target]] = [list[target], list[index]];
    appState.project = renumberReferences({ ...appState.project, references: list }, appState.project.references);
    $('#mainPrompt').value = activeCut().prompt;
    renderReferences(); renderCuts(); scheduleDraft();
  },
  removeReference(id) {
    if (!appState.project.references.some((ref) => ref.id === id)) return;
    files.delete(id);
    appState.project = removeReference(appState.project, id);
    $('#mainPrompt').value = activeCut().prompt;
    renderReferences(); renderCuts(); scheduleDraft();
  },
  addFiles: (incoming, role = null) => addFiles(incoming, role),
  openLibrary() { $('#openLibrary').click(); },
  setSeed(value) { appState.project.seed = value === '' || value == null ? '' : String(value); $('#seed').value = appState.project.seed; scheduleDraft(); },
  // A frame of a finished clip becomes «Кадр сцены»: the next generation starts from it.
  addSceneFrame(asset) {
    allAssets.set(asset.asset_id, asset);
    files.set(asset.asset_id, { url: `/api/assets/${encodeURIComponent(asset.asset_id)}/file` });
    appState.project.references = appState.project.references.filter((ref) => ref.role !== 'Кадр сцены');
    appState.project = addReference(appState.project, { id: asset.asset_id, kind: 'image', name: asset.original_name, role: 'Кадр сцены' });
    renderReferences(); renderCuts(); scheduleDraft();
    return appState.project.references.find((ref) => ref.id === asset.asset_id);
  },
};

// Wiring order matters: each part registers its listeners in the order the page always used.
installDraftSync();
installDirectionControls();
installReferenceScroll();
installLibraryControls();
installVideoWorkflow();
installStoryControls();
installComposer();
installTimelineControls();
installProjectSettings();
installQueue();
installVideoBackend();
installOptionalControls();
installAppI18n(document.body, getLocale);
window.addEventListener('studio:localechange', () => {
  renderDirectorProfiles();
  renderDirectorStrip();
  renderDirectorGrid();
  presetForm.relocalize();
  renderLibraryTypes();
  renderLibrary();
  renderCuts();
  renderJobs();
  renderStage();
  renderEffectiveSize();
});
void bootstrap();
installFranchiseHandoff(window.h3Studio);
