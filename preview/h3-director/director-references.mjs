// Turns Director preset photos into REAL generation inputs:
//  • lighting / color / style preset photo → extra <Picture N> reference with role «Свет» / «Цвет» / «Стиль»
//  • color preset photo → Lab statistics for the H3ColorGrade node (color_grade in job settings)
import { labStatsFromPixels } from './photo-analyze.mjs';
import { lightReferenceKey } from './light-reference.mjs';

export const PHOTO_REFERENCE_ROLES = { lighting: 'Свет', color: 'Цвет', style: 'Стиль' };
const MAX_IMAGES = 9;

/** Presets selected in `state` that carry an uploaded photo (custom presets with imageAssetId). */
export function presetPhotoReferences(state = {}, presets = [], existing = [], isAvailable = () => true) {
  const used = new Set(existing.map((ref) => ref.id));
  let images = Math.max(0, ...existing.filter((ref) => ref.kind === 'image').map((ref) => ref.number || 0));
  const imageCount = existing.filter((ref) => ref.kind === 'image').length;
  const extra = [];
  for (const [category, role] of Object.entries(PHOTO_REFERENCE_ROLES)) {
    const preset = presets.find((item) => item.id === state[category] && item.category === category);
    const assetId = preset?.imageAssetId;
    if (!assetId || used.has(assetId) || !isAvailable(assetId)) continue;
    if (imageCount + extra.length >= MAX_IMAGES) break;
    images += 1;
    used.add(assetId);
    extra.push({ id: assetId, kind: 'image', number: images, role, presetCategory: category });
  }
  return extra;
}

/** {target_mean, target_std, strength} for the selected color preset, or null. */
export async function colorGradeForState(state = {}, presets = [], loadPixels) {
  const preset = presets.find((item) => item.id === state.color && item.category === 'color');
  if (!preset?.image) return null;
  // Own photo (custom preset): grade by default. Built-in palettes: only when strength is set explicitly.
  const strength = preset.parameters?.grade_strength ?? (preset.imageAssetId ? 0.5 : 0);
  if (!(strength > 0)) return null;
  const { mean, std } = labStatsFromPixels(await loadPixels(preset.image));
  return { target_mean: mean, target_std: std, strength, preserve_luminance: false };
}

export async function loadImagePixels(url, maxSide = 256) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Фото пресета: HTTP ${response.status}`);
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return context.getImageData(0, 0, canvas.width, canvas.height);
  } finally { bitmap.close(); }
}

/** Lighting preset without its own photo → parameters for a rendered lighting reference, or null. */
export function lightReferenceRequest(state = {}, presets = []) {
  const preset = presets.find((item) => item.id === state.lighting && item.category === 'lighting');
  if (!preset || preset.imageAssetId || preset.parameters?.reference_image === false) return null;
  return { preset, key: lightReferenceKey(preset.parameters || {}) };
}

/** Same preset list with the lighting preset pointing at the rendered reference asset. */
export function withLightReferenceAsset(presets, presetId, assetId) {
  return presets.map((item) => (item.id === presetId ? { ...item, imageAssetId: assetId } : item));
}

/** «Камера» (and style texture) preset → H3FilmLook inputs, or null for a clean digital look. */
export function filmLookForState(state = {}, presets = [], seed = 0) {
  const find = (category) => presets.find((item) => item.id === state[category] && item.category === category)?.parameters || {};
  const camera = find('camera'), style = find('style');
  const byCapture = { film35: [0.3, 1.3], film16: [0.5, 2.0], vintage: [0.45, 1.8], documentary: [0.12, 1.2] }[camera.capture] || [0, 1.2];
  let grain = { subtle: 0.35, fine: 0.2 }[camera.grain] ?? byCapture[0];
  if (!grain) grain = { subtle_grain: 0.2, organic: 0.25, tactile: 0.2 }[style.texture] || 0;
  const contrast = { low: -0.2, soft: -0.12, natural: 0, medium: 0.1, high: 0.22 }[camera.contrast] ?? 0;
  if (!grain && !contrast) return null;
  return { contrast, grain, grain_size: byCapture[1], seed: Number.isInteger(seed) && seed >= 0 ? seed % 2 ** 31 : 0 };
}
