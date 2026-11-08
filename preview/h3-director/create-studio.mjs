// «Создать» as one compact workspace: the generator on the left, results on the right, and the
// professional controls in two modal windows («Режиссура», «Параметры H3») instead of a long page.
// The existing sections are moved into the windows — the same elements with all their handlers,
// nothing is copied or re-implemented.
const createView = !new URLSearchParams(location.search).get('view') && !/\/audio-studio\.html$/.test(location.pathname);

function dialog(id, title, subtitle) {
  const node = document.createElement('dialog');
  node.className = 'cs-dialog';
  node.id = id;
  node.innerHTML = `<header class="cs-dialog-head"><div><h2>${title}</h2><p>${subtitle}</p></div>
    <button type="button" class="cs-close" aria-label="Закрыть">×</button></header><div class="cs-dialog-body"></div>
    <footer class="cs-dialog-foot"><span>Настройки применяются к текущему кадру и сохраняются в проекте.</span><button type="button" class="cs-done">Готово</button></footer>`;
  node.addEventListener('click', (event) => {
    if (event.target === node || event.target.closest('.cs-close, .cs-done')) node.close();
  });
  document.body.append(node);
  return node;
}

function move(selector, target) {
  const node = typeof selector === 'string' ? document.querySelector(selector) : selector;
  if (node && node.parentNode !== target) target.append(node);
  return node;
}

function open(dialogNode) {
  if (!dialogNode.open) dialogNode.showModal();
  dialogNode.querySelector('.cs-dialog-body').scrollTop = 0;
}

// The page-boot cover waits for this rebuild: it moves the reference block and the queue across the page.
let installed = () => {};
if (createView) window.studioBoot?.hold(new Promise((resolve) => { installed = resolve; }));

function install() {
  const controls = document.querySelector('#createControls .controls-content');
  const tools = document.querySelector('.generator-tools');
  if (!window.h3Studio?.ready?.() || !controls || !tools || !document.querySelector('.official-controls')) { setTimeout(install, 50); return; }
  if (document.body.classList.contains('cs-ready')) return installed();

  const director = dialog('csDirector', 'Режиссура кадра', 'Камера, объектив, движение, свет, цвет, стиль, игра героя и голос');
  const params = dialog('csParams', 'Параметры H3', 'Качество, шаги, звук, эффекты камеры, ключевые кадры и служебные настройки');
  const directorBody = director.querySelector('.cs-dialog-body');
  const paramsBody = params.querySelector('.cs-dialog-body');
  move('.director-section', directorBody);
  move('#acting', directorBody);
  move('.settings-section', paramsBody);
  move('.official-controls', paramsBody);
  move('#advanced', paramsBody);
  move('.prompt-direction-help', paramsBody);
  // «Все кадры» / «С последнего кадра» belong to multi-shot generation — keep them with the parameters.
  const shots = document.createElement('section');
  shots.className = 'cs-shots';
  shots.innerHTML = '<h3>Несколько кадров</h3>';
  [...controls.querySelectorAll('.scene-generation-actions, .scenes-status, #scenesColorOff')].forEach((node) => shots.append(node));
  if (shots.children.length > 1) paramsBody.append(shots);
  document.body.classList.add('cs-on', 'cs-ready');
  document.body.classList.remove('cs-loading');

  // The generator buttons open the windows instead of scrolling to a hidden part of the page.
  tools.addEventListener('click', (event) => {
    const button = event.target.closest('[data-generator-jump]');
    if (!button) return;
    const jump = button.dataset.generatorJump;
    if (jump === 'director') { event.stopImmediatePropagation(); open(director); }
    if (jump === 'advanced') { event.stopImmediatePropagation(); open(params); }
  }, true);
  document.getElementById('moreSettings')?.addEventListener('click', (event) => {
    event.stopImmediatePropagation();
    open(params);
    requestAnimationFrame(() => params.querySelector('.output-details')?.setAttribute('open', ''));
  }, true);
  // One place for the two windows: «Режиссура» and «⚙ Параметры H3» sit in the parameter row next to
  // length / format / quality / takes; the separate tool row above the references is hidden (CSS).
  const gear = document.getElementById('moreSettings');
  if (gear && !document.getElementById('csDirectorButton')) {
    const directorButton = document.createElement('button');
    directorButton.type = 'button';
    directorButton.id = 'csDirectorButton';
    directorButton.className = 'cs-row-button';
    directorButton.innerHTML = '<span aria-hidden="true">🎬</span> Режиссура';
    directorButton.addEventListener('click', () => open(director));
    gear.before(directorButton);
    gear.classList.add('cs-row-button');
    gear.insertAdjacentHTML('beforeend', '<span class="cs-gear-label">Параметры H3</span>');
  }
  window.h3CreateStudio = { openDirector: () => open(director), openParams: () => open(params) };
  installed();
}

if (createView) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
}
