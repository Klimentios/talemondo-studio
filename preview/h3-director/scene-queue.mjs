// Validate the entire batch before sending any scene. Preserve unsent scenes on failure.
import { directorConfigForState, normalizeDirectorParameters } from './director-config.mjs?v=20261007-camera-options-5';
import { DIRECTOR_PRESETS } from './director-presets.mjs?v=20261031-lighting-15-1';

const directionLabel = (value) => String(value || '').trim();

// The board's own camera for this shot (framing, angle, purpose, emotion and seam; lens; camera path) wins over the
// shot's director presets; every value is checked against app/director_schema.json, unknown ones are left out.
export function settingsForShot(settings, cut) {
  const sceneShot = cut?.sceneShot || {};
  const config = { ...(settings?.director_config || {}) };
  // The board stores a complete preset set on each cut. Compile it for this cut before applying
  // explicit camera fields; otherwise every queued shot inherits the currently selected cut.
  // A category the snapshot already compiled from the same preset keeps its prompt overrides (speed, end framing).
  if (cut?.director) for (const [category, entry] of Object.entries(directorConfigForState(
    cut.director, settings?.custom_director_presets || [], DIRECTOR_PRESETS)))
    if (config[category]?.id !== entry.id) config[category] = entry;
  const override = (category, raw) => {
    const parameters = normalizeDirectorParameters(category, raw);
    if (!Object.keys(parameters).length) return;
    const base = { ...(config[category]?.parameters || {}) };
    // A named move (whip pan, crash zoom…) belongs to its own path; another path drops it with its direction.
    if (category === 'movement' && base.movement !== parameters.movement) { delete base.camera_move; delete base.direction; }
    config[category] = { id: `${category}.scene_board`, parameters: { ...base, ...parameters } };
  };
  override('shot', { framing: sceneShot.framing, angle: sceneShot.angle, purpose: sceneShot.shot_purpose,
    emotion: sceneShot.emotion, transition_in: sceneShot.transition_in });
  override('lens', { focal_length_mm: Number(sceneShot.lens_mm) || undefined });
  override('movement', { movement: sceneShot.camera_motion });
  return { ...settings, director_config: config };
}

export function promptForShot(cut) {
  const prompt = cut?.prompt || '';
  const action = directionLabel(cut?.sceneShot?.action);
  if (!action || prompt.includes(action)) return prompt;
  // H3 IR is sectioned: keep shot direction inside detailed_description, never after the final audio section.
  if (/^\s*subject_definitions\s*:/i.test(prompt)) {
    const match = /^detailed_description\s*:/im.exec(prompt);
    if (!match) throw new Error('Промпт H3: нет раздела detailed_description для действия шота');
    const next = /^\s*(?:overall_soundscape|diegetic_sounds|non_diegetic_music)\s*:/im;
    const remainder = prompt.slice(match.index + match[0].length);
    const end = next.exec(remainder);
    const insert = end ? match.index + match[0].length + end.index : prompt.length;
    return `${prompt.slice(0, insert).trimEnd()}\nShot-specific action direction: ${action}\n\n${prompt.slice(insert).trimStart()}`;
  }
  return `${prompt.trim()}\n\nShot-specific action direction: ${action}`;
}

export function sceneRequests(cuts, settings) {
  return cuts.map((cut, index) => {
    const duration = Number(cut.duration);
    let prompt = promptForShot(cut);
    if (!prompt?.trim()) throw new Error(`Сцена ${index + 1}: добавьте описание`);
    if (!Number.isInteger(duration) || duration < 4 || duration > 15)
      throw new Error(`Сцена ${index + 1}: длительность от 4 до 15 секунд`);
    if (cut.productionSpec && Number(cut.productionSpec.shot?.duration) !== duration)
      throw new Error(`Шот ${index + 1}: длительность изменена — обнови постановку ShotSpec`);
    const timecodes = [...prompt.matchAll(/\[Shot\s+\d+:\s*[\d.]+\s*-\s*[\d.]+\s*s\]/gi)];
    if (timecodes.length > 1) throw new Error(`Шот ${index + 1}: несколько таймкодов в одном клипе — раздели действия на шоты`);
    if (timecodes.length) prompt = prompt.replace(timecodes[0][0], `[Shot 1: 0-${duration}s]`);
    return { id: cut.id, prompt, settings: { ...settingsForShot(settings, cut), duration },
      ...(cut.productionSpec ? { production_spec: structuredClone(cut.productionSpec) } : {}) };
  });
}
export async function submitScenes(requests, send, saved) {
  for (const request of requests) {
    const job = await send(request);
    await saved(request, job);
  }
}
