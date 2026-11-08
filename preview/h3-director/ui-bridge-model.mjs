/** Compare an agent's scene request with the canvas state after applying it. */
export function verifyAppliedScene(scene, state) {
  const differences = [];
  const active = state.scenes.find((item) => item.id === state.active_scene);
  if (scene.title && active?.title !== scene.title) differences.push('название сцены');
  if (state.shots.length !== scene.shots.length) differences.push('число шотов');
  for (const [index, shot] of scene.shots.entries()) {
    const actual = state.shots[index];
    if (!actual) break;
    for (const key of ['title', 'duration', 'framing', 'lens_mm', 'camera_motion']) {
      if (shot[key] != null && actual[key] !== shot[key]) differences.push(`шот ${index + 1}: ${key}`);
    }
    if (actual.prompt !== String(shot.prompt || '').trim().slice(0, 240)) differences.push(`шот ${index + 1}: prompt`);
  }
  return { ok: differences.length === 0, differences };
}
