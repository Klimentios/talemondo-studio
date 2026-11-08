import test from 'node:test';
import assert from 'node:assert/strict';
import { opticsFieldNote, opticsSummary, verticalFovDeg } from './optics-display.mjs';
import { cameraStateForPresets, cameraOpticsForPresets } from './director-camera.mjs';
import { DIRECTOR_PRESETS } from './director-presets.mjs';

test('field of view respects the sensor', () => {
  assert.ok(Math.abs(verticalFovDeg(35) - 37.85) < 0.01);
  assert.ok(verticalFovDeg(35, 'super_35') < verticalFovDeg(35, 'full_frame'));
  assert.ok(Math.abs(verticalFovDeg(35, 'super_35') - 29.9) < 0.1);
});

test('ISO, shutter and sensor are labelled as text only', () => {
  for (const key of ['iso', 'shutter_angle_deg', 'sensor_format']) assert.match(opticsFieldNote('camera', key), /только текст/);
  assert.equal(opticsFieldNote('camera', 'grain'), '');
  assert.equal(opticsFieldNote('lens', 'focal_length_mm'), '');
  assert.match(opticsFieldNote('camera', 'iso', 'en'), /text only/);
});

test('optics summary shows the sensor FOV and what H3 really receives', () => {
  const full = opticsSummary({ focal_length_mm: 35, aperture_f: 2.8, sensor_format: 'full_frame' }, 37.85);
  assert.equal(full, '35 мм · f/2,8 · угол обзора ≈ 37,8° (полный кадр). ISO, затвор и сенсор — только текст режиссуры');
  const s35 = opticsSummary({ focal_length_mm: 35, aperture_f: 2.8, sensor_format: 'super_35' }, 37.85);
  assert.match(s35, /≈ 29,9° \(Super 35\); в H3 уходит полнокадровый 37,9°/);
  assert.equal(opticsSummary(null, 40), '');
});

test('the camera_state sent to H3 is unchanged by the sensor (prompt text stays)', () => {
  const state = { camera: 'camera.alexa35', lens: 'lens.35', shot: 'shot.medium', movement: 'movement.push' };
  const cameraState = cameraStateForPresets(state, DIRECTOR_PRESETS, 125, '');
  assert.ok(cameraState);
  const optics = cameraOpticsForPresets(state, DIRECTOR_PRESETS, cameraState);
  assert.equal(optics.sensor_format, 'super_35');
  assert.ok(Math.abs(cameraState.keys[0].fov - verticalFovDeg(cameraState.keys[0].focal_length_mm)) < 0.01);
});
