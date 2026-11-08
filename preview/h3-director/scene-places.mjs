// Places of the scene's franchise passport («Комплект сцены», local_studio/scene_kit.py): which place each
// «Окружение» picture shows. shot-references.mjs placeOff uses them to keep a picture out of shots set elsewhere.
const cache = new Map();  // franchise id → places, or a pending promise

export function sceneFranchiseId(project) {
  return project?.sceneDirector?.agentScene?.franchise_id
    || project?.scenes?.find((item) => item.id === project.activeSceneId)?.franchiseSource?.franchise_id || '';
}

export function scenePlaces(franchiseId) {
  const value = cache.get(franchiseId);
  return Array.isArray(value) ? value : [];
}

// Resolves to the places (empty without a franchise or when the kit is unreachable: then nothing is left out).
export function loadScenePlaces(franchiseId) {
  if (!franchiseId) return Promise.resolve([]);
  const value = cache.get(franchiseId);
  if (Array.isArray(value)) return Promise.resolve(value);
  if (value) return value;
  const pending = fetch(`/api/franchises/${encodeURIComponent(franchiseId)}/scene-kit`)
    .then((response) => (response.ok ? response.json() : { places: [] }))
    .then((kit) => (Array.isArray(kit.places) ? kit.places : []), () => [])
    .then((places) => { cache.set(franchiseId, places); return places; });
  cache.set(franchiseId, pending);
  return pending;
}
