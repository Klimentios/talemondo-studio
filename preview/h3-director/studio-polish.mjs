// Studio-wide small behaviours (Claude, 2026-10-07).

// Library: load the next page of files when the «Показать ещё» button scrolls into view.
function autoLoadMore(id) {
  const button = document.getElementById(id);
  if (!button || !('IntersectionObserver' in window)) return;
  new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting) && !button.hidden && !button.disabled && button.offsetParent) button.click();
  }, { rootMargin: '300px' }).observe(button);
}
['libraryMore', 'libraryResultsMore'].forEach(autoLoadMore);

// «Текущий проект» in the side rail: the same name and counts on every page, read from the saved
// draft (the audio and edit pages have no live Studio state of their own).
const plural = (n, one, few, many) => {
  const a = n % 10; const b = n % 100;
  return `${n} ${a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many}`;
};
async function railProject() {
  const name = document.getElementById('railProjectName');
  const meta = document.getElementById('railProjectMeta');
  if (!name || !meta) return;
  let project = window.h3Studio?.ready?.() ? window.h3Studio.project() : null;
  if (!project) {
    try { project = (await (await fetch('/api/draft')).json()).project; } catch { return; }
  }
  const scenes = Array.isArray(project?.scenes) && project.scenes.length ? project.scenes : [{ cuts: project?.cuts || [] }];
  const shots = scenes.reduce((sum, scene) => sum + (Array.isArray(scene.cuts) ? scene.cuts.length : 0), 0);
  name.textContent = project?.title?.trim() || 'Мой фильм';
  meta.textContent = `${plural(scenes.length, 'сцена', 'сцены', 'сцен')} · ${plural(shots, 'шот', 'шота', 'шотов')}`;
}
railProject();
setTimeout(railProject, 1500);
['h3:scenes-changed', 'h3:shot-added', 'h3:scene-active-changed'].forEach((name) => window.addEventListener(name, () => setTimeout(railProject, 50)));
