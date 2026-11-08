// The Studio agent's hands in this tab (server: local_studio/ui_bridge.py). The tab polls for a command, carries it
// out with the page's own code — the same calls as the buttons — and reports the result to the waiting agent.
import { applyDraft, applySceneDraft } from './agent-draft.mjs?v=20261106-aspects-1';
import { verifyAppliedScene } from './ui-bridge-model.mjs?v=20261028-verify-1';
import { setChatStartFrame } from './chat-image-handoff.mjs?v=20261101-1';

const POLL_MS = 1500;
const tab = globalThis.crypto?.randomUUID?.() || String(Math.random()).slice(2);
const studio = () => window.h3Studio;

function notice(text) {
  let box = document.querySelector('.ui-bridge-notice');
  if (!box) {
    box = Object.assign(document.createElement('div'), { className: 'ui-bridge-notice', role: 'status' });
    document.body.append(box);
  }
  box.textContent = `✦ Агент: ${text}`;
  box.hidden = false;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => { box.hidden = true; }, 6000);
}

function board() {
  const s = studio();
  const { scenes, activeSceneId } = s.sceneState();
  const project = s.project();
  return {
    active_scene: activeSceneId,
    franchise_id: project.sceneDirector?.agentScene?.franchise_id ||
      scenes.find((scene) => scene.id === activeSceneId)?.franchiseSource?.franchise_id || '',
    scenes: scenes.map((scene) => ({ id: scene.id, title: scene.title, shots: (scene.cuts || []).length })),
    scene_settings: { mode: project.mode, aspect_ratio: project.aspect, quality: project.quality,
      megapixels: project.megapixels, steps: project.steps, style: project.sceneStyle || '' },
    build: window.h3Storyboard?.state?.() || null,
    references: s.references().map((ref) => ({ asset_id: ref.id, kind: ref.kind, number: ref.number, role: ref.role, owner: ref.owner || '' })),
    shots: s.cuts().map((cut, index) => ({ shot: index + 1, id: cut.id, title: cut.sceneShot?.title || '', duration: cut.duration,
      start: cut.startFrame?.source || 'keyframe', start_frame_asset: cut.startFrame?.asset_id || null,
      transition_in: cut.sceneShot?.transition_in || '', job_id: cut.shotJobId || null,
      keyframe_jobs: cut.keyframeJobIds || [], frame_accepted: Boolean(window.h3Storyboard?.frameAccepted?.(cut.id)),
      prompt: String(cut.prompt || '').slice(0, 240),
      framing: cut.sceneShot?.framing || '', angle: cut.sceneShot?.angle || '',
      lens_mm: cut.sceneShot?.lens_mm || null, camera_motion: cut.sceneShot?.camera_motion || '',
      state_before: cut.sceneShot?.state_before || '', state_after: cut.sceneShot?.state_after || '',
      director: cut.director || {},
      production_spec: cut.productionSpec ? { shot_id: cut.productionSpec.shot?.id, revision: cut.productionSpec.shot?.revision,
        beats: cut.productionSpec.shot?.beats?.length || 0, franchise_id: cut.productionSpec.franchise_id } : null })),
  };
}

const ACTIONS = {
  set_start_frame: async ({ scene_id, shot_id, asset_id }) => setChatStartFrame({ studio: studio(),
    storyboard: window.h3Storyboard, sceneId: scene_id, shotId: shot_id, assetId: asset_id }),
  board: async () => board(),
  // «Создать видео»: the agent fills the whole form (references, performer pairs, prompt, render settings, recipe).
  apply_draft: async ({ draft }) => {
    if (!draft?.prompt) throw new Error('Нет черновика для формы');
    const applied = await applyDraft(draft);
    notice('форма «Создать видео» заполнена');
    return { applied };
  },
  apply_scene: async ({ scene, scene_id }) => {
    if (!scene?.shots?.length) throw new Error('Нет сцены для холста');
    // An agent bound to its own scene rebuilds that scene in place (the old board stays in previousCuts).
    const done = await applySceneDraft(scene, window.h3Studio, { inPlace: Boolean(scene_id) });
    notice(`сцена «${scene.title || ''}» на холсте`);
    const state = board();
    return { applied: done, verification: verifyAppliedScene(scene, state), board: state };
  },
  build_scene: async () => {
    if (!window.h3Storyboard?.build) throw new Error('Раскадровка ещё не загрузилась — открой «Видео студию»');
    const started = await window.h3Storyboard.build();
    notice(started?.started ? `собираю сцену: шоты ${started.pending.join(', ')}` : 'все шоты уже готовы');
    return started || { started: false };
  },
  stop_build: async () => { window.h3Storyboard?.stop?.(); notice('сборка остановлена'); return { stopped: true }; },
  reset_scene: async () => {
    if (!studio().resetScene()) throw new Error('Сцену не удалось сбросить');
    notice('сцена сброшена');
    return board();
  },
  select_scene: async ({ scene_id }) => {
    studio().selectScene(scene_id);
    if (studio().sceneState().activeSceneId !== scene_id) throw new Error(`Нет сцены ${scene_id}`);
    return board();
  },
  new_scene: async ({ title }) => {
    studio().createScene();
    if (title) studio().renameScene(studio().sceneState().activeSceneId, String(title).slice(0, 100));
    return board();
  },
};

// A chat belongs to one scene (agent_chat.py scene_id): its commands act in that scene, not in the one the user
// is looking at. The tab switches to it for the command and back; «Собрать сцену» stays there, since the build
// runs on the open scene.
async function inScene(sceneId, run, stay) {
  const s = studio();
  const before = s.sceneState().activeSceneId;
  if (!sceneId || sceneId === before) return run();
  if (!s.sceneState().scenes.some((scene) => scene.id === sceneId)) throw new Error('Сцена этого чата удалена — агент остановлен');
  s.selectScene(sceneId);
  try { return await run(); }
  finally {
    if (!stay && s.sceneState().scenes.some((scene) => scene.id === before)) s.selectScene(before);
    else if (stay) notice(`открыта сцена агента «${s.sceneState().scenes.find((scene) => scene.id === sceneId)?.title || ''}»`);
  }
}

async function report(id, body) {
  try {
    const response = await fetch(`/api/ui/commands/${id}/result`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, tab }) });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      notice(failure.detail || 'Ответ не принят; сверь холст и очередь перед повтором.');
    }
  } catch { notice('Результат команды не подтверждён; сверь холст и очередь перед повтором.'); }
}

async function poll() {
  try {
    if (!studio()?.canWriteDraft?.()) return;  // still loading, or another tab owns the project
    const response = await fetch(`/api/ui/commands/next?tab=${tab}`);
    if (response.status !== 200) return;
    const command = await response.json();
    try {
      const run = ACTIONS[command.action];
      if (!run) throw new Error(`Неизвестная команда ${command.action}`);
      const payload = command.payload || {};
      const result = await inScene(payload.scene_id, () => run(payload), command.action === 'build_scene');
      await studio().saveDraft?.();
      await report(command.id, { ok: true, result });
    } catch (error) {
      await report(command.id, { ok: false, error: String(error?.message || error).slice(0, 1500) });
    }
  } catch { /* Studio restarting */ } finally {
    setTimeout(poll, POLL_MS);
  }
}

setTimeout(poll, POLL_MS);
