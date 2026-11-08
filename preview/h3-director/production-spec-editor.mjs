// Edits authored staging only. Biology, evidence and reference bindings remain explicit
// in the versioned contract; saving never submits a render or grants creative approval.
import { installBeatsEditor } from './production-beats-editor.mjs?v=20261029-behavior-1';
import { newProductionSpec } from './production-spec-model.mjs?v=20261029-new-spec-1';
import { installBehaviorEditor } from './production-behavior-editor.mjs?v=20261029-behavior-1';
import { installLookEditor } from './production-look-editor.mjs?v=20261029-visual-1';
export function revisedSpec(original, fields, duration) {
  const next = structuredClone(original);
  Object.assign(next.shot, fields.shot, { duration: Number(duration), revision: original.shot.revision + 1 });
  next.shot.beats = fields.beats.map(beat => ({ ...beat, start: Number(beat.start), end: Number(beat.end) }));
  if (fields.behaviorPatterns) next.behavior_patterns = structuredClone(fields.behaviorPatterns);
  if (Object.hasOwn(fields, 'visualProfile')) next.visual_profile = structuredClone(fields.visualProfile);
  next.characters = next.characters.map((actor, index) => {
    const patch = fields.characters[index];
    return Object.entries(patch).some(([key, value]) => actor[key] !== value)
      ? { ...actor, ...patch, revision: original.shot.revision === 0 ? 1 : actor.revision + 1 } : actor;
  });
  return next;
}

function node(tag, text, attrs = {}) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  Object.assign(element, attrs);
  return element;
}

// Validation errors in the editor's own words: «Действие 1 · Что вызвало действие — не заполнено».
const FIELD_LABELS = { purpose: 'Зачем нужен кадр', state_in: 'Состояние в начале', state_out: 'Состояние в конце',
  first_frame_description: 'Что видно в самом начале кадра', spatial_layout: 'Карта пространства',
  identity: 'Описание героя', physical_constraints: 'Физические особенности', individual_behavior: 'Индивидуальное поведение',
  cause: 'Что вызвало действие', observed_body_action: 'Что видно в движении тела', effect: 'Результат и реакция',
  start_s: 'Начало, с', end_s: 'Конец, с', actors: 'Участники', franchise_id: 'Франшиза' };
const GROUPS = { beats: 'Действие', characters: 'Герой', behavior_patterns: 'Паттерн' };
export function specFieldErrors(detail) {
  if (!Array.isArray(detail)) return typeof detail === 'string' ? detail : JSON.stringify(detail);
  return detail.map((item) => {
    const loc = (item?.loc || []).filter((part) => part !== 'body' && part !== 'shot');
    const where = loc.map((part, at) => typeof part === 'number' ? `${GROUPS[loc[at - 1]] || '№'} ${part + 1}`
      : GROUPS[part] && typeof loc[at + 1] === 'number' ? '' : FIELD_LABELS[part] || part).filter(Boolean).join(' · ');
    const what = item?.type === 'string_too_short' || item?.type === 'missing' ? 'не заполнено' : item?.msg || '';
    return [where, what].filter(Boolean).join(' — ');
  }).join('; ');
}

export function openProductionSpecEditor(cut, { studio, onSaved = () => {} }) {
  if (!studio.canWriteDraft()) return;
  const scene = studio.sceneState?.();
  const sceneId = scene?.activeSceneId;
  const franchiseId = studio.project().sceneDirector?.agentScene?.franchise_id ||
    scene?.scenes?.find(item => item.id === sceneId)?.franchiseSource?.franchise_id || '';
  const original = cut.productionSpec ? structuredClone(cut.productionSpec)
    : newProductionSpec(cut, studio.references(), franchiseId);
  const snapshot = JSON.stringify(cut.productionSpec);
  const duration = cut.duration;
  const dialog = node('dialog', '', { className: 'production-spec-editor' });
  const form = node('form', '', { className: 'director-dialog-shell' });
  const header = node('header', '', { className: 'director-dialog-header' });
  header.append(node('h2', 'Постановка и поведение'));
  const close = node('button', 'Закрыть', { type: 'button', className: 'k-btn' });
  close.onclick = () => dialog.close();
  header.append(close);
  form.append(header, node('p', `${cut.productionSpec ? `Версия ${original.shot.revision}` : 'Новая постановка'} · ${duration} с. Описания действий сохраняются в языке задания.`));
  const body = node('div', '', { className: 'production-spec-fields' });
  function field(label, value, numeric = false) {
    const wrapper = node('label', label);
    const input = numeric ? node('input', '', { type: 'number', step: '0.1', min: '0', value })
      : node('textarea', '', { value: value || '', rows: 2, maxLength: 2000 });
    input.className = 'k-field';
    wrapper.append(input); body.append(wrapper);
    return () => input.value;
  }
  const purpose = field('Зачем нужен кадр', original.shot.purpose);
  const stateIn = field('Состояние в начале', original.shot.state_in);
  const stateOut = field('Состояние в конце', original.shot.state_out);
  const firstFrame = field('Что видно в самом начале кадра', original.shot.first_frame_description);
  const spatialLayout = field('Карта пространства: позиции, проходы, масштаб и контакты', original.shot.spatial_layout);
  const actors = original.characters.map(actor => {
    body.append(node('h3', actor.id));
    return { identity: field('Описание героя по назначенному референсу', actor.identity),
      physical_constraints: field('Физические особенности и ограничения', actor.physical_constraints),
      individual_behavior: field('Индивидуальное поведение', actor.individual_behavior) };
  });
  const status = node('p', '', { role: 'status' });
  const look = installLookEditor(body, { profile: original.visual_profile, references: studio.references(),
    say: message => { status.textContent = message; } });
  const franchise = node('select', '', { className: 'k-field', required: true });
  const franchiseLabel = node('label', 'Франшиза'); franchiseLabel.append(franchise); body.prepend(franchiseLabel);
  if (original.franchise_id) {
    franchise.append(new Option(original.franchise_id, original.franchise_id)); franchise.disabled = true;
  } else {
    franchise.append(new Option('Выбери франшизу', ''));
    fetch('/api/franchises').then(async response => {
      if (!response.ok) throw new Error('Не удалось загрузить франшизы');
      for (const item of await response.json()) franchise.append(new Option(item.name, item.id));
    }).catch(error => { status.textContent = error.message; });
  }
  let beats;
  const patterns = installBehaviorEditor(body, { patterns: original.behavior_patterns || [], characters: original.characters,
    getBeats: () => beats?.read() || [], onChange: () => beats?.refreshPatterns(), say: message => { status.textContent = message; } });
  beats = installBeatsEditor(body, { beats: original.shot.beats, characters: original.characters,
    duration, patterns: () => patterns.read(), say: message => { status.textContent = message; } });
  const save = node('button', 'Проверить и сохранить', { type: 'submit', className: 'k-btn' });
  form.append(body, status, save);
  dialog.append(form); document.body.append(dialog);
  // The modal lives outside the polling storyboard: queue refreshes cannot erase edits.
  dialog.addEventListener('close', () => dialog.remove());
  form.onsubmit = async event => {
    event.preventDefault(); save.disabled = true;
    try {
      const spec = revisedSpec(original, { shot: { purpose: purpose(), state_in: stateIn(), state_out: stateOut(),
        first_frame_description: firstFrame(), spatial_layout: spatialLayout() },
        characters: actors.map(fields => Object.fromEntries(Object.entries(fields).map(([key, get]) => [key, get()]))),
        beats: beats.read(), behaviorPatterns: patterns.read(), visualProfile: look.read() }, duration);
      spec.franchise_id = franchise.value;
      const response = await fetch('/api/production/spec/validate', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec) });
      const result = await response.json();
      if (!response.ok || !result.valid) throw new Error(`Постановка не сохранена: ${response.ok
        ? result.issues.map(item => `${item.code}: ${item.item_id}`).join('; ') : specFieldErrors(result.detail)}`);
      const current = studio.cuts().find(item => item.id === cut.id);
      // Validate first, then compare again: edits or scene switches during the request
      // must never overwrite a different version of the shot.
      if (!studio.canWriteDraft() || !current || studio.sceneState?.()?.activeSceneId !== sceneId || current.shotJobId !== cut.shotJobId || current.duration !== duration || JSON.stringify(current.productionSpec) !== snapshot)
        throw new Error('Шот изменён во время редактирования. Открой постановку заново.');
      const project = studio.project();
      if (!project.sceneDirector?.agentScene?.franchise_id) {
        project.sceneDirector = { ...project.sceneDirector, agentScene: {
          ...project.sceneDirector?.agentScene, franchise_id: spec.franchise_id } };
      }
      studio.editCuts(cuts => cuts.map(item => item.id === cut.id ? { ...item, productionSpec: result.spec,
        productionSpecChanged: Boolean(item.shotJobId), productionFrameChanged: Boolean(item.startFrame?.asset_id) && item.startFrame?.source !== 'previous' } : item));
      await studio.saveDraft();
      onSaved(); dialog.close();
    } catch (error) { status.textContent = error.message || String(error); }
    finally { save.disabled = false; }
  };
  dialog.showModal();
}
