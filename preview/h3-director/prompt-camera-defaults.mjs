import { cameraFramingDistance } from './director-camera.mjs?v=20261008-auto-camera-1';

// Resolve omitted dependencies using the same catalogue and geometry as the UI.
// Explicit unsupported movements and authored freeform fields remain errors.
export function resolvePromptCameraDefaults(state, camera, presets, authored = new Set()) {
  const added = [];
  if (camera.speed === undefined && camera.end_framing === undefined) return added;
  if (!state.shot && !authored.has('shot')) {
    const framing = camera.end_framing === 'medium' ? 'full' : 'medium';
    const shot = presets.find(p => p.category === 'shot' && p.parameters?.framing === framing);
    if (shot) { state.shot = shot.id; added.push(shot.name); }
  }
  if (!state.movement && !authored.has('movement')) {
    const shot = presets.find(p => p.id === state.shot);
    const start = cameraFramingDistance(shot?.parameters?.framing);
    const end = cameraFramingDistance(camera.end_framing);
    const direction = end !== undefined && start !== undefined && end > start ? 'backward' : 'forward';
    const movement = presets.find(p => p.category === 'movement' &&
      p.parameters?.movement === 'dolly' && p.parameters?.direction === direction);
    if (movement) { state.movement = movement.id; added.push(movement.name); }
  }
  return added;
}
