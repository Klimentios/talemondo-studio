// UI polling only: never submits jobs or changes dispatch, pause or reconciliation state.
export function startStudioPolling({ refresh, isActive, document: doc = document,
  setTimer = setTimeout, clearTimer = clearTimeout, activeMs = 3000, idleMs = 30000 }) {
  let timer = null;
  let running = false;
  let stopped = false;
  function clear() { if (timer !== null) clearTimer(timer); timer = null; }
  function reschedule() {
    clear();
    if (!stopped && !running && !doc.hidden) timer = setTimer(tick, isActive() ? activeMs : idleMs);
  }
  async function tick() {
    clear();
    if (stopped || running || doc.hidden) return;
    running = true;
    try { await refresh(); }
    finally { running = false; reschedule(); }
  }
  const visibility = () => { clear(); if (!doc.hidden) return tick(); };
  doc.addEventListener('visibilitychange', visibility);
  reschedule();
  return { reschedule, stop() { stopped = true; clear(); doc.removeEventListener('visibilitychange', visibility); } };
}
