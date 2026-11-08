// A single delegated pointer listener lights the surface under the cursor. No animation on touch or reduced motion.
const enabled = matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)');
let active = null;
let frame = 0;
let x = 0;
let y = 0;
function clear() {
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  active?.classList.remove('glass-pointer');
  active = null;
}
document.addEventListener('pointermove', (event) => {
  if (!enabled.matches || event.pointerType !== 'mouse') return;
  const target = event.target.closest?.('.k-btn, .k-chip, .studio-rail nav a, .studio-rail .rail-settings summary, .kc-float button, .filterbar button, header nav button, .audio-page .mode-picker button');
  if (!target || target.matches(':disabled, [aria-disabled="true"]')) { clear(); return; }
  if (target !== active) { clear(); active = target; active.classList.add('glass-pointer'); }
  const rect = target.getBoundingClientRect();
  x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  y = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  if (!frame) frame = requestAnimationFrame(() => {
    if (active) { active.style.setProperty('--glass-pointer-x', `${x}px`); active.style.setProperty('--glass-pointer-y', `${y}px`); }
    frame = 0;
  });
}, { passive: true });
document.addEventListener('pointerout', (event) => { if (active && !active.contains(event.relatedTarget)) clear(); });
addEventListener('blur', clear);
enabled.addEventListener?.('change', clear);
