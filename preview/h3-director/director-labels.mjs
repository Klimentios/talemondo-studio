// Human-readable labels for Director parameters (RU/EN).
// Field and option names come from app/director_schema.json via director-config.mjs.
// Values themselves stay English: they are the server contract and go into the H3 prompt.
import { directorField, directorFieldLabel } from './director-config.mjs?v=20261007-camera-options-5';

const PALETTE_TAGS = {
  ru: { cyan: 'циан', magenta: 'маджента', near_black: 'почти чёрный', neutral: 'нейтральный', warm_skin: 'тёплая кожа', earth: 'земляные', sky: 'небесные', charcoal: 'угольный', single_accent: 'один акцент', amber: 'янтарь', cream: 'кремовый', muted_brown: 'приглушённый коричневый', limited: 'ограниченная палитра' },
  en: { near_black: 'near black', warm_skin: 'warm skin', single_accent: 'single accent', muted_brown: 'muted brown' },
};
const UNITS = {
  ru: { focal_length_mm: (v) => `${v} мм`, aperture_f: (v) => `f/${v}`, focus_distance_m: (v) => `${v} м`, shutter_angle_deg: (v) => `${v}°` },
  en: { focal_length_mm: (v) => `${v} mm`, aperture_f: (v) => `f/${v}`, focus_distance_m: (v) => `${v} m`, shutter_angle_deg: (v) => `${v}°` },
};
const lang = (locale) => (String(locale).toLowerCase().startsWith('en') ? 'en' : 'ru');

export function fieldLabel(category, key, locale = 'ru') { return directorFieldLabel(category, key, undefined, locale); }
export const fieldLabelEn = (category, key) => fieldLabel(category, key, 'en');

export function valueLabel(category, key, value, locale = 'ru') {
  const l = lang(locale);
  if (typeof value === 'boolean') return l === 'en' ? (value ? 'Yes' : 'No') : (value ? 'Да' : 'Нет');
  if (Array.isArray(value)) return value.map((item) => PALETTE_TAGS[l][item] || String(item).replaceAll('_', ' ')).join(', ');
  if (typeof value === 'number') return UNITS[l][key]?.(value) ?? String(value);
  return directorFieldLabel(category, key, value, l);
}
export const valueLabelEn = (category, key, value) => valueLabel(category, key, value, 'en');

export function formatParameters(category, parameters = {}, locale = 'ru', limit = Infinity) {
  return Object.entries(parameters || {})
    .filter(([key]) => key !== 'swatches' && directorField(category, key))
    .slice(0, limit)
    .map(([key, value]) => `${fieldLabel(category, key, locale)}: ${valueLabel(category, key, value, locale)}`)
    .join(' · ');
}
export const formatParametersRu = (category, parameters, limit) => formatParameters(category, parameters, 'ru', limit);

export const hasCyrillic = (text) => /[А-Яа-яЁё]/.test(String(text || ''));

// English direction for H3 when the user leaves the description empty.
const CATEGORY_EN = { camera: 'Camera', lens: 'Lens', shot: 'Shot', movement: 'Camera movement', focus: 'Focus', lighting: 'Lighting', color: 'Color grade', style: 'Visual style', acting: 'Acting and voice' };
export function parametersToPrompt(category, parameters = {}) {
  const parts = Object.entries(parameters || {}).filter(([key]) => key !== 'swatches' && directorField(category, key)?.prompt !== false).map(([key, value]) => {
    const v = Array.isArray(value) ? value.join(', ') : typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value).replaceAll('_', ' ');
    return `${key.replaceAll('_', ' ')} ${v}`;
  });
  if (category === 'color' && parameters.swatches?.length) parts.push(`palette ${parameters.swatches.join(' ')}`);
  return parts.length ? `${CATEGORY_EN[category] || category}: ${parts.join(', ')}.` : '';
}
