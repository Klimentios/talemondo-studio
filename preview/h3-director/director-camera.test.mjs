import test from 'node:test';
import assert from 'node:assert/strict';
import { cameraStateForPresets, cameraOpticsForPresets } from './director-camera.mjs';
import { DIRECTOR_PRESETS } from './director-presets.mjs';

test('explicit scene placement takes precedence over generic two-shot dimensions', () => {
  const state = { movement: 'movement.push', lens: 'lens.50', focus: 'focus.eyes', shot: 'shot.two_shot' };
  const guide = cameraStateForPresets(state, DIRECTOR_PRESETS, 243,
    'Camera approximately 30 cm above the floor and 2 m from the cats, positioned slightly front-left of their interaction axis.');
  assert.equal(guide.keys[0].distance, 2);
  assert.equal(guide.keys[0].target[1], 0.3);
  assert.equal(guide.keys[0].aperture_f, 5.6);
  assert.ok(guide.keys[0].orbit < 0);
});

test('dolly and optical selections become valid native CameraGuide v2 keys', () => {
  const guide = cameraStateForPresets({ movement: 'movement.push', lens: 'lens.35', focus: 'focus.eyes', shot: 'shot.low' }, DIRECTOR_PRESETS, 362);
  assert.equal(guide.version, 2);
  assert.equal(guide.keys[0].focal_length_mm, 35);
  assert.equal(guide.keys[0].aperture_f, 2.8); // medium framing + eyes: shallow, but not f/2
  assert.equal(guide.keys[0].elevation, -12);
  assert.ok(guide.keys[0].distance > guide.keys[1].distance);
  assert.ok(guide.keys[1].t <= 361 / 24);
  const optics = cameraOpticsForPresets({ camera: 'camera.alexa35', focus: 'focus.eyes' }, DIRECTOR_PRESETS, guide);
  assert.equal(optics.sensor_format, 'super_35');
  assert.equal(optics.focal_length_mm, 35);
  assert.equal(optics.focus_mode, 'subject_head');
  for (const camera of ['camera.sony_burano', 'camera.canon_c400']) {
    assert.equal(cameraOpticsForPresets({ camera }, DIRECTOR_PRESETS, guide).sensor_format, 'full_frame');
  }
});

test('unsupported movement leaves camera guide to the server director', () => {
  assert.equal(cameraStateForPresets({ movement: 'movement.handheld' }, DIRECTOR_PRESETS, 362), null);
});

test('custom movement and optical controls change native guide and node inputs', () => {
  const custom = [
    { id: 'custom.11111111-1111-4111-8111-111111111111', category: 'movement', parameters: { movement: 'orbit', direction: 'left', speed: 'fast' } },
    { id: 'custom.22222222-2222-4222-8222-222222222222', category: 'lens', parameters: { focal_length_mm: 85, aperture_f: 4 } },
    { id: 'custom.33333333-3333-4333-8333-333333333333', category: 'camera', parameters: { sensor_format: 'super_35', iso: 1600, shutter_angle_deg: 90 } },
    { id: 'custom.44444444-4444-4444-8444-444444444444', category: 'focus', parameters: { target: 'subject', focus_distance_m: 3 } },
  ];
  const state = Object.fromEntries(custom.map((preset) => [preset.category, preset.id]));
  const guide = cameraStateForPresets(state, [...DIRECTOR_PRESETS, ...custom], 362);
  assert.ok(guide.keys[1].orbit < 0);
  assert.equal(guide.keys[0].focal_length_mm, 85);
  assert.equal(guide.keys[0].aperture_f, 4);
  assert.equal(guide.keys[1].focus_distance_m, 3);
  const optics = cameraOpticsForPresets(state, [...DIRECTOR_PRESETS, ...custom], guide);
  assert.equal(optics.sensor_format, 'super_35');
  assert.equal(optics.iso, 1600);
  assert.equal(optics.shutter_angle_deg, 90);
});

test('a static shot sends no trajectory or optics; framing stays card text', () => {
  assert.equal(cameraStateForPresets({ shot: 'shot.close', lens: 'lens.85', camera: 'camera.alexa35' }, DIRECTOR_PRESETS, 362), null);
  assert.equal(cameraStateForPresets({ shot: 'shot.full', focus: 'focus.eyes', movement: 'movement.static' }, DIRECTOR_PRESETS, 362), null);
  assert.equal(cameraOpticsForPresets({ shot: 'shot.close', camera: 'camera.alexa35' }, DIRECTOR_PRESETS, null), null);
});

test('lens or camera alone does not impose a framing; tilt is native', () => {
  assert.equal(cameraStateForPresets({ lens: 'lens.35', camera: 'camera.alexa35' }, DIRECTOR_PRESETS, 362), null);
  assert.equal(cameraStateForPresets({ movement: 'movement.static' }, DIRECTOR_PRESETS, 362), null);
  const tilt = cameraStateForPresets({ movement: 'movement.tilt' }, DIRECTOR_PRESETS, 362);
  assert.ok(tilt.keys[1].elevation > tilt.keys[0].elevation);
  assert.ok(tilt.keys[1].target[1] < tilt.keys[0].target[1]);
  const cameraPosition = (key) => {
    const orbit = key.orbit * Math.PI / 180, elevation = key.elevation * Math.PI / 180;
    return [Math.sin(orbit) * Math.cos(elevation), Math.sin(elevation), Math.cos(orbit) * Math.cos(elevation)]
      .map((component, index) => key.target[index] + key.distance * component);
  };
  const before = cameraPosition(tilt.keys[0]), after = cameraPosition(tilt.keys[1]);
  before.forEach((value, index) => assert.ok(Math.abs(value - after[index]) < 1e-9));
});

test('shallow focus is f/2.8 only on close framings; wide and full shots keep f/4', () => {
  const close = cameraStateForPresets({ movement: 'movement.push', focus: 'focus.eyes', shot: 'shot.close' }, DIRECTOR_PRESETS, 243);
  const full = cameraStateForPresets({ movement: 'movement.push', focus: 'focus.eyes', shot: 'shot.full' }, DIRECTOR_PRESETS, 243);
  const deep = cameraStateForPresets({ movement: 'movement.push', focus: 'focus.deep', shot: 'shot.close' }, DIRECTOR_PRESETS, 243);
  assert.equal(close.keys[0].aperture_f, 2.8);
  assert.equal(full.keys[0].aperture_f, 4);
  assert.equal(deep.keys[0].aperture_f, 8);
});
