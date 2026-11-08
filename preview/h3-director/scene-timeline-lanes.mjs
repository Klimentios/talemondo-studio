// Timeline lanes use only scene references and names explicitly present in shot descriptions.
function element(tag, className, text = '') {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

export function renderSceneTimelineLanes(root, cuts, references, selectShot) {
  root.replaceChildren();
  const owners = [...new Set(references
    .filter((ref) => ['Лицо героя', 'Персонаж целиком', 'Костюм'].includes(ref.role) && ref.owner?.trim())
    .map((ref) => ref.owner.trim()))];
  for (const owner of owners) {
    const matches = cuts.map((cut) => `${cut.sceneShot?.title || ''} ${cut.sceneShot?.action || ''} ${cut.sceneShot?.purpose || ''}`
      .toLocaleLowerCase().includes(owner.toLocaleLowerCase()));
    if (!matches.some(Boolean)) continue;
    const row = element('div', 'scene-timeline-lane is-character');
    row.append(element('span', 'scene-timeline-lane-label', owner));
    const segments = element('div', 'scene-timeline-lane-segments');
    cuts.forEach((cut, index) => {
      const segment = element(matches[index] ? 'button' : 'span', `scene-timeline-lane-segment${matches[index] ? ' is-present' : ''}`,
        matches[index] ? owner : '');
      segment.style.flexGrow = String(Math.max(1, Number(cut.duration) || 1));
      if (matches[index]) {
        segment.type = 'button';
        segment.title = `${owner} упомянут в шоте ${index + 1}`;
        segment.addEventListener('click', () => selectShot(index));
      }
      segments.append(segment);
    });
    row.append(segments);
    root.append(row);
  }
  const locations = references.filter((ref) => ref.role === 'Окружение' && ref.kind === 'image');
  if (locations.length) {
    const row = element('div', 'scene-timeline-lane is-location');
    row.append(element('span', 'scene-timeline-lane-label', 'Место'));
    const band = element('button', 'scene-timeline-location', locations.map((ref) => ref.name).join(' · '));
    band.type = 'button';
    band.title = 'Общие референсы окружения сцены';
    band.addEventListener('click', () => document.querySelector('#scene-setup')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
    row.append(band);
    root.append(row);
  }
}
