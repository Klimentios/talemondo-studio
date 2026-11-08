// Photo → Director parameters. Runs fully in the browser: pixels never leave the Mac.
// The result is a starting point the user can correct in the form; values are
// rough measurements of the reference photo, not a guarantee of the H3 result.
import { extractPaletteFromPixels } from './palette-extract.mjs';

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const percentile = (sorted, p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];
const mean = (list) => (list.length ? list.reduce((a, b) => a + b, 0) / list.length : 0);

/** Low-level measurements of an RGBA ImageData-like object. */
export function measurePixels({ data, width, height }) {
  if (!data || !(width > 0) || !(height > 0) || data.length < width * height * 4) throw new Error('Не удалось прочитать пиксели фото');
  const n = width * height;
  const Y = new Float32Array(n), R = new Float32Array(n), G = new Float32Array(n), B = new Float32Array(n), S = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const r = data[i * 4] / 255, g = data[i * 4 + 1] / 255, b = data[i * 4 + 2] / 255;
    R[i] = r; G[i] = g; B[i] = b;
    Y[i] = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    S[i] = max > 0.04 ? (max - min) / max : 0;
  }
  const sorted = Float32Array.from(Y).sort();
  const p02 = percentile(sorted, 0.02), p50 = percentile(sorted, 0.5), p98 = percentile(sorted, 0.98);

  // Regions: thirds for direction, center vs. border ring for backlight/focus.
  const region = { left: [], right: [], top: [], bottom: [], center: [], border: [] };
  const lap = new Float32Array(n), grad = new Float32Array(n);
  for (let y = 1; y < height - 1; y++) for (let x = 1; x < width - 1; x++) {
    const i = y * width + x;
    lap[i] = Math.abs(4 * Y[i] - Y[i - 1] - Y[i + 1] - Y[i - width] - Y[i + width]);
    grad[i] = Math.hypot(Y[i + 1] - Y[i - 1], Y[i + width] - Y[i - width]);
  }
  const lapCenter = [], lapBorder = [], flatNoise = [], grads = [];
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = y * width + x, fx = x / width, fy = y / height;
    if (fx < 1 / 3) region.left.push(Y[i]); else if (fx > 2 / 3) region.right.push(Y[i]);
    if (fy < 1 / 3) region.top.push(Y[i]); else if (fy > 2 / 3) region.bottom.push(Y[i]);
    const inner = fx > 0.3 && fx < 0.7 && fy > 0.25 && fy < 0.75;
    const outer = fx < 0.12 || fx > 0.88 || fy < 0.12 || fy > 0.88;
    if (inner) { region.center.push(Y[i]); lapCenter.push(lap[i]); }
    if (outer) { region.border.push(Y[i]); lapBorder.push(lap[i]); }
    if (x > 0 && y > 0 && x < width - 1 && y < height - 1) {
      grads.push(grad[i]);
      if (grad[i] < 0.02) flatNoise.push(lap[i]);
    }
  }
  grads.sort((a, b) => a - b);

  // Warmth of shadows and highlights separately (for split-tone detection).
  const lo = percentile(sorted, 0.2), hi = percentile(sorted, 0.8);
  const warmAll = [], warmShadow = [], warmHigh = [];
  const shadowRGB = [0, 0, 0, 0], highRGB = [0, 0, 0, 0];
  for (let i = 0; i < n; i++) {
    const w = R[i] - B[i];
    if (Y[i] > 0.15) warmAll.push(w);
    if (Y[i] <= lo) { warmShadow.push(w); shadowRGB[0] += R[i]; shadowRGB[1] += G[i]; shadowRGB[2] += B[i]; shadowRGB[3]++; }
    if (Y[i] >= hi) { warmHigh.push(w); highRGB[0] += R[i]; highRGB[1] += G[i]; highRGB[2] += B[i]; highRGB[3]++; }
  }
  const avgRGB = (acc) => acc[3] ? [acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3]] : [0, 0, 0];
  const neonShare = mean(Array.from({ length: n }, (_, i) => (S[i] > 0.55 && (B[i] > R[i] * 1.2 || (R[i] > G[i] * 1.6 && B[i] > G[i] * 1.2)) ? 1 : 0)));

  return {
    mean: mean(Array.from(Y)), p02, p50, p98, range: p98 - p02,
    shadows: mean(Array.from(Y, (v) => (v < 0.12 ? 1 : 0))),
    highlights: mean(Array.from(Y, (v) => (v > 0.88 ? 1 : 0))),
    saturation: mean(Array.from(S)), neonShare,
    warmth: mean(warmAll), warmthShadows: mean(warmShadow), warmthHighlights: mean(warmHigh),
    shadowColor: avgRGB(shadowRGB), highlightColor: avgRGB(highRGB),
    left: mean(region.left), right: mean(region.right), top: mean(region.top), bottom: mean(region.bottom),
    center: mean(region.center), border: mean(region.border),
    edgeHardness: percentile(grads, 0.97) / Math.max(0.1, p98 - p02),
    sharpCenter: mean(lapCenter), sharpBorder: mean(lapBorder),
    noise: mean(flatNoise),
  };
}

const NAMED = [
  ['black', [0.05, 0.05, 0.05]], ['charcoal', [0.2, 0.2, 0.21]], ['navy', [0.08, 0.12, 0.3]], ['teal', [0.1, 0.4, 0.42]],
  ['blue', [0.2, 0.35, 0.7]], ['cyan', [0.4, 0.75, 0.8]], ['green', [0.25, 0.45, 0.25]], ['olive', [0.4, 0.4, 0.2]],
  ['brown', [0.35, 0.22, 0.12]], ['amber', [0.85, 0.55, 0.2]], ['orange', [0.9, 0.45, 0.15]], ['red', [0.7, 0.15, 0.12]],
  ['magenta', [0.75, 0.2, 0.6]], ['purple', [0.4, 0.2, 0.5]], ['peach', [0.95, 0.75, 0.6]], ['cream', [0.95, 0.9, 0.78]],
  ['white', [0.95, 0.95, 0.95]], ['grey', [0.55, 0.55, 0.55]], ['gold', [0.85, 0.7, 0.3]],
];
export function colorName([r, g, b]) {
  let best = NAMED[0][0], dist = Infinity;
  for (const [name, [nr, ng, nb]] of NAMED) {
    const d = (r - nr) ** 2 + (g - ng) ** 2 + (b - nb) ** 2;
    if (d < dist) { dist = d; best = name; }
  }
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const tone = y < 0.12 ? 'deep' : y > 0.8 ? 'bright' : y > 0.55 ? 'soft' : '';
  const warm = r - b > 0.08 ? 'warm' : b - r > 0.08 ? 'cool' : '';
  return [tone, ['black', 'white', 'grey', 'charcoal', 'cream'].includes(best) ? warm : '', best].filter(Boolean).join(' ');
}

const temperatureOf = (w) => (w > 0.06 ? 'warm' : w < -0.03 ? 'cool' : 'neutral');

/** Category → parameters measured from the photo. Unknowable fields are left out (stay «Авто»). */
export function parametersFromMeasurements(category, m) {
  const contrast4 = m.range < 0.45 ? 'low' : m.range < 0.62 ? 'natural' : m.range < 0.8 ? 'medium' : 'high';
  const backlit = m.border - m.center > 0.12;
  if (category === 'lighting') {
    const direction = backlit ? 'back' : Math.abs(m.left - m.right) > 0.1 ? 'side' : m.top - m.bottom > 0.12 ? 'top' : 'front';
    const key = m.mean < 0.25 ? 'low' : m.mean < 0.5 ? 'medium' : 'high';
    const temperature = temperatureOf(m.warmth);
    const softness = m.edgeHardness > 0.35 ? 'hard' : m.edgeHardness > 0.18 ? 'medium' : 'soft';
    const source = m.neonShare > 0.04 && key !== 'high' ? 'neon'
      : backlit ? 'backlight'
        : temperature === 'cool' && contrast4 === 'low' ? 'overcast'
          : key === 'low' && m.p98 > 0.8 && m.highlights < 0.03 ? 'practical'
            : temperature === 'warm' && contrast4 !== 'low' ? 'sun'
              : direction === 'side' ? 'window' : 'daylight';
    return {
      source, direction, softness, contrast: contrast4, temperature, key,
      fill: m.shadows > 0.35 ? 'low' : m.shadows > 0.12 ? 'medium' : 'high',
      saturation: m.saturation < 0.18 ? 'controlled' : m.saturation < 0.45 ? 'natural' : 'high',
      ...(backlit ? { rim: true } : {}),
    };
  }
  if (category === 'color') {
    const split = Math.sign(m.warmthShadows) !== Math.sign(m.warmthHighlights) && Math.abs(m.warmthShadows - m.warmthHighlights) > 0.08;
    return {
      temperature: split ? 'split' : temperatureOf(m.warmth),
      saturation: m.saturation < 0.04 ? 'none' : m.saturation < 0.15 ? 'low' : m.saturation < 0.3 ? 'natural' : m.saturation < 0.45 ? 'moderate' : 'high',
      contrast: m.range < 0.5 ? 'low' : m.range < 0.75 ? 'medium' : 'high',
      shadows: colorName(m.shadowColor), highlights: colorName(m.highlightColor),
    };
  }
  if (category === 'focus') {
    const ratio = m.sharpCenter / Math.max(1e-4, m.sharpBorder);
    return { target: 'subject', depth: ratio > 2.2 ? 'shallow' : ratio > 1.3 ? 'medium' : 'deep' };
  }
  if (category === 'camera') {
    return {
      contrast: contrast4 === 'medium' ? 'medium' : contrast4,
      ...(m.noise > 0.03 ? { grain: m.noise > 0.06 ? 'subtle' : 'fine' } : {}),
      detail: m.sharpCenter > 0.04 ? 'crisp' : 'natural',
    };
  }
  if (category === 'style') {
    return {
      contrast: m.range > 0.8 ? 'high' : m.range > 0.6 ? 'medium' : 'natural',
      texture: m.noise > 0.03 ? 'subtle_grain' : 'clean',
      ...(m.saturation < 0.04 && m.range > 0.75 ? { rendering: 'noir' } : {}),
    };
  }
  return {}; // lens, shot, movement: a still photo is only an illustration
}

export const PHOTO_CATEGORIES = new Set(['lighting', 'color', 'focus', 'camera', 'style']);

export function analyzePixels(category, imageData) {
  const parameters = parametersFromMeasurements(category, measurePixels(imageData));
  if (category === 'color') parameters.swatches = extractPaletteFromPixels(imageData, 8);
  return parameters;
}

export async function analyzePhotoFile(category, file) {
  if (!(file instanceof Blob) || !/^image\/(png|jpeg|webp)$/.test(file.type)) throw new Error('Нужен PNG, JPEG или WebP');
  if (file.size > 30 * 1024 * 1024) throw new Error('Фото больше 30 МБ');
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 256 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(3, Math.round(bitmap.width * scale));
    canvas.height = Math.max(3, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return analyzePixels(category, context.getImageData(0, 0, canvas.width, canvas.height));
  } finally { bitmap.close(); }
}

// CIE Lab statistics (D65), identical math to app/comfy_nodes/h3_color_grade/grade.py.
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const labF = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
export function rgbToLab(r, g, b) {
  const R = toLinear(r), G = toLinear(g), B = toLinear(b);
  const x = (0.4124564 * R + 0.3575761 * G + 0.1804375 * B) / 0.95047;
  const y = 0.2126729 * R + 0.7151522 * G + 0.0721750 * B;
  const z = (0.0193339 * R + 0.1191920 * G + 0.9503041 * B) / 1.08883;
  const fx = labF(x), fy = labF(y), fz = labF(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
export function labStatsFromPixels({ data, width, height }) {
  const n = width * height, sum = [0, 0, 0], sq = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    const lab = rgbToLab(data[i * 4] / 255, data[i * 4 + 1] / 255, data[i * 4 + 2] / 255);
    for (let c = 0; c < 3; c++) { sum[c] += lab[c]; sq[c] += lab[c] * lab[c]; }
  }
  const round = (v) => Math.round(v * 100) / 100;
  const mean = sum.map((s) => s / n);
  const std = sq.map((s, c) => Math.sqrt(Math.max(1e-6, s / n - mean[c] * mean[c])));
  return {
    mean: [round(Math.min(100, Math.max(0, mean[0]))), round(Math.min(127, Math.max(-128, mean[1]))), round(Math.min(127, Math.max(-128, mean[2])))],
    std: [round(Math.min(100, std[0])), round(Math.min(128, std[1])), round(Math.min(128, std[2]))],
  };
}
