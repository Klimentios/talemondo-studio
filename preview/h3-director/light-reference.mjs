// Lighting preset without a photo → a rendered lighting reference image.
// A neutral grey sphere on a floor in front of a wall, lit exactly as the preset says
// (direction, softness, contrast, key level, fill, temperature, rim, saturation).
// The image goes to H3 as <Picture N> with role «Свет» — real image conditioning
// instead of words. Pure JS: renders the same pixels in the browser and in tests.

const TEMPERATURE = { warm: [1.0, 0.72, 0.45], neutral: [1.0, 0.96, 0.9], cool: [0.66, 0.8, 1.0] };
const SOURCE_TINT = {
  sun: [1.0, 0.78, 0.52], daylight: [1.0, 0.97, 0.92], window: [0.92, 0.95, 1.0], practical: [1.0, 0.68, 0.38],
  backlight: [1.0, 0.85, 0.65], neon: [1.0, 0.25, 0.75], overcast: [0.85, 0.9, 0.98],
};
const DIRECTION = {
  front: [0.35, 0.4, 0.85], side: [0.97, 0.22, 0.12], back: [0.45, 0.35, -0.82], top: [0.08, 0.97, 0.22],
};
const norm = (v) => { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); };
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const mix = (a, b, t) => a.map((x, i) => x + (b[i] - x) * t);

/** Resolve preset parameters into a concrete light rig. */
export function lightRig(p = {}) {
  const source = p.source || 'daylight';
  const direction = p.direction || (source === 'backlight' ? 'back' : source === 'window' ? 'side' : source === 'overcast' ? 'top' : 'front');
  const temp = TEMPERATURE[p.temperature] || null;
  let keyColor = temp ? mix(SOURCE_TINT[source] || TEMPERATURE.neutral, temp, 0.6) : (SOURCE_TINT[source] || TEMPERATURE.neutral);
  const sat = { controlled: 0.55, natural: 1, high: 1.35 }[p.saturation] ?? 1;
  const desat = (c) => { const g = (c[0] + c[1] + c[2]) / 3; return c.map((x) => Math.max(0, g + (x - g) * sat)); };
  keyColor = desat(keyColor);
  const fillColor = desat(source === 'neon' ? [0.2, 0.85, 1.0] : mix(keyColor, [0.75, 0.85, 1.0], 0.35));
  const softness = p.softness || (source === 'overcast' ? 'soft' : source === 'sun' ? 'hard' : 'medium');
  const contrast = p.contrast || 'natural';
  const fillByContrast = { low: 0.6, natural: 0.38, medium: 0.25, high: 0.1 }[contrast];
  const fill = { low: 0.6, medium: 1, high: 1.6 }[p.fill] ?? 1;
  return {
    light: norm(DIRECTION[direction] || DIRECTION.front),
    keyColor, fillColor,
    key: { low: 0.55, medium: 0.85, high: 1.15 }[p.key] ?? 0.9,
    fill: Math.min(0.9, Math.max(source === 'neon' ? 0.35 : 0, fillByContrast * fill)),
    wrap: { soft: 0.55, medium: 0.25, hard: 0.0 }[softness],
    shininess: { soft: 8, medium: 24, hard: 70 }[softness],
    shadowBlur: { soft: 0.22, medium: 0.1, hard: 0.025 }[softness],
    rim: p.rim === true || direction === 'back' ? 1 : 0,
    ambient: { low: 0.2, natural: 0.08, medium: 0.05, high: 0.015 }[contrast] + (source === 'overcast' ? 0.06 : 0),
    gradient: { low: 0.4, natural: 1, medium: 1.35, high: 1.9 }[contrast],
  };
}

export function renderLightReferencePixels(params = {}, width = 768, height = 432) {
  const rig = lightRig(params);
  const data = new Uint8ClampedArray(width * height * 4);
  const cx = width * 0.5, cy = height * 0.47, r = height * 0.27, floorY = height * 0.74;
  const L = rig.light, V = [0, 0, 1], H = norm([L[0] + V[0], L[1] + V[1], L[2] + V[2]]), F = norm([-L[0] * 0.8, 0.15, 0.75]);
  // Cast shadow on the floor: ellipse displaced away from the light, blurred by softness.
  const shadowX = cx - L[0] * r * 1.4, shadowY = floorY + 0.06 * height, shadowRx = r * (1.05 + Math.abs(L[0]) * 0.8), shadowRy = r * 0.22;
  const wallLight = (x) => 0.32 + 0.28 * rig.gradient * L[0] * ((x / width) - 0.5) * 2 * (L[2] > -0.3 ? 1 : -0.5);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    let color;
    const dx = (x - cx) / r, dy = (cy - y) / r, d2 = dx * dx + dy * dy;
    if (d2 <= 1) {
      const n = [dx, dy, Math.sqrt(1 - d2)];
      const diffuse = Math.max(0, (dot(n, L) + rig.wrap) / (1 + rig.wrap));
      const fill = Math.max(0, dot(n, F)) * rig.fill;
      const spec = Math.pow(Math.max(0, dot(n, H)), rig.shininess) * (L[2] > 0 ? 0.35 : 0);
      const rim = rig.rim * Math.pow(1 - n[2], 3) * Math.max(0, 0.4 + n[0] * Math.sign(L[0] || 1) * 0.6 + n[1] * 0.3) * 1.4;
      const albedo = 0.62;
      color = [0, 1, 2].map((c) => albedo * (rig.ambient + diffuse * rig.key * rig.keyColor[c] + fill * rig.fillColor[c] * 0.6)
        + spec * rig.keyColor[c] + rim * rig.keyColor[c] * rig.key);
    } else if (y >= floorY) {
      const sx = (x - shadowX) / shadowRx, sy = (y - shadowY) / shadowRy;
      const dist = Math.sqrt(sx * sx + sy * sy);
      const blur = rig.shadowBlur * 6;
      const shadow = dist < 1 - blur ? 1 : dist > 1 + blur ? 0 : (1 + blur - dist) / (2 * blur);
      const lit = 0.42 * rig.key * (L[1] > -0.2 ? 1 : 0.6) * (1 - shadow * (1 - rig.fill));
      color = [0, 1, 2].map((c) => 0.5 * (rig.ambient + lit * rig.keyColor[c] + rig.fill * 0.25 * rig.fillColor[c]));
    } else {
      const w = Math.max(0.04, wallLight(x)) * rig.key * (L[2] < -0.3 ? 1.25 : 1);
      color = [0, 1, 2].map((c) => 0.42 * (rig.ambient + w * rig.keyColor[c] + rig.fill * 0.15 * rig.fillColor[c]));
    }
    const i = (y * width + x) * 4;
    for (let c = 0; c < 3; c++) data[i + c] = Math.round(255 * Math.pow(Math.min(1, Math.max(0, color[c])), 1 / 2.2));
    data[i + 3] = 255;
  }
  return { data, width, height };
}

/** Stable cache key: the same lighting parameters reuse one uploaded image. */
export function lightReferenceKey(params = {}) {
  const keys = ['source', 'direction', 'softness', 'contrast', 'temperature', 'fill', 'key', 'saturation', 'rim'];
  return 'light-ref-v1:' + keys.map((k) => `${k}=${params[k] ?? ''}`).join(';');
}

export async function lightReferenceFile(params = {}) {
  const { data, width, height } = renderLightReferencePixels(params);
  const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
  canvas.getContext('2d').putImageData(new ImageData(data, width, height), 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  return new File([blob], 'light-reference.png', { type: 'image/png' });
}
