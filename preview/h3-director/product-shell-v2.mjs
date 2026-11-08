const params = new URLSearchParams(location.search);
const pathname = location.pathname;
const view = pathname.endsWith('/audio-studio.html')
    ? 'audio'
    : (params.get('view') || 'create');
document.body.dataset.productView = view;

// The side rail highlights the current page itself (studio-rail.mjs).

function updateProjectContext() {
  const studio = window.h3Studio;
  if (!studio?.ready?.()) {
    const meta = document.querySelector('#railProjectMeta');
    if (meta && view !== 'create' && view !== 'scenes' && view !== 'assets') meta.textContent = 'Медиа и шоты проекта';
    if (view === 'create' || view === 'scenes' || view === 'assets') setTimeout(updateProjectContext, 150);
    return;
  }
  const project = studio.project?.() || {};
  let state;
  try { state = studio.sceneState?.(); } catch { /* project snapshot is enough */ }
  const scenes = Array.isArray(state?.scenes) ? state.scenes : (Array.isArray(project.scenes) ? project.scenes : []);
  const shots = scenes.reduce((count, scene) => count + (Array.isArray(scene?.cuts) ? scene.cuts.length : 0), 0);
  const name = document.querySelector('#railProjectName');
  const meta = document.querySelector('#railProjectMeta');
  const plural = (n, one, few, many) => { const a = n % 10; const b = n % 100; return `${n} ${a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many}`; };
  if (name) name.textContent = project.title?.trim() || 'Мой фильм';
  if (meta) meta.textContent = `${plural(scenes.length || 1, 'сцена', 'сцены', 'сцен')} · ${plural(shots, 'шот', 'шота', 'шотов')}`;
}
updateProjectContext();

if (view === 'create') {
  const page = document.querySelector('.page');
  const head = document.querySelector('.project-head');
  if (page && head && !document.querySelector('.product-hero')) {
    const hero = document.createElement('section');
    hero.className = 'product-hero';
    hero.setAttribute('aria-labelledby', 'productHeroTitle');
    hero.innerHTML = `
      <div class="product-showcase" aria-hidden="true">
        <article class="showcase-card showcase-left"><img src="./assets/stock/location-tokyo.jpg" alt=""><span>НОЧНОЙ ГОРОД</span></article>
        <article class="showcase-card showcase-mid-left"><img src="./assets/stock/human-formal-suit.jpg" alt=""><span>КИНОСЦЕНЫ</span></article>
        <article class="showcase-card showcase-main"><img src="./assets/stock/prop-toy-mouse.jpg" alt=""><small>ПРИМЕР РЕФЕРЕНСА</small><strong>ИСТОРИЯ<br>НАЧИНАЕТСЯ ЗДЕСЬ</strong></article>
        <article class="showcase-card showcase-mid-right"><img src="./assets/stock/location-newyork.jpg" alt=""><span>ДИНАМИКА</span></article>
        <article class="showcase-card showcase-right"><img src="./assets/stock/costume-cat-dinosaur.jpg" alt=""><span>ГЕРОИ</span></article>
      </div>
      <div class="product-hero-copy">
        <h1 id="productHeroTitle">Оживи свою историю</h1>
        <p>Создавай кинематографичные ролики с постоянными героями, своими референсами и полным контролем H3.</p>
      </div>`;
    page.insertBefore(hero, head);
    // Idea templates were removed at the user's request; the hero above is hidden by create-studio.css.
  }

  const prompt = document.querySelector('.prompt-block');
  const references = document.querySelector('.reference-block');
  if (prompt && references && !prompt.querySelector('.generator-reference-drawer')) {
    const drawer = document.createElement('details');
    drawer.className = 'generator-reference-drawer';
    drawer.innerHTML = `<summary><span><b>＋</b><strong>Референсы</strong><small>Герой, место, предмет, движение или звук</small></span><em data-reference-total>0 / 9</em></summary>`;
    drawer.append(references);
    prompt.prepend(drawer);
    const list = document.querySelector('#referenceList');
    let openedForReferences = false;
    const update = () => {
      const count = list?.querySelectorAll('.reference-card').length || 0;
      drawer.querySelector('[data-reference-total]').textContent = `${count} / 9`;
      if (count && !openedForReferences) { drawer.open = true; openedForReferences = true; }
    };
    if (list) new MutationObserver(update).observe(list, {childList:true,subtree:true});
    update();
  }
  if (prompt && !prompt.querySelector('.generator-heading')) {
    const heading = document.createElement('div');
    heading.className = 'generator-heading';
    heading.innerHTML = `<div><span class="generator-icon" aria-hidden="true">▶</span><span><strong>Генерация видео</strong><small>MiniMax H3 · локальная очередь</small></span></div><nav class="generator-modes" aria-label="Тип задачи"><span class="is-active" aria-current="page">Видео</span><a href="/?view=scenes">Видео студия</a><a href="/audio-studio.html">Звук</a></nav>`;
    const tools = document.createElement('div');
    tools.className = 'generator-tools';
    tools.innerHTML = `<button type="button" data-generator-jump="references">Референсы</button><button type="button" data-generator-jump="director">Режиссура</button><button type="button" data-generator-jump="advanced">Параметры H3</button>`;
    prompt.prepend(heading, tools);
    tools.addEventListener('click', (event) => {
      const button = event.target.closest('[data-generator-jump]');
      if (!button) return;
      if (button.dataset.generatorJump === 'references') {
        const drawer = document.querySelector('.generator-reference-drawer');
        if (drawer) drawer.open = true;
        requestAnimationFrame(() => drawer?.scrollIntoView({behavior:'smooth',block:'nearest'}));
        return;
      }
      const controls = document.querySelector('#createControls');
      if (controls) controls.open = true;
      const target = button.dataset.generatorJump === 'director' ? document.querySelector('.director-section') : document.querySelector('.settings-section');
      requestAnimationFrame(() => target?.scrollIntoView({behavior:'smooth',block:'center'}));
    });
  }
}
