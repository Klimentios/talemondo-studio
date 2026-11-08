// Present the existing shot controls in one focused inspector without changing their actions.
let activeTab = 'shot';

function node(tag, className = '', text = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function referencePanel(refs, referenceUrl) {
  const wrap = node('div', 'scene-inspector-references');
  const intro = node('p', '', 'Материалы сцены доступны каждому шоту. Стартовый кадр выбирается отдельно.');
  wrap.append(intro);
  const list = node('div', 'scene-inspector-reference-list');
  for (const ref of refs) {
    const item = node('div', 'scene-inspector-reference');
    if (ref.kind === 'image') {
      const image = node('img');
      image.src = referenceUrl(ref.id);
      image.alt = '';
      image.loading = 'lazy';
      item.append(image);
    } else item.append(node('span', 'scene-inspector-reference-icon', ref.kind === 'audio' ? '♫' : '▶'));
    const copy = node('span');
    copy.append(node('strong', '', ref.owner?.trim() || ref.name || 'Материал'), node('small', '', ref.role || 'Референс'));
    item.append(copy);
    list.append(item);
  }
  if (!refs.length) list.append(node('p', 'scene-inspector-empty', 'Добавьте материалы сцены слева.'));
  wrap.append(list);
  const showAssets = node('button', 'scene-inspector-link', 'Открыть материалы сцены');
  showAssets.type = 'button';
  showAssets.addEventListener('click', () => window.dispatchEvent(new CustomEvent('h3:open-scene-materials')));
  wrap.append(showAssets);
  return wrap;
}

export function arrangeShotInspector(card, refs, referenceUrl) {
  if (!card) return card;
  const header = card.querySelector(':scope > header');
  const progress = card.querySelector(':scope > progress');
  const media = card.querySelector(':scope > .storyboard-media');
  const captions = card.querySelector(':scope > .storyboard-captions');
  const slot = card.querySelector(':scope > .storyboard-slot');
  const takes = card.querySelector(':scope > .storyboard-takes');
  const prompt = card.querySelector(':scope > .storyboard-prompt-label');
  const camera = card.querySelector(':scope > .storyboard-shot-camera');
  const row = card.querySelector(':scope > .storyboard-row');
  const qc = card.querySelector(':scope > .storyboard-qc');
  const qcActions = card.querySelector(':scope > .storyboard-qc-actions');
  const notes = [...card.children].filter((child) => child.classList.contains('storyboard-note'));
  const staging = card.querySelector(':scope > .storyboard-staging');
  const actions = card.querySelector(':scope > .storyboard-actions');
  const buttons = [...(actions?.children || [])];
  const findAction = (action) => buttons.find((button) => button.dataset.action === action);
  const tabs = [
    ['shot', 'Шот'], ['refs', 'Референсы'], ['camera', 'Камера'], ['motion', 'Движение'], ['ai', 'AI'],
  ];
  const tablist = node('div', 'scene-inspector-tabs');
  tablist.setAttribute('role', 'tablist');
  tablist.setAttribute('aria-label', 'Настройки выбранного шота');
  const panels = new Map();
  const tabButtons = new Map();
  for (const [key, label] of tabs) {
    const button = node('button', '', label);
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.id = `scene-tab-${key}`;
    button.setAttribute('aria-controls', `scene-panel-${key}`);
    tablist.append(button);
    tabButtons.set(key, button);
    const panel = node('section', 'scene-inspector-panel');
    panel.id = `scene-panel-${key}`;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', button.id);
    panels.set(key, panel);
    button.addEventListener('click', () => selectTab(key));
  }
  function selectTab(key) {
    activeTab = key;
    for (const [name, panel] of panels) {
      const selected = name === key;
      panel.hidden = !selected;
      tabButtons.get(name).setAttribute('aria-selected', String(selected));
      tabButtons.get(name).tabIndex = selected ? 0 : -1;
    }
  }
  tablist.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const current = tabs.findIndex(([key]) => key === activeTab);
    const next = (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    selectTab(tabs[next][0]);
    tabButtons.get(tabs[next][0]).focus();
  });

  panels.get('shot').append(...[prompt, row, staging, takes, ...notes].filter(Boolean));
  panels.get('refs').append(...[slot, referencePanel(refs, referenceUrl)].filter(Boolean));
  const cameraFields = [...(camera?.children || [])];
  const framing = node('div', 'scene-inspector-fields');
  framing.append(...cameraFields.slice(0, 2));
  panels.get('camera').append(framing);
  const motion = node('div', 'scene-inspector-fields');
  motion.append(...cameraFields.slice(2));
  panels.get('motion').append(motion);
  panels.get('ai').append(qc || node('p', 'scene-inspector-empty', 'AI-проверка доступна после готового дубля.'), ...(qcActions ? [qcActions] : []));
  if (findAction('qc')) panels.get('ai').append(findAction('qc'));

  const footer = node('div', 'scene-inspector-footer');
  const run = findAction('run');
  const rerun = findAction('rerun');
  if (run) footer.append(run);
  if (rerun && !rerun.disabled) footer.append(rerun);
  const more = node('details', 'scene-inspector-more');
  more.append(node('summary', '', 'Ещё'), ...['edit', 'up', 'down', 'remove'].map(findAction).filter(Boolean));
  footer.append(more);
  card.classList.add('scene-inspector-card');
  card.replaceChildren(...[header, progress, media, captions, tablist, ...panels.values(), footer].filter(Boolean));
  selectTab(activeTab);
  return card;
}
