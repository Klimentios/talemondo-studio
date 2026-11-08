// Tiny, local direction diagrams. These are previews of camera motion, not
// reference videos or a claim that every motion has native graph support.
import { DIRECTOR_SCHEMA } from './director-config.mjs?v=20261007-camera-options-5';

export const MOTION_VARIANTS = Object.freeze({
  static: { label: 'Статично', arrow: '▣', scene: 'street' },
  push: { label: 'Наезд', arrow: '→', scene: 'street' },
  pull: { label: 'Отъезд', arrow: '←', scene: 'street' },
  track: { label: 'Следование', arrow: '⇢', scene: 'car' },
  side: { label: 'Сбоку', arrow: '↔', scene: 'car' },
  orbit: { label: 'Орбита', arrow: '⟳', scene: 'orbit' },
  pan: { label: 'Панорама', arrow: '→', scene: 'city' },
  tilt: { label: 'Наклон', arrow: '↑', scene: 'tower' },
  handheld: { label: 'Ручная', arrow: '≈', scene: 'street' },
  crane: { label: 'Кран вверх', arrow: '↑', scene: 'tower' },
});
const CAMERA_MOVE_OPTIONS = DIRECTOR_SCHEMA.categories.movement.find((field) => field.key === 'camera_move')?.options || [];
const CAMERA_MOVE_VARIANTS = Object.freeze({
  static: 'static', dolly: 'push', tracking: 'track', truck: 'side', slider: 'side', orbit: 'orbit',
  pan: 'pan', tilt: 'tilt', handheld: 'handheld', crane: 'crane', pedestal: 'crane',
  zoom: 'push',
});

const SCENES = Object.freeze({
  street: '<path class="motion-horizon" d="M0 82h320v98H0z"/><path class="motion-building" d="M0 28h61v76H0zM234 15h86v91h-86z"/><path class="motion-window" d="M10 41h11v15H10zM34 42h11v15H34zM248 29h12v17h-12zM278 29h12v17h-12z"/><path class="motion-road" d="M0 180 118 94h80l122 86z"/><path class="motion-road-mark" d="m160 98-4 82"/><path class="motion-subject" d="M144 89c0-10 7-17 16-17s16 7 16 17l-3 25h-27zM150 114h8l-3 31h-12zm12 0h8l7 31h-12z"/>',
  car: '<path class="motion-horizon" d="M0 85h320v95H0z"/><path class="motion-building" d="M0 27h43v78H0zM57 46h43v59H57zM221 31h44v74h-44zM277 50h43v55h-43z"/><path class="motion-road" d="M0 109h320v71H0z"/><path class="motion-road-mark" d="M15 146h47m36 0h46m35 0h46m34 0h47"/><path class="motion-subject" d="m95 130 13-24h88l22 24 10 2v18H89v-15z"/><circle class="motion-wheel" cx="116" cy="151" r="11"/><circle class="motion-wheel" cx="202" cy="151" r="11"/>',
  city: '<path class="motion-horizon" d="M0 70h320v110H0z"/><path class="motion-building" d="M0 55h45v76H0zM50 20h55v110H50zM110 44h43v86h-43zM170 15h56v115h-56zM236 47h35v83h-35zM277 31h43v99h-43z"/><path class="motion-window" d="M63 35h9v12h-9zm22 0h9v12h-9zM181 30h10v14h-10zm23 0h10v14h-10zM282 46h10v11h-10z"/><path class="motion-road" d="M0 131h320v49H0z"/><path class="motion-road-mark" d="M11 157h55m30 0h55m30 0h55m30 0h44"/>',
  tower: '<path class="motion-horizon" d="M0 91h320v89H0z"/><path class="motion-building" d="M0 76h74v104H0zM118 5h82v175h-82zM244 64h76v116h-76z"/><path class="motion-window" d="M132 23h18v21h-18zm32 0h18v21h-18zM132 58h18v21h-18zm32 0h18v21h-18zM132 93h18v21h-18zm32 0h18v21h-18z"/><path class="motion-road" d="M0 160h320v20H0z"/>',
  orbit: '<ellipse class="motion-orbit-track" cx="160" cy="96" rx="96" ry="55"/><path class="motion-horizon" d="M0 140h320v40H0z"/><path class="motion-subject" d="M143 63h34l12 55-29 16-29-16z"/><path class="motion-window" d="M149 76h22v19h-22z"/>',
});

export function motionVariant(presetId) {
  if (typeof presetId !== 'string' || !presetId.startsWith('movement.')) return null;
  const key = presetId.slice('movement.'.length);
  if (Object.hasOwn(MOTION_VARIANTS, key)) return key;
  const move = CAMERA_MOVE_OPTIONS.find((option) => option.value === key);
  return move ? CAMERA_MOVE_VARIANTS[move.camera_path] || null : null;
}

export function createMotionPreview(presetId) {
  const variant = motionVariant(presetId);
  if (!variant) return null;
  const info = MOTION_VARIANTS[variant];
  const preview = document.createElement('span');
  preview.className = 'motion-preview';
  preview.dataset.motion = variant;
  const key = presetId.slice('movement.'.length);
  const move = CAMERA_MOVE_OPTIONS.find((option) => option.value === key);
  if (move) {
    preview.dataset.move = move.value;
    preview.dataset.category = move.category;
    if (move.direction === 'left' || move.direction === 'backward' || move.direction === 'down')
      preview.dataset.direction = 'reverse';
  }
  preview.setAttribute('aria-hidden', 'true');
  const arrow = preview.dataset.direction === 'reverse'
    ? ({ '→': '←', '←': '→', '↑': '↓', '↓': '↑', '⇢': '⇠', '⟳': '⟲' }[info.arrow] || info.arrow)
    : info.arrow;
  preview.innerHTML = `<svg class="motion-scene" viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" focusable="false"><g class="motion-world">${SCENES[info.scene]}</g><path class="motion-reticle" d="M10 22V10h12m276 0h12v12M10 158v12h12m276 0h12v-12"/></svg><span class="motion-direction">${arrow}</span>`;
  return preview;
}
