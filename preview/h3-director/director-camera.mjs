import { sceneCameraPlacement } from './camera-scene.mjs?v=20261004-camera-voice-1';
// Native CameraGuide v2 values supported by app/director.py.
// A native move (dolly, orbit, truck, crane, tilt) becomes camera_state; static shots send none.
// camera_state reaches H3 as text (app/graph.py compile_projected: distance, angles, vertical FOV
// of a 24 mm full-frame gate, lens, focus). The H3CameraGuide / H3OperatorCamera guide video is
// opt-in on the GPU (H3_CAMERA_GUIDE_VIDEO=1); only there ISO, shutter and sensor are used.
// Movements without a native path (pan, handheld, tracking) stay as direction text
// and let the server Director plan the camera.

// Framing → camera distance (m) and look-at height (m) for a standing subject.
const FRAMING = {
  wide: { distance: 7.5, height: 1.0 }, full: { distance: 4.2, height: 1.0 },
  two_shot: { distance: 3.6, height: 1.2 }, medium: { distance: 2.4, height: 1.3 },
  over_shoulder: { distance: 2.2, height: 1.45, orbit: 22 }, point_of_view: { distance: 1.6, height: 1.55 },
  close: { distance: 1.3, height: 1.6 }, extreme_close: { distance: 0.7, height: 1.62 },
  insert: { distance: 0.6, height: 1.1 },
};
const ANGLE = { eye: 0, low: -12, high: 18, overhead: 70 };
export const NATIVE_CAMERA_MOVES = ['dolly', 'orbit', 'truck', 'slider', 'crane', 'pedestal', 'tilt', 'pan', 'zoom'];
// Only a real camera move becomes a trajectory. A static shot sends no optics: on 2026-09-29/30 every
// good render had no camera_state, and since static shots started carrying lens, f-stop and a focus
// plane, faces on medium and wide shots went soft. Framing still reaches H3 as the card's text.

export function cameraFramingDistance(framing) { return FRAMING[framing]?.distance; }

export function cameraStateForPresets(state, presets, frameCount, scenePrompt = '', cameraOverrides = {}) {
  const find = (category) => presets.find((item) => item.id === state?.[category] && item.category === category);
  const motion = find('movement')?.parameters || {};
  const movement = motion.movement;
  const isStatic = !find('movement') || movement === 'static';
  if (isStatic || !NATIVE_CAMERA_MOVES.includes(movement)) return null;
  const focal = Number(find('lens')?.parameters?.focal_length_mm || 50);
  if (!Number.isFinite(focal) || focal < 12 || focal > 135) return null;
  const speed = { slow: 0.65, medium: 1, fast: 1.35 }[cameraOverrides.speed || motion.speed] || 1;
  const focus = find('focus')?.parameters || {};
  const shot = find('shot')?.parameters || {};
  // The f-stop comes from an explicit lens or depth choice. A focus *target* (eyes, subject) no longer
  // implies f/2: that shallow plane blurred every face standing off the focus distance.
  // Shallow depth stays shallow only where it reads: close and medium framings. Full and wide shots
  // keep f/4 and two-shots f/5.6 so a character off the focus plane does not go soft.
  const closeFraming = ['close', 'extreme_close', 'insert', 'medium', 'over_shoulder', 'point_of_view'].includes(shot.framing);
  const aperture = Number(find('lens')?.parameters?.aperture_f || (focus.depth === 'deep' ? 8 :
    shot.framing === 'two_shot' ? 5.6 : focus.depth === 'shallow' && closeFraming ? 2.8 : 4));
  const frame = FRAMING[shot.framing] || { distance: 4, height: 1 };
  const endFrame = FRAMING[cameraOverrides.end_framing] || null;
  // Keep the same framing at any focal length: distance scales with focal / 50 mm.
  const base = Math.max(0.5, Math.min(50, frame.distance * focal / 50));
  const elevation = ANGLE[shot.angle] ?? 0;
  const fov = 2 * Math.atan(24 / (2 * focal)) * 180 / Math.PI;
  const duration = (frameCount - 1) / 24;
  if (!(duration > 0)) return null;
  const focusDistance = Number(focus.focus_distance_m || Math.min(50, Math.max(0.3, base)));
  const start = { t: 0, orbit: frame.orbit || 0, elevation, distance: base, target: [0, frame.height, 0], fov,
    focal_length_mm: focal, aperture_f: aperture, focus_distance_m: focusDistance };
  const end = { ...start, t: Math.floor(duration * 10000) / 10000, target: [...start.target] };
  const clampDistance = (value) => Math.max(0.5, Math.min(50, value));
  const clampFocal = (value) => Math.max(12, Math.min(135, value));
  const speedProgress = { slow: 1, medium: 0.8, fast: 0.55 }[cameraOverrides.speed || motion.speed] || 1;
  const backVector = (orbit, angle) => {
    const a = orbit * Math.PI / 180, e = angle * Math.PI / 180;
    return [Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)];
  };
  const cameraPosition = (key) => backVector(key.orbit, key.elevation)
    .map((component, index) => key.target[index] + component * key.distance);
  const fixedCameraTarget = (position, orbit, angle, distance) => backVector(orbit, angle)
    .map((component, index) => position[index] - component * distance);
  if (movement === 'dolly') {
    const inward = motion.direction === 'forward';
    if (endFrame) {
      start.distance = base;
      end.distance = clampDistance(endFrame.distance * focal / 50);
      end.target[1] = endFrame.height;
    } else {
      start.distance = clampDistance(inward ? base * (1 + 0.38 * speed) : base * (1 - 0.2 * speed));
      end.distance = clampDistance(inward ? base * (1 - 0.2 * speed) : base * (1 + 0.38 * speed));
    }
    if (!focus.focus_distance_m) { start.focus_distance_m = start.distance; end.focus_distance_m = end.distance; }
  } else if (movement === 'zoom') {
    const zoomAmount = { slow: 1.25, medium: 1.6, fast: 2 }[cameraOverrides.speed || motion.speed] || 1.6;
    start.distance = end.distance = base;
    end.focal_length_mm = clampFocal(endFrame
      ? focal * frame.distance / endFrame.distance
      : focal * (motion.direction === 'forward' ? zoomAmount : 1 / zoomAmount));
    end.fov = 2 * Math.atan(24 / (2 * end.focal_length_mm)) * 180 / Math.PI;
  } else if (movement === 'orbit') {
    const arc = String(motion.camera_move || '').startsWith('arc_');
    end.orbit = start.orbit + (motion.direction === 'left' ? -1 : 1) * (arc ? 12 : 25) * speed;
  } else if (movement === 'pan') {
    const position = cameraPosition(start);
    end.orbit = start.orbit + (motion.direction === 'right' ? -1 : 1) * (motion.speed === 'fast' ? 65 : 35) * speed;
    end.target = fixedCameraTarget(position, end.orbit, end.elevation, end.distance);
  } else if (movement === 'truck' || movement === 'slider') {
    const distance = movement === 'slider' ? 0.45 : 1.2;
    end.target = [(motion.direction === 'left' ? -1 : 1) * distance * speed, frame.height, 0];
  }
  else if (movement === 'crane') end.elevation = Math.max(-60, Math.min(60, elevation + (motion.direction === 'down' ? -1 : 1) * 20 * speed));
  else if (movement === 'tilt') {
    const position = cameraPosition(start);
    end.elevation = Math.max(-60, Math.min(60, elevation + (motion.direction === 'down' ? -1 : 1) * 28 * speed));
    end.target = fixedCameraTarget(position, end.orbit, end.elevation, end.distance);
  } else if (movement === 'pedestal') {
    const lift = (motion.direction === 'down' ? -1 : 1) * 0.75 * speed;
    end.target = [start.target[0], start.target[1] + lift, start.target[2]];
  }
  if (endFrame && ['dolly', 'zoom'].includes(movement))
    end.t = Math.floor(duration * speedProgress * 10000) / 10000;
  const explicit = sceneCameraPlacement(scenePrompt);
  if (explicit) {
    const ratio = end.distance / start.distance;
    start.distance = explicit.distance; end.distance = clampDistance(explicit.distance * ratio);
    const shift = explicit.height - start.target[1];
    start.target[1] = explicit.height; end.target[1] += shift;
    if (explicit.orbit !== undefined) { const delta = end.orbit - start.orbit; start.orbit = explicit.orbit; end.orbit = explicit.orbit + delta; }
    if (!focus.focus_distance_m) { start.focus_distance_m = start.distance; end.focus_distance_m = end.distance; }
  }
  if (endFrame) {
    if (movement === 'zoom') {
      end.focal_length_mm = clampFocal(focal * frame.distance / endFrame.distance);
      end.fov = 2 * Math.atan(24 / (2 * end.focal_length_mm)) * 180 / Math.PI;
    } else end.distance = clampDistance(endFrame.distance * focal / 50);
    end.target[1] = endFrame.height;
    if (!focus.focus_distance_m) end.focus_distance_m = end.distance;
  }
  return { version: 2, keys: [start, end] };
}

export function cameraOpticsForPresets(state, presets, cameraState) {
  if (!cameraState) return null;
  const camera = presets.find((item) => item.id === state?.camera && item.category === 'camera');
  const cameraFormats = {
    'camera.alexa35': 'super_35',
    'camera.sony_fx6': 'full_frame',
    'camera.ursa_cine': 'full_frame',
    'camera.sony_burano': 'full_frame',
    'camera.canon_c400': 'full_frame',
  };
  const first = cameraState.keys[0];
  const focus = presets.find((item) => item.id === state?.focus && item.category === 'focus')?.parameters;
  const cameraParams = camera?.parameters || {};
  const rawFormat = cameraParams.sensor_format;
  const sensorFormat = rawFormat === 'super_35' ? 'super_35' : 'full_frame';
  return {
    sensor_format: cameraFormats[camera?.id] || sensorFormat,
    focal_length_mm: first.focal_length_mm,
    aperture_f: first.aperture_f,
    focus_mode: ['subject', 'eyes'].includes(focus?.target) ? 'subject_head' : 'manual',
    focus_distance_m: first.focus_distance_m,
    shutter_angle_deg: cameraParams.shutter_angle_deg || 180,
    iso: cameraParams.iso || 800,
  };
}
