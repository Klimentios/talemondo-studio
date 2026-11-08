// Local palette extraction. Pixels stay in the browser; the selected image is
// stored separately through Studio's existing Mac asset endpoint.
const hex = (value) => Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, '0');
const distance = (a, b) => Math.hypot(a.r - b.r, a.g - b.g, a.b - b.b);

export function extractPaletteFromPixels(imageData, count = 8) {
  const { data, width, height } = imageData || {};
  if (!data || !Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || data.length < width * height * 4)
    throw new Error('Не удалось прочитать пиксели изображения');
  if (!Number.isInteger(count) || count < 1 || count > 12) throw new Error('Неверное число цветов');
  const bins = new Map();
  const stride = Math.max(1, Math.floor(Math.sqrt(width * height / 16000)));
  for (let y = 0; y < height; y += stride) for (let x = 0; x < width; x += stride) {
    const offset = (y * width + x) * 4;
    if (data[offset + 3] < 160) continue;
    const r = data[offset], g = data[offset + 1], b = data[offset + 2];
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    const bin = bins.get(key) || { r: 0, g: 0, b: 0, n: 0 };
    bin.r += r; bin.g += g; bin.b += b; bin.n++;
    bins.set(key, bin);
  }
  if (!bins.size) throw new Error('В изображении нет непрозрачных цветов');
  const candidates = [...bins.values()].sort((a, b) => b.n - a.n).slice(0, 192)
    .map((bin) => ({ r: bin.r / bin.n, g: bin.g / bin.n, b: bin.b / bin.n, n: bin.n }));
  const chosen = [];
  while (chosen.length < Math.min(count, candidates.length)) {
    let best = null, bestScore = -Infinity;
    for (const color of candidates) {
      if (chosen.includes(color)) continue;
      const separation = chosen.length ? Math.min(...chosen.map((selected) => distance(color, selected))) : 128;
      const score = Math.sqrt(color.n) * (0.25 + Math.min(separation, 180) / 180);
      if (score > bestScore) { best = color; bestScore = score; }
    }
    if (!best || (chosen.length && Math.min(...chosen.map((selected) => distance(best, selected))) < 18)) break;
    chosen.push(best);
  }
  return chosen.sort((a, b) => (a.r * .2126 + a.g * .7152 + a.b * .0722) -
                               (b.r * .2126 + b.g * .7152 + b.b * .0722))
    .map(({ r, g, b }) => `#${hex(r)}${hex(g)}${hex(b)}`);
}

export async function extractPaletteFromImage(file) {
  if (!(file instanceof Blob) || !/^image\/(png|jpeg|webp)$/.test(file.type))
    throw new Error('Для палитры выбери PNG, JPEG или WebP');
  if (file.size > 30 * 1024 * 1024) throw new Error('Изображение больше 30 МБ');
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 192 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('Браузер не смог прочитать изображение');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return extractPaletteFromPixels(context.getImageData(0, 0, canvas.width, canvas.height));
  } finally { bitmap.close(); }
}
