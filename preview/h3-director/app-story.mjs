// Story: adding shots and editing the timeline of shots.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { addCut, removeCut } from './model.mjs?v=20261106-refmatch';
import { activeCut, selectCut } from './app-direction.mjs?v=20261106-refmatch';
import { scheduleDraft } from './app-draft.mjs?v=20261106-refmatch';
import { renderBrief } from './app-references.mjs?v=20261108-clean';
import { $ } from './app-shared.mjs?v=20261106-refmatch';

export function installStoryControls() {
  $('#showScenes').addEventListener('click', () => { $('#sceneRail').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  $('#railAddCut').addEventListener('click', () => addScene());
}

export function addScene(focus = true) {
  const previous = activeCut();
  appState.project = addCut(appState.project);
  const added = appState.project.cuts.at(-1);
  added.duration = previous.duration;
  added.director = $('#newSceneMode')?.value === 'auto' ? {} : { ...previous.director };
  added.directorProfileId = $('#newSceneMode')?.value === 'auto' ? '' : (previous.directorProfileId || '');
  selectCut(added.id);
  if (focus) $('#mainPrompt').focus();
  scheduleDraft();
}

export function installTimelineControls() {
  for (const selector of ['#addCutTop', '#addCutBottom']) $(selector).addEventListener('click', () => addScene());
  $('#timeline').addEventListener('input', (event) => {
    const cut = appState.project.cuts.find((item) => item.id === event.target.closest('[data-id]')?.dataset.id);
    if (!cut) return;
    if (event.target.dataset.action === 'duration' && event.target.value !== '') {
      cut.duration = Math.min(15, Math.max(4, Number(event.target.value) || 5));
      if (cut.id === activeCut().id) $('#duration').value = cut.duration;
    }
    renderBrief();
    scheduleDraft();
  });
  $('#timeline').addEventListener('change', (event) => {
    if (event.target.dataset.action === 'duration') {
      const cut = appState.project.cuts.find((item) => item.id === event.target.closest('[data-id]')?.dataset.id);
      if (cut) event.target.value = cut.duration;
    }
  });
  $('#timeline').addEventListener('click', (event) => {
    const action = event.target.dataset.action;
    const id = event.target.closest('[data-id]')?.dataset.id;
    if (action === 'select') { selectCut(id); return; }
    if (action !== 'remove') return;
    if (!window.confirm('Удалить этот кадр из проекта? Готовые ролики в очереди сохранятся.')) return;
    appState.project = removeCut(appState.project, id);
    if (appState.selectedCutId === id) appState.selectedCutId = appState.project.cuts[0].id;
    selectCut(appState.selectedCutId);
    scheduleDraft();
  });
}
