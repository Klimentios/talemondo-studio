// Card illustrations for built-in Director presets: preset id → tile in assets/director/tiles/.
export const directorVisuals = {
  shot: { wide: 0, full: 1, medium: 2, close: 3, extreme: 4, low: 5, high: 6, over_shoulder: 7 },
  lens: { '24': 0, '35': 2, '50': 2, '85': 3, '135': 3, macro: 4, anamorphic: 11 },
  movement: { static: 2, push: 3, pull: 0, track: 12, side: 12, orbit: 7, pan: 0, tilt: 5, handheld: 12, crane: 6 },
  focus: { auto: 2, eyes: 4, object: 4, deep: 0, shallow: 3, rack: 7 },
  lighting: { natural: 0, golden: 8, window: 9, practical: 9, rim: 10, silhouette: 10, lowkey: 14, neon: 11, overcast: 1 },
  style: { realism: 2, documentary: 12, commercial: 8, film: 13, animation3d: 15, animation2d: 15, noir: 14, scifi: 11,
    neon_thriller: 11, intimate_drama: 9, epic_landscape: 0, modern_noir: 14, kinetic_action: 12,
    warm_memory: 8, product_luxury: 4, fantasy_3d: 15, graphic_2d: 15, observational_realism: 1 },
  camera: { modern: 5, film35: 13, film16: 9, documentary: 12, vintage: 8, clean: 6 },
  acting: { human_natural: 2, human_expressive: 4, animal_natural: 15, animal_humanlike: 15 },
};
export function applyDirectorVisual(art, preset) {
  const tile = directorVisuals[preset.category]?.[preset.id.split('.').at(-1)];
  if (tile === undefined) return false;
  art.classList.add('has-atlas');
  art.style.backgroundImage = `url('./assets/director/tiles/${String(tile).padStart(2, '0')}.jpg')`;
  art.style.backgroundSize = 'cover';
  art.style.backgroundPosition = tile === 15 ? 'center top' : 'center';
  return true;
}
