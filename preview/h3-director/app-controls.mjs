// Optional controls (acting, official H3 controls) installed through explicit callbacks.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { installOfficialControls } from './official-controls.mjs?v=20261106-refmatch';
import { installActing } from './acting.mjs?v=20261004-acting-5';
import { addReference } from './model.mjs?v=20261106-refmatch';
import { apiReady } from './app-bootstrap.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { renderLibrary } from './app-library.mjs?v=20261108-clean';
import { renderReferences } from './app-references.mjs?v=20261108-clean';
import { $, allAssets, files, showToast } from './app-shared.mjs?v=20261106-refmatch';

export let actingControl;
export let officialControls;

export function installOptionalControls() {
  actingControl = installActing({
    showToast,
    onSettingsChange: ({ mode, owner, voice, timbre, texture, accent, emotion, pace }) => {
      appState.project.actingMode = mode; appState.project.actingOwner = owner; appState.project.actingVoice = voice; appState.project.actingTimbre = timbre;
      appState.project.actingTexture = texture; appState.project.actingAccent = accent; appState.project.actingEmotion = emotion; appState.project.actingPace = pace;
      if (owner) { appState.project.actingVoices ||= {}; appState.project.actingVoices[owner] = { voice, timbre, texture, accent, emotion, pace }; }
      scheduleDraft();
    },
    voiceForOwner: (owner) => appState.project.actingVoices?.[owner],
    references: () => appState.project.references.filter((ref) => ref.kind === 'image' &&
      (String(ref.role || '').startsWith('Авто') || ['Лицо героя', 'Персонаж целиком'].includes(ref.role)))
      .map((ref) => ({ ...ref, asset_id: ref.id, original_name: ref.name })),
    owners: () => [...new Set(appState.project.references.filter((ref) => ref.kind === 'image' && ref.owner).map((ref) => ref.owner))],
    onAttach: async ({ source, prepared, owner, referenceId }) => {
      if (!apiReady) throw new Error('Локальная Studio недоступна');
      if (appState.project.references.filter((ref) => ref.kind === 'video').length >= 3 || appState.project.references.length >= 12) {
        throw new Error('Лимит сцены: до 3 видео и 12 файлов всего');
      }
      if (referenceId) {
        const selected = appState.project.references.find((ref) => ref.id === referenceId && ref.kind === 'image');
        if (selected) { selected.owner = owner; if (String(selected.role || '').startsWith('Авто')) selected.role = 'Лицо героя'; }
      }
      for (const [asset, kind, role] of [[source, 'image', 'Лицо героя'], [prepared, 'video', 'Актёрская игра']]) {
        if (appState.project.references.some((ref) => ref.id === asset.asset_id)) continue;
        allAssets.set(asset.asset_id, asset);
        files.set(asset.asset_id, { url: `/api/assets/${encodeURIComponent(asset.asset_id)}/file` });
      appState.project.library ||= {};
        appState.project.library[asset.asset_id] = { name: `${owner} · ${asset.original_name}`, category: `Персонажи / ${owner}`,
          type: kind === 'image' ? 'characters' : 'motion', characterName: owner };
        appState.project = addReference(appState.project, { id: asset.asset_id, kind, name: appState.project.library[asset.asset_id].name,
          role, owner, ...(kind === 'video' ? { retention: 'weak_reference' } : {}) });
      }
      appState.project.useVideoAudio = false;
      $('#useVideoAudio').checked = false;
      renderReferences(); renderLibrary(); scheduleDraft();
    },
    onAttachVoice: async ({ asset, owner, referenceId }) => {
      if (!apiReady) throw new Error('Локальная Studio недоступна');
      if (appState.project.references.filter((ref) => ref.kind === 'audio').length >= 3 || appState.project.references.length >= 12)
        throw new Error('Лимит сцены: до 3 аудио и 12 файлов всего');
      const selected = appState.project.references.find((ref) => ref.id === referenceId && ref.kind === 'image');
      if (selected) { selected.owner = owner; if (String(selected.role || '').startsWith('Авто')) selected.role = 'Лицо героя'; }
      if (!appState.project.references.some((ref) => ref.kind === 'image' && ref.owner === owner))
        throw new Error(`Сначала прикрепи фото героя «${owner}»`);
    allAssets.set(asset.asset_id, asset);
    files.set(asset.asset_id, { url: `/api/assets/${encodeURIComponent(asset.asset_id)}/file` });
      appState.project.library ||= {};
      appState.project.library[asset.asset_id] = { name: `${owner} · ${asset.original_name}`, category: `Персонажи / ${owner}`,
        type: 'audio', characterName: owner };
      appState.project = addReference(appState.project, { id: asset.asset_id, kind: 'audio', name: appState.project.library[asset.asset_id].name,
        role: 'Голос', owner });
      renderReferences(); renderLibrary(); scheduleDraft();
    },
  });
  officialControls = installOfficialControls({ project: () => appState.project, onChange: scheduleDraft });
}

export function syncSceneReferenceControls() {
  for (const ref of appState.project.references || []) if (!files.has(ref.id)) files.set(ref.id, {url:`/api/assets/${encodeURIComponent(ref.id)}/file`});
  $('#useVideoAudio').checked = Boolean(appState.project.useVideoAudio);
  actingControl.sync({mode:appState.project.actingMode,owner:appState.project.actingOwner,voice:appState.project.actingVoice,timbre:appState.project.actingTimbre,
    texture:appState.project.actingTexture,accent:appState.project.actingAccent,emotion:appState.project.actingEmotion,pace:appState.project.actingPace});
}
