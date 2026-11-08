// Curated starting points. Each choice is editable per shot after applying it.
const profileEnglish = {
  'neon-thriller': 'Neon thriller', 'intimate-drama': 'Intimate drama',
  'epic-landscape': 'Epic landscape', 'modern-noir': 'Modern noir',
  'kinetic-action': 'Kinetic action', 'warm-memory': 'Warm memory',
  'product-luxury': 'Luxury commercial', 'fantasy-3d': 'Fantasy 3D',
  'graphic-2d': 'Graphic 2D', 'observational': 'Observational cinema',
};
const profile = (id, name, mode, camera, lens, shot, movement, focus, lighting, color, style) =>
  ({ id, name, titleEn: profileEnglish[id], mode,
    director: { camera, lens, shot, movement, focus, lighting, color, style } });

export const DIRECTOR_PROFILES = [
  profile('neon-thriller', 'Неоновый триллер', 'realistic', 'camera.sony_burano', 'lens.35', 'shot.medium', 'movement.side', 'focus.auto', 'lighting.neon', 'color.teal_orange', 'style.neon_thriller'),
  profile('intimate-drama', 'Камерная драма', 'realistic', 'camera.alexa35', 'lens.85', 'shot.close', 'movement.push', 'focus.eyes', 'lighting.window', 'color.neutral', 'style.intimate_drama'),
  profile('epic-landscape', 'Эпический пейзаж', 'realistic', 'camera.ursa_cine', 'lens.24', 'shot.wide', 'movement.crane', 'focus.deep', 'lighting.golden', 'color.warm', 'style.epic_landscape'),
  profile('modern-noir', 'Современный нуар', 'realistic', 'camera.alexa_mini_lf', 'lens.50', 'shot.medium', 'movement.static', 'focus.shallow', 'lighting.lowkey', 'color.monochrome', 'style.modern_noir'),
  profile('kinetic-action', 'Динамичный экшен', 'realistic', 'camera.sony_fx6', 'lens.35', 'shot.full', 'movement.track', 'focus.auto', 'lighting.practical', 'color.cool', 'style.kinetic_action'),
  profile('warm-memory', 'Тёплое воспоминание', 'realistic', 'camera.panavision_dxl2', 'lens.50', 'shot.medium', 'movement.pull', 'focus.shallow', 'lighting.golden', 'color.pastel', 'style.warm_memory'),
  profile('product-luxury', 'Премиальная реклама', 'realistic', 'camera.alexa35', 'lens.macro', 'shot.extreme', 'movement.push', 'focus.object', 'lighting.rim', 'color.neutral', 'style.product_luxury'),
  profile('fantasy-3d', 'Фэнтези 3D', 'animation3d', 'camera.ursa_cine', 'lens.35', 'shot.full', 'movement.orbit', 'focus.auto', 'lighting.golden', 'color.vivid', 'style.fantasy_3d'),
  profile('graphic-2d', 'Графическая 2D', 'animation2d', 'camera.canon_c400', 'lens.50', 'shot.medium', 'movement.pan', 'focus.deep', 'lighting.natural', 'color.vivid', 'style.graphic_2d'),
  profile('observational', 'Наблюдательное кино', 'realistic', 'camera.documentary_rig', 'lens.35', 'shot.medium', 'movement.handheld', 'focus.deep', 'lighting.overcast', 'color.desaturated', 'style.observational_realism'),
];

export function displayDirectorProfile(profile, locale = 'ru') {
  if (!profile) return '';
  return String(locale).toLowerCase().startsWith('en') ? (profile.titleEn || profile.name) : profile.name;
}
