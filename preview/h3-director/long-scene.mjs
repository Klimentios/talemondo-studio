// A long request opens the existing scene planner; generation stays in the shot queue.
export function sceneWindows(cuts, references) {
  const ids = references.filter(ref => ref.kind === 'video' && Number(ref.sourceDuration) > 15
    && ref.role !== 'Продолжение').map(ref => ref.id);
  let start = 0;
  return cuts.map(cut => {
    const next = { ...cut };
    if (ids.length) next.referenceWindow = { start, duration: cut.duration, assetIds: ids };
    start += cut.duration;
    return next;
  });
}

export async function prepareShotReferences(references, window, request) {
  if (!window) return references;
  if (window.assetIds.some(id => !references.some(ref => ref.id === id && ref.kind === 'video')))
    throw new Error('Исходное видео плана удалено. Построй план заново с текущими референсами.');
  const next = [];
  for (const ref of references) {
    if (!window.assetIds.includes(ref.id)) { next.push(ref); continue; }
    const asset = await request(`/api/assets/${encodeURIComponent(ref.id)}/segment`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: window.start, duration: window.duration }),
    });
    next.push({ ...ref, sourceAssetId: ref.id, id: asset.asset_id, name: asset.original_name, sourceDuration: window.duration });
  }
  return next;
}

export async function openLongScene({ seconds, project, prompt, save, navigate }) {
  if (!Number.isInteger(seconds) || seconds <= 15 || seconds > 180) return false;
  project.sceneDirector = { ...project.sceneDirector, idea: prompt,
    targetSeconds: seconds, plan: null, appliedAt: null, autoPrepare: true };
  await save();
  navigate('/?view=scenes#scene-idea');
  return true;
}

export function sourceSceneCuts(plan, prompt, previousCuts, references) {
  const nextId = Math.max(0, ...previousCuts.map(cut => Number(cut.id.split('-')[1]) || 0)) + 1;
  return sceneWindows(plan.shots.map((shot, index) => ({id:`cut-${nextId + index}`, duration:shot.duration,
    prompt: scopedSourcePrompt(prompt, shot.action, shot.duration), startFrame:null, sceneShot:{...shot}})), references);
}

function scopedSourcePrompt(prompt, action, duration) {
  const scope = `This shot covers only the attached ${duration}-second source excerpt. Observed action in this excerpt: ${action}`;
  const section = /^(detailed_description\s*:\s*)/im;
  return section.test(prompt) ? prompt.replace(section, (_, heading) => `${heading}\n${scope}\n`) : `${prompt}\n\n${scope}`;
}

export function validateSourceWindow(cuts, cut) {
  if (!cut.referenceWindow) return;
  const index = cuts.findIndex(item => item.id === cut.id);
  const expected = cuts.slice(0,index).reduce((sum,item) => sum + Number(item.duration),0);
  if (index < 0 || cut.referenceWindow.start !== expected || cut.referenceWindow.duration !== Number(cut.duration))
    throw new Error('Порядок или длительность шотов изменились. Перестрой план, чтобы участки видео соответствовали сцене.');
}
