// «Паспорт франшизы»: pure helpers for franchise-profile.mjs. Server contract: local_studio/franchise_profile.py.
import { DEFAULT_ASPECT } from './aspects.mjs?v=20261106-aspects-1';

export const ROLE_KINDS = [
  ['character', 'Герой'], ['costume', 'Костюм'], ['weapon', 'Оружие'], ['prop', 'Предмет'], ['location', 'Локация'],
  ['fantasy', 'Фантазия'], ['style', 'Стиль'], ['light', 'Свет'], ['color', 'Цвет'], ['voice', 'Голос'], ['music', 'Музыка'],
];

// Reference roles a picture or sound can play in a job (local_studio/store.py REFERENCE_ROLE_PROMPTS).
export const ASSET_ROLES = {
  image: ['Лицо героя', 'Персонаж целиком', 'Костюм', 'Предмет', 'Окружение', 'Стиль', 'Свет', 'Цвет', 'Кадр сцены', 'Композиция'],
  video: ['Движение', 'Движение и камера', 'Актёрская игра', 'Камера', 'Ритм сцены'],
  audio: ['Голос', 'Музыка', 'Звуковой эффект'],
};

export const DEFAULT_ROLE = { character: 'Персонаж целиком', costume: 'Костюм', weapon: 'Предмет', prop: 'Предмет',
  location: 'Окружение', fantasy: 'Окружение', style: 'Стиль', light: 'Свет', color: 'Цвет', voice: 'Голос', music: 'Музыка' };

export function emptyProfile() {
  return { revision: 0, look: { style: '', texture: '', palette: '', language: 'English', aspect_ratio: DEFAULT_ASPECT },
    director_defaults: {}, camera: { lens_min: null, lens_max: null, moves: [], dutch: false, signature: '' },
    edit: { transitions: [], signature_transition: '', min_shot: 1, max_shot: 15, music_asset_id: '', music_volume: 0.35,
      fade_in: 0, fade_out: 0.5, look: '', look_intensity: 0.6, match_color: true }, roles: [], notes: '' };
}

export function newRole(kind = 'character') {
  return { name: '', kind, aliases: [], owner: '', with_owner: kind === 'costume', always: kind === 'style',
    entity_id: '', assets: [], prompt: '' };
}

export const splitList = (text) => String(text || '').split(',').map((part) => part.trim()).filter(Boolean);

/** Body for PUT /profile: drops unnamed roles and empty director defaults, keeps the revision. */
export function profileBody(profile) {
  const defaults = {};
  for (const [category, params] of Object.entries(profile.director_defaults || {})) {
    const kept = Object.fromEntries(Object.entries(params || {}).filter(([, value]) => value !== '' && value != null));
    if (Object.keys(kept).length) defaults[category] = kept;
  }
  const number = (value) => (value === '' || value == null || !Number.isFinite(Number(value)) ? null : Number(value));
  return { ...profile, director_defaults: defaults,
    camera: { ...profile.camera, lens_min: number(profile.camera.lens_min), lens_max: number(profile.camera.lens_max) },
    roles: profile.roles.filter((role) => role.name.trim()).map((role) => ({ ...role, name: role.name.trim(),
      aliases: role.aliases.filter(Boolean), owner: role.owner.trim(), prompt: role.prompt.trim() })) };
}

/** Bible cards that resolve on their own: characters and locations with pictures, not covered by a role. */
export function bibleCards(entities, profile) {
  const covered = new Set(profile.roles.map((role) => role.entity_id).filter(Boolean));
  return (entities || []).filter((card) => ['character', 'location'].includes(card.type) && card.asset_ids?.length &&
    !covered.has(card.id));
}

/** Shot for /resolve-shots from the «try a shot» form. */
export function trialShot(characters, text) {
  return { characters: splitList(characters), title: String(text || '').trim() };
}
