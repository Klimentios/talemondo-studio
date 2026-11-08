// Honest optics display. What reaches H3 from the camera card: focal length, aperture and focus,
// as camera_state text (vertical field of view of a 24 mm full-frame gate, see director-camera.mjs).
// ISO, shutter angle and sensor format feed only H3OperatorCamera, which runs only with the
// opt-in guide video (H3_CAMERA_GUIDE_VIDEO=1), so by default they are direction text only.
// Display only: nothing here changes camera_state or the prompt.

export const TEXT_ONLY_CAMERA_FIELDS = new Set(['iso', 'shutter_angle_deg', 'sensor_format']);

// Vertical gate height, mm (3:2 / 4-perf gates, the frame the catalog FOV refers to).
export const SENSOR_HEIGHT_MM = { full_frame: 24, super_35: 18.66, micro_four_thirds: 13 };
const SENSOR_NAMES = {
  ru: { full_frame: 'полный кадр', super_35: 'Super 35', micro_four_thirds: 'Micro 4/3' },
  en: { full_frame: 'full frame', super_35: 'Super 35', micro_four_thirds: 'Micro 4/3' },
};

export function opticsFieldNote(category, key, locale = 'ru') {
  if (category !== 'camera' || !TEXT_ONLY_CAMERA_FIELDS.has(key)) return '';
  return locale === 'en' ? ' · text only, not rendered' : ' · только текст, на рендер не влияет';
}

export function verticalFovDeg(focalMm, sensorFormat = 'full_frame') {
  const height = SENSOR_HEIGHT_MM[sensorFormat] ?? SENSOR_HEIGHT_MM.full_frame;
  return 2 * Math.atan(height / (2 * Number(focalMm))) * 180 / Math.PI;
}

const deg = (value, en) => `${en ? value.toFixed(1) : value.toFixed(1).replace('.', ',')}°`;

/** "35 мм · f/2,8 · угол обзора ≈ 28,7° (Super 35) — в H3 уходит 37,8° …" */
export function opticsSummary(optics, renderFov, locale = 'ru') {
  if (!optics?.focal_length_mm) return '';
  const en = locale === 'en';
  const sensor = SENSOR_HEIGHT_MM[optics.sensor_format] ? optics.sensor_format : 'full_frame';
  const fov = verticalFovDeg(optics.focal_length_mm, sensor);
  const aperture = optics.aperture_f ? ` · f/${en ? optics.aperture_f : String(optics.aperture_f).replace('.', ',')}` : '';
  const head = `${optics.focal_length_mm} ${en ? 'mm' : 'мм'}${aperture} · ${en ? 'vertical FOV' : 'угол обзора'} ≈ ${deg(fov, en)} (${SENSOR_NAMES[en ? 'en' : 'ru'][sensor]})`;
  const sent = Number(renderFov);
  const mismatch = Number.isFinite(sent) && Math.abs(sent - fov) > 0.5
    ? en ? `; H3 receives the full-frame ${deg(sent, en)}` : `; в H3 уходит полнокадровый ${deg(sent, en)}`
    : '';
  const note = en ? '. ISO, shutter and sensor are direction text only' : '. ISO, затвор и сенсор — только текст режиссуры';
  return head + mismatch + note;
}
