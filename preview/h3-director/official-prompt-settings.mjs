export function writtenShotCount(prompt = '') {
  return new Set([...String(prompt).matchAll(/\[Shot\s+(\d+)\]/gi)].map(match => Number(match[1]))).size;
}

export function reconcilePromptCamera(settings, prompt = '') {
  if (writtenShotCount(prompt) <= 1) return { ...settings };
  // Each written shot owns its camera. One trajectory cannot cross editorial cuts.
  const config = settings.director_config ? { ...settings.director_config } : null;
  if (config) { delete config.movement; delete config.shot; delete config.lens; delete config.focus; }
  return { ...settings, camera_state: null, camera_optics: null,
    director_config: config && Object.keys(config).length ? config : null };
}

export function officialPromptSettings(settings, prompt = '') {
  return { ...reconcilePromptCamera(settings, prompt), emotional_direction: false };
}
