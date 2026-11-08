// Generation: validated settings snapshot and submission to the Mac queue.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { alignPromptPictures } from './prompt-reference-bindings.mjs?v=20261014-heading-1';
import { bindExplicitSubjects } from './reference-subjects.mjs?v=20261029-scene-run-2';
import { prepareReferenceExcerpts } from './reference-excerpts.mjs?v=20261029-scene-run-1';
import { cacheSettings } from './cache-controls.mjs?v=20261009-1';
import { renderSettings } from './render-experiments.mjs?v=20261021-2';
import { resolveNamedReplacements } from './named-character-references.mjs?v=20261029-isolated-1';
import { parsePromptDirection } from './prompt-direction.mjs?v=20261008-auto-camera-1';
import { validateReplacementMotion } from './replacement-policy.mjs?v=20261004-cleanup-1';
import { swapMaskSetting } from './swap-mask-editor.mjs?v=20261105-review-1';
import { swapLoraAdapter, swapLoraSetting } from './swap-lora.mjs?v=20261104-swap15-1';
import { samplingProfile } from './sampling-profile.mjs?v=20261104-refbeta-1';
import { DIRECTOR_PRESETS, compileDirectorState, getPreset } from './director-presets.mjs?v=20261031-lighting-15-1';
import { directorConfigForState } from './director-config.mjs?v=20261007-camera-options-5';
import { colorGradeForState, filmLookForState, loadImagePixels, presetPhotoReferences } from './director-references.mjs';
import { cameraOpticsForPresets, cameraStateForPresets } from './director-camera.mjs?v=20261008-auto-camera-1';
import { officialSettings } from './official-controls.mjs?v=20261106-refmatch';
import { referenceOwnerLabels, subjectNames } from './reference-owners.mjs?v=20261029-scene-run-2';
import { probeReferenceVideoDuration } from './reference-duration.mjs?v=20261006-1';
import { prepareShotReferences, validateSourceWindow } from './long-scene.mjs?v=20261006-auto-2';
import { promptSettingsPackage } from './prompt-settings-package.mjs?v=20261008-1';
import { officialPromptSettings, reconcilePromptCamera } from './official-prompt-settings.mjs?v=20261008-auto-multishot-1';
import { heroNames, performerSpec } from './reference-pairing.mjs?v=20261015-1';
import { addActingVoiceDirection, addReferenceRoleDirections, addScaleContinuityDirection, canSubmitStyleAdapter, effectiveFrameCount, effectiveResolution } from './model.mjs?v=20261106-refmatch';
import { enabledStyleAdapters, franchiseMemberships } from './app-bootstrap.mjs?v=20261106-refmatch';
import { activeCut, activeDirector, directorPresets, syncPromptDirection } from './app-direction.mjs?v=20261106-refmatch';
import { attachLibraryAssets } from './app-library.mjs?v=20261108-clean';
import { refreshJobs } from './app-queue.mjs?v=20261106-refmatch';
import { editVideoPairing } from './app-references.mjs?v=20261108-clean';
import { $, allAssets, apiJson, files, referenceLimits, resolvedProjectStyle, showToast } from './app-shared.mjs?v=20261106-refmatch';
import { isCloudBackend } from './video-backend.mjs?v=20261108-api-1';

function colorMatchSetting() {
  const match = appState.project.colorMatch;
  const ref = match && appState.project.references.find((item) => item.id === match.asset_id && item.kind === 'image');
  return ref ? { color_match: { picture: ref.number, strength: match.strength, method: 'mkl' } } : {};
}

export function settingsSnapshot() {
  syncPromptDirection('prompt', true);
  const duration = Number(activeCut()?.duration);
  if (/⟦(?:удалённый|неизвестный) (?:Picture|Video|Audio) \d+⟧/.test(activeCut().prompt))
    throw new Error('Исправьте ссылки на удалённые файлы в промпте перед отправкой');
  if (!Number.isInteger(duration) || duration < 4 || duration > 15) throw new Error('Длительность: выбери целое число от 4 до 15 секунд');
  const promptCamera = parsePromptDirection(activeCut().prompt, directorPresets()).camera || {};
  let cameraState = cameraStateForPresets(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()],
    effectiveFrameCount(duration), activeCut().prompt, promptCamera);
  // A static framing guide yields to a user camera video and to the H3 limit of three video guides.
  const videos = appState.project.references.filter((ref) => ref.kind === 'video');
  const continuations = videos.filter((ref) => ref.role === 'Продолжение');
  if (continuations.length > 1 || (continuations.length && videos.at(-1) !== continuations[0]))
    throw new Error('Для продолжения выбери одно видео и поставь его последним среди видео-референсов');
  if (continuations.length && appState.project.references.length < 2)
    throw new Error('Продолжению нужен ещё один референс героя или окружения');
  if (continuations.length && appState.project.useVideoAudio && appState.project.references.some((ref) => ref.kind === 'audio'))
    throw new Error('Продолжение со звуком нельзя совмещать с отдельным аудиореференсом');
  const staticGuide = cameraState && !['dolly', 'orbit', 'truck', 'crane', 'tilt'].includes(getPreset(activeDirector().movement, directorPresets())?.parameters?.movement);
  // A camera video drives the camera for static and moving presets alike (one camera source).
  if ((staticGuide && videos.length >= 3) || videos.some((ref) => ['Камера', 'Движение и камера'].includes(ref.role))) cameraState = null;
  effectiveResolution(appState.project.aspect, appState.project.quality, appState.project.megapixels);
  if (!Number.isInteger(Number(appState.project.steps)) || appState.project.steps < 20 || appState.project.steps > 25) throw new Error('Шаги: 20–25');
  const experimentSettings = renderSettings(appState.project);
  const cloud = isCloudBackend();
  // The MiniMax API has no ComfyUI LoRA input; GPU availability must not block
  // an otherwise valid cloud video request. The own GPU keeps its LoRA rules.
  appState.project.styleAdapter = cloud ? 'none' : resolvedProjectStyle();
  if (!cloud && !canSubmitStyleAdapter(appState.project.styleAdapter, enabledStyleAdapters)) throw new Error(`LoRA ${appState.project.styleAdapter} для выбранного сценария недоступна на GPU`);
  const gpuStyle = (appState.project.mode === 'realistic' || (appState.project.mode === 'replace'
      && appState.project.references.some(ref => ref.kind === 'image')
      && appState.project.references.some(ref => ref.kind === 'video')))
      && appState.project.styleAdapter === 'none' && !experimentSettings.render_options?.adapter
      ? 'auto' : (appState.project.styleAdapter || 'none');
  return {
    ...(cloud ? { backend: 'minimax_api', cloud_model: 'MiniMax-H3', cloud_resolution: '768P' } : { backend: 'gpu' }),
    duration, aspect_ratio: appState.project.aspect, quality: appState.project.quality, mode: appState.project.mode,
    megapixels: appState.project.quality === 'custom' ? Number(appState.project.megapixels) : null,
    steps: Number(appState.project.steps), ref_image_size: appState.project.refImageSize,
    style_adapter: cloud ? 'none' : gpuStyle, cache_mode: appState.project.cacheMode || 'safe',
    ...cacheSettings(appState.project),
    ...experimentSettings,
    ...(document.getElementById('turboMode')?.value && !cloud ? { ab_turbo: document.getElementById('turboMode').value } : {}),
    sampling_profile: samplingProfile(appState.project.samplingProfile || 'standard'),
    ...swapLoraSetting(appState.project),
    ...swapMaskSetting(appState.project, activeCut().referenceWindow?.start || 0),
    seed: appState.project.seed === '' ? null : Number(appState.project.seed),
    use_video_audio: Boolean(appState.project.useVideoAudio),
    // The Studio agent already prepares the prompt. Send it directly to H3;
    // legacy saved jobs keep their explicit settings when repeated.
    emotional_direction: false, agent_prepared_prompt: true,
    camera_state: cameraState,
    ...colorMatchSetting(),
    ...officialSettings(appState.project, duration),
    camera_optics: cameraOpticsForPresets(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()], cameraState),
    director_config: directorConfigForState(activeDirector(), directorPresets(), DIRECTOR_PRESETS, promptCamera),
  };
}

export async function enqueue(prompt = $('#mainPrompt').value, ids = appState.project.references.map((ref) => ref.id), settings = settingsSnapshot(),
                       referenceNumbers = Object.fromEntries(appState.project.references.map((ref) => [ref.id, ref.number])), applyDirection = true, productionSpec = null,
                       { compile = false } = {}) {
  const catalog = [...allAssets.values()].flatMap(asset => {
    const saved = appState.project.library?.[asset.asset_id] || {};
    const memberships = franchiseMemberships.get(asset.asset_id) || [];
    const franchises = new Set(memberships.map(item => item.franchise));
    const choices = franchises.size === 1 ? memberships.filter(item => item.franchise && item.characterName) : [];
    return choices.map(info => ({ asset, info: { ...info, hidden: Boolean(saved.hidden) } }));
  });
  const named = resolveNamedReplacements(prompt, catalog, appState.project.references);
  if (named.length) {
    for (const { info } of named) {
      if (info.preferredRole === 'Костюм' && appState.project.references.some(ref => ref.owner === info.characterName && ref.role === 'Костюм'))
        throw new Error(`У ${info.characterName} уже есть костюм в сцене. Убери прежний образ перед заменой.`);
    }
    if (appState.project.references.length + named.length > 12 || appState.project.references.filter(ref => ref.kind === 'image').length + named.length > referenceLimits.image)
      throw new Error('Для именованных персонажей не хватает мест для фото. Убери лишние референсы из сцены.');
    attachLibraryAssets(named.map(({ asset }) => asset.asset_id), new Map(named.map(({ asset, info }) => [asset.asset_id, info])));
    ids = [...new Set([...ids, ...named.map(({ asset }) => asset.asset_id)])];
    referenceNumbers = Object.fromEntries(appState.project.references.filter(ref => ids.includes(ref.id)).map(ref => [ref.id, ref.number]));
  }
  const settingsPackage = promptSettingsPackage(prompt, settings);
  prompt = settingsPackage.prompt; settings = settingsPackage.settings;
  const promptOverrides = settingsPackage.overrides;
  validateSourceWindow(appState.project.cuts, activeCut());
  const sourceWindow = activeCut().referenceWindow;
  let submissionReferences = appState.project.references;
  if (!sourceWindow) {
    const originals = submissionReferences.filter(ref => ids.includes(ref.id));
    submissionReferences = await prepareReferenceExcerpts(originals, settings.duration, apiJson);
    const replacements = new Map(submissionReferences.map((ref, i) => [originals[i].id, ref.id]));
    ids = ids.map(id => replacements.get(id) || id);
    referenceNumbers = Object.fromEntries(Object.entries(referenceNumbers).map(([id, n]) => [replacements.get(id) || id, n]));
  }
  if (sourceWindow) {
    if (sourceWindow.duration !== Number(settings.duration)) throw new Error('Длительность шота изменилась. Примени план сцены заново, чтобы обновить участки видео.');
    const originals = submissionReferences;
    submissionReferences = await prepareShotReferences(originals, sourceWindow, apiJson);
    const otherMedia = await prepareReferenceExcerpts(submissionReferences.filter(ref => ref.kind !== 'video'), settings.duration, apiJson);
    const mediaReplacements = new Map(submissionReferences.filter(ref => ref.kind !== 'video').map((ref, i) => [ref.id, otherMedia[i]]));
    submissionReferences = submissionReferences.map(ref => mediaReplacements.get(ref.id) || ref);
    const replacements = new Map(originals.map((ref, index) => [ref.id, submissionReferences[index].id]));
    ids = ids.map(id => replacements.get(id) || id);
    referenceNumbers = Object.fromEntries(Object.entries(referenceNumbers).map(([id, number]) => [replacements.get(id) || id, number]));
  }
  const attachedReferences = submissionReferences.filter((ref) => ids.includes(ref.id))
    .map((ref) => ({ ...ref, number: referenceNumbers[ref.id] ?? ref.number }));
  const nativeVideoReplacement = settings.mode === 'replace'
    && attachedReferences.some((ref) => ref.kind === 'image')
    && attachedReferences.some((ref) => ref.kind === 'video');
  validateReplacementMotion(settings, attachedReferences);
  const replacementRequested = attachedReferences.some(ref => ref.kind === 'image')
    && attachedReferences.some(ref => ref.kind === 'video')
    && (settings.mode === 'replace' || attachedReferences.some(ref => ref.kind === 'video' && ref.performers?.pairs?.length));
  if (replacementRequested && settings.backend !== 'minimax_api' && !enabledStyleAdapters.includes(swapLoraAdapter(appState.project))) {
    throw new Error('Задание не отправлено: GPU не объявляет Character Swap. Без этого адаптера H3 может оставить людей из исходного видео.');
  }
  // The official six-section prompt already holds the story and direction words.
  // Keep camera node settings: they are independent graph controls, not extra prompt text.
  const officialFormat = /^\s*subject_definitions:/.test(prompt);
  if (officialFormat) {
    settings = officialPromptSettings(settings, prompt);
    // Colour grade and film look are graph nodes, not prompt text: keep them.
    const colorGrade = await colorGradeForState(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()], loadImagePixels);
    const filmLook = filmLookForState(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()],
      Number.isInteger(settings.seed) ? settings.seed : Math.floor(Math.random() * 2 ** 31));
    settings = { ...settings, ...(colorGrade ? { color_grade: colorGrade } : {}), ...(filmLook ? { film_look: filmLook } : {}) };
    applyDirection = false;
  }
  if (applyDirection) {
    // Preset photos become real inputs: extra <Picture N> references and the H3ColorGrade node.
    const photoPresets = [...DIRECTOR_PRESETS, ...directorPresets()];
    const extra = presetPhotoReferences(activeDirector(), photoPresets, attachedReferences, (id) => allAssets.has(id));
    attachedReferences.push(...extra);
    ids = [...ids, ...extra.map((ref) => ref.id)];
    referenceNumbers = { ...referenceNumbers, ...Object.fromEntries(extra.map((ref) => [ref.id, ref.number])) };
    const colorGrade = await colorGradeForState(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()], loadImagePixels);
    const filmLook = filmLookForState(activeDirector(), [...DIRECTOR_PRESETS, ...directorPresets()],
      Number.isInteger(settings.seed) ? settings.seed : Math.floor(Math.random() * 2 ** 31));
    settings = { ...settings, ...(colorGrade ? { color_grade: colorGrade } : {}), ...(filmLook ? { film_look: filmLook } : {}) };
    const direction = parsePromptDirection(prompt, directorPresets());
    if (direction.errors.length) throw new Error(direction.errors.join(' '));
    prompt = compileDirectorState(direction.present ? direction.scene.trim() : prompt, activeDirector(), directorPresets(), direction.camera);
  }
  settings = reconcilePromptCamera({ ...settings, ...promptOverrides }, prompt);
  // Keep the envelope aligned: otherwise Mac parsing would reintroduce its trajectory.
  for (const key of ['camera_state', 'camera_optics', 'director_config']) {
    if (Object.hasOwn(promptOverrides, key)) promptOverrides[key] = settings[key];
  }
  if (!officialFormat) {
    prompt = addScaleContinuityDirection(prompt);
    prompt = addActingVoiceDirection(prompt, appState.project);
    // Preset photos are attached above; name every resulting <Picture N> before
    // the Mac store checks reference bindings. The store rebuilds this contract.
    prompt = addReferenceRoleDirections(prompt, attachedReferences);
  }
  // Only a picture bound to a hero becomes that hero's body; an unowned «Авто» picture
  // (a location, a prop) stays «Авто», otherwise H3 got "<Picture N> = body" next to
  // the user's "<Picture N> = LOCATION ONLY".
  const referenceRoles = Object.fromEntries(attachedReferences.map((ref) => [ref.id,
    nativeVideoReplacement && ref.kind === 'image' && ref.owner && (!ref.role || ref.role.startsWith('Авто'))
      ? 'Персонаж целиком' : ref.role]));
  const inferredOwners = referenceOwnerLabels(attachedReferences, prompt);
  const namedSubjects = subjectNames(prompt);
  const multipleSubjects = namedSubjects.length > 1;
  const referenceOwners = Object.fromEntries(attachedReferences.flatMap((ref) => {
    const owner = ref.manualOwner
      ? (multipleSubjects && !namedSubjects.includes(ref.owner) ? '' : ref.owner)
      : (inferredOwners.get(ref.id) || (multipleSubjects ? '' : ref.owner));
    return owner ? [[ref.id, owner]] : [];
  }));
  const bindings = attachedReferences.map(ref => ({ ...ref, role: referenceRoles[ref.id], owner: referenceOwners[ref.id] || '' }));
  prompt = alignPromptPictures(prompt, bindings);
  if (officialFormat) prompt = bindExplicitSubjects(prompt, bindings);
  const referencePerformers = Object.fromEntries(attachedReferences.flatMap(ref => {
    const spec = performerSpec(ref, attachedReferences, prompt);
    return spec ? [[ref.id, spec]] : [];
  }));
  const shortMotionRefs = [];
  for (const ref of attachedReferences) {
    const role = referenceRoles[ref.id] || ref.role;
    if (ref.kind !== 'video' || !['Движение', 'Движение и камера'].includes(role)) continue;
    const duration = await probeReferenceVideoDuration(files.get(ref.id)?.url || `/api/assets/${encodeURIComponent(ref.id)}/file`);
    if (duration != null && duration + 0.25 < Number(settings.duration)) {
      shortMotionRefs.push({ name: ref.name || 'Видео движения', duration });
    }
  }
  if (shortMotionRefs.length) {
    const refs = shortMotionRefs.map((ref) => `• ${ref.name}: ${ref.duration.toFixed(1)} с`).join('\n');
    const message = `Длительность видео движения короче ролика (${Number(settings.duration)} с):\n${refs}\n\nПродолжить? H3 будет развивать движение по промпту после конца референса; точное повторение или зацикливание не гарантируется.`;
    if (!window.confirm(message)) throw new Error('Задание не отправлено. Добавь более длинное видео движения или подтверди продолжение по промпту.');
    settings = { ...settings, allow_short_motion_reference: true };
  }
  if (heroNames(attachedReferences).length > 1) {
    const unresolved = attachedReferences.find(ref => ref.kind === 'video'
      && ['Движение', 'Движение и камера'].includes(ref.role)
      && !ref.owner && !referencePerformers[ref.id]);
    if (unresolved) {
      editVideoPairing(appState.project.references.find(ref => ref.id === (unresolved.sourceAssetId || unresolved.id)), prompt);
      throw new Error('Укажи, кого заменяет каждый исполнитель видео, затем нажми «Создать видео» ещё раз.');
    }
  }
  const body = { prompt: Object.keys(promptOverrides).length
      ? prompt + '\n```studio-settings\n' + JSON.stringify({ version: 1, settings: promptOverrides }) + '\n```'
      : prompt, asset_ids: ids, reference_numbers: referenceNumbers,
      reference_roles: referenceRoles, reference_owners: referenceOwners,
      reference_performers: referencePerformers, settings,
      ...(productionSpec ? { production_spec: productionSpec } : {}) };
  if (compile) return body;  // the scene run on the Mac sends it later (storyboard.mjs)
  const job = await apiJson('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  showToast('Задание сохранено в очереди Mac');
  await refreshJobs();
  return job;
}
