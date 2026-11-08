// Route the multi-shot workflow to its own Studio workspace without duplicating draft state.
import { russianShotCount } from './scene-project-model.mjs?v=20261031-render-state-1';
const sceneMode = new URLSearchParams(window.location.search).get('view') === 'scenes';
const assetsMode = new URLSearchParams(window.location.search).get('view') === 'assets';
const russianSceneCount = (count) => {
  const value = Math.abs(Math.trunc(Number(count) || 0));
  const lastTwo = value % 100;
  const last = value % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? 'сцен' : last === 1 ? 'сцена' : last >= 2 && last <= 4 ? 'сцены' : 'сцен';
  return `${value} ${noun}`;
};
if (sceneMode) document.body.classList.add('scene-view');

function install() {
  const scenes = document.querySelector('#sceneRail');
  const composer = document.querySelector('#composer');
  if (!scenes) { setTimeout(install, 100); return; }
  // The side rail highlights itself (studio-rail.mjs); only the old top nav is handled here.
  const nav = document.querySelectorAll('.top-nav a');
  for (const link of nav) {
    if (link.getAttribute('href') === '#sceneRail') link.href = '/?view=scenes';
    if (link.getAttribute('href') === '#composer') link.href = '/';
    if (sceneMode && link.getAttribute('href') === '#jobQueue') link.href = '/#jobQueue';
    const isScenes = link.href.endsWith('?view=scenes');
    const isVideo = link.getAttribute('href') === '/';
    const isAssets = link.getAttribute('href') === '/?view=assets';
    link.classList.toggle('active', sceneMode ? isScenes : assetsMode ? isAssets : isVideo);
    link.classList.toggle('rail-active', sceneMode ? isScenes : assetsMode ? isAssets : isVideo);
  }
  const button = document.querySelector('#showScenes');
  button?.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!sceneMode) window.location.assign('/?view=scenes');
  }, true);
  if (!sceneMode) return;

  document.querySelector('.site-header .brand')?.setAttribute('href', '/');

  document.title = 'TaleMondo — Видео студия';
  const head = scenes.querySelector('.scene-rail-head');
  installSceneModes(scenes);
  if (composer) {
    const editorBar = document.createElement('div');
    editorBar.className = 'scene-editor-bar';
    editorBar.innerHTML = '<div><strong>Настройки выбранного шота</strong><span>Описание, режиссура, параметры H3</span></div>';
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = 'Закрыть настройки';
    close.addEventListener('click', () => {
      document.body.classList.remove('scene-editing');
      document.querySelector('#createControls')?.removeAttribute('open');
      scenes.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    editorBar.append(close);
    composer.prepend(editorBar);
    window.addEventListener('h3:scene-edit', () => {
      const controls = document.querySelector('#createControls');
      if (controls) controls.open = true;
      document.body.classList.add('scene-editing');
      requestAnimationFrame(() => composer.scrollIntoView({ block: 'start', behavior: 'smooth' }));
    });
  }
  document.querySelector('#railAddCut')?.setAttribute('aria-label', 'Добавить новый шот');
  if (head) head.classList.add('scene-page-toolbar');
}

// The page-boot cover waits for the mode bar and its first summary: they push the page down 58 px.
let sceneModesShown = () => {};
if (sceneMode) window.studioBoot?.hold(new Promise((resolve) => { sceneModesShown = resolve; }));

function installSceneModes(root) {
  if (root.querySelector('.scene-mode-bar')) return;
  const idea = document.querySelector('#scene-idea');
  if (!idea) { setTimeout(() => installSceneModes(root), 25); return; }
  const modes = document.createElement('nav');
  modes.className = 'scene-mode-bar';
  modes.setAttribute('aria-label', 'Режимы сцены');
  const summary = document.createElement('div');
  summary.className = 'scene-mode-summary';
  summary.setAttribute('aria-live', 'polite');
  const projectName = document.createElement('strong');
  const details = document.createElement('small');
  summary.append(projectName, details);
  const tabs = document.createElement('div');
  tabs.className = 'scene-mode-tabs';
  const entries = [
    ['Шоты', '#scene-board', 'storyboard'], ['Материалы', '#scene-setup', 'materials'],
    ['Сборка', '#scene-assembly', 'export'],
  ];
  for (const [label, href, mode] of entries) {
    const link = document.createElement('a');
    link.textContent = label;
    link.href = href;
    link.dataset.sceneMode = mode;
    tabs.append(link);
  }
  modes.append(summary, tabs);
  idea.after(modes);

  const render = () => {
    const studio = window.h3Studio;
    if (!studio?.ready?.()) { setTimeout(render, 50); return; }
    const project = studio.project?.() || {};
    let state;
    try { state = studio.sceneState?.(); } catch { /* use project snapshot */ }
    const scenes = Array.isArray(state?.scenes) ? state.scenes : (Array.isArray(project.scenes) ? project.scenes : []);
    const active = scenes.find((scene) => scene.id === (state?.activeSceneId || project.activeSceneId)) || scenes[0];
    const cuts = Array.isArray(active?.cuts) ? active.cuts : [];
    projectName.textContent = active?.title || project.title || 'Сцена';
    const filmShots = scenes.flatMap((scene) => Array.isArray(scene.cuts) ? scene.cuts : []);
    const filmSeconds = filmShots.reduce((sum, cut) => sum + (Number(cut.duration) || 0), 0);
    details.textContent = `Фильм: ${russianSceneCount(scenes.length)} · ${russianShotCount(filmShots.length)} · ${filmSeconds} сек по плану`;
    sceneModesShown();
  };
  const setMode = (mode) => {
    document.body.dataset.sceneMode = mode;
    tabs.querySelectorAll('[data-scene-mode]').forEach((link) => {
      const active = link.dataset.sceneMode === mode;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  };
  setMode(({ '#scene-board': 'storyboard', '#scene-setup': 'materials', '#scene-assembly': 'export' })[location.hash] || 'storyboard');
  tabs.addEventListener('click', (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link) return;
    event.preventDefault();
    setMode(link.dataset.sceneMode);
    history.replaceState(null, '', link.hash);
    root.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  window.addEventListener('h3:open-scene-materials', () => {
    setMode('materials');
    history.replaceState(null, '', '#scene-setup');
    root.querySelector('#scene-setup')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  render();
  window.addEventListener('h3:scenes-changed', render);
  window.addEventListener('h3:scene-active-changed', render);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
else install();
