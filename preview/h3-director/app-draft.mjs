// Draft persistence: debounced saves, cross-tab conflict protection, save-failure warnings.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { saveActiveScene } from './scene-project-model.mjs?v=20261031-render-state-1';
import { errorText } from './api-error.mjs?v=20261008-1';
import { apiReady, hydrating } from './app-bootstrap.mjs?v=20261106-refmatch';
import { showToast } from './app-shared.mjs?v=20261106-refmatch';

let draftTimer;
let draftWrite = Promise.resolve();
let tabId;
let draftChannel;
export let draftStale = false;
let lastDraftWarning = 0;
let draftRevision = 0;

export function setDraftRevision(revision) {
  draftRevision = Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function markDraftStale() {
  if (draftStale) return;
  draftStale = true;
  clearTimeout(draftTimer); draftTimer = null;
  const bar = document.createElement('div');
  bar.className = 'draft-stale-bar';
  bar.setAttribute('role', 'alert');
  bar.innerHTML = '<span>Проект изменён в другой вкладке Studio. Эта вкладка больше не сохраняет, чтобы не затереть изменения.</span><button type="button">Обновить</button>';
  bar.querySelector('button').addEventListener('click', () => location.reload());
  document.body.append(bar);
}

export function installDraftSync() {
  // Two open Studio tabs used to overwrite each other's project with stale state (last writer
  // won). A tab that sees another tab save stops saving and asks to reload instead.
  tabId = globalThis.crypto?.randomUUID?.() || String(Math.random());
  draftChannel = 'BroadcastChannel' in window ? new BroadcastChannel('studio-draft') : null;
  draftChannel?.addEventListener('message', (event) => {
    if (event.data?.type !== 'saved' || event.data.tab === tabId || draftStale) return;
    markDraftStale();
  });
  window.addEventListener('pagehide', flushDraft);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushDraft();
  });
}

export function reportDraftFailure(promise) {
  promise.catch((error) => {
    if (Date.now() - lastDraftWarning < 5000) return;
    lastDraftWarning = Date.now();
    showToast(`Черновик не сохранён: ${error.message}`);
  });
  return promise;
}

export function persistDraft(keepalive = false) {
  if (draftStale) return Promise.resolve();
  saveActiveScene(appState.project);
  draftWrite = draftWrite.catch(() => {}).then(async () => {
    if (draftStale) return;
    const body = JSON.stringify({ project: appState.project, expected_revision: draftRevision });
    const response = await fetch('/api/draft', { method: 'PUT', keepalive,
      headers: { 'Content-Type': 'application/json' }, body });
    if (response.status === 409) { markDraftStale(); throw new Error('проект изменён в другом окне; обновите страницу'); }
    if (!response.ok) throw new Error(errorText(await response.json().catch(() => ({})), response.status));
    draftRevision = (await response.json()).revision;
    draftChannel?.postMessage({ type: 'saved', tab: tabId });
  });
  return draftWrite;
}

function flushDraft() {
  if (!apiReady || hydrating || !draftTimer) return;
  clearTimeout(draftTimer);
  draftTimer = null;
  void persistDraft(true);
}

export function scheduleDraft() {
  if (!apiReady || hydrating) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(async () => {
    draftTimer = null;
    try { await persistDraft(); }
    catch { showToast('Не удалось сохранить черновик'); }
  }, 250);
}
