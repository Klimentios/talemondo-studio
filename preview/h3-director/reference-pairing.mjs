// Explicit performer-to-hero mapping for a movement video with multiple heroes.
// Only a mapping written in the user's prompt or confirmed in the dialog is submitted.
const MOTION_ROLES = new Set(['Движение', 'Движение и камера', 'Исходное видео']);
const CYRILLIC = Object.fromEntries([..."абвгдеёжзийклмнопрстуфхцчшщыьъэюя"].map((letter, index) =>
  [letter, ['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','sh','shch','y','','','e','yu','ya'][index]]));

function nameKey(value) {
  return [...String(value || '').toLocaleLowerCase('ru')].map(char => CYRILLIC[char] ?? char)
    .join('').replace(/[^a-z0-9]/g, '');
}

export function resolveHeroName(value, heroes) {
  for (const strip of [false, true]) {
    const key = text => nameKey(strip ? String(text || '').trim().replace(/[\s_-]+\d+$/, '') : text);
    const wanted = key(value);
    const matches = heroes.filter(hero => wanted && key(hero) === wanted);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) return null;
  }
  return null;
}

export function heroNames(references) {
  const names = new Map();
  for (const ref of references) {
    if (ref.kind === 'image' && ['Лицо героя', 'Персонаж целиком'].includes(ref.role) && ref.owner?.trim()) {
      names.set(nameKey(ref.owner), ref.owner.trim());
    }
  }
  return [...names.values()];
}

export function inferPerformerPairs(prompt, heroes) {
  const names = new Map(heroes.map(name => [nameKey(name), name]));
  const pairs = [];
  const usedHeroes = new Set();
  const usedPerformers = new Set();
  const pattern = /^(?:REFERENCE\s+)?PERFORMER\s+([A-D])\s*(?:→|->|=|:)\s*([^\r\n]+)/gim;
  for (const match of String(prompt || '').matchAll(pattern)) {
    const hero = names.get(nameKey(match[2]));
    const performer = `performer ${match[1].toUpperCase()}`;
    if (!hero || usedHeroes.has(nameKey(hero)) || usedPerformers.has(performer)) return null;
    usedHeroes.add(nameKey(hero));
    usedPerformers.add(performer);
    pairs.push({ owner: hero, performer });
  }
  return pairs.length >= 2 && pairs.length === heroes.length ? pairs : null;
}

export function performerSpec(ref, references, prompt) {
  if (ref.kind !== 'video' || !MOTION_ROLES.has(ref.role)) return null;
  const heroes = heroNames(references);
  if (ref.performers?.pairs?.length) return {
    pairs: ref.performers.pairs.map(pair => ({ owner: resolveHeroName(pair.owner, heroes) || pair.owner, performer: pair.performer,
      ...(pair.relative_scale_percent ? { relative_scale_percent: pair.relative_scale_percent } : {}) })),
    keep_others: Boolean(ref.performers.keep_others),
  };
  const pairs = heroes.length > 1 ? inferPerformerPairs(prompt, heroes) : null;
  return pairs ? { pairs, keep_others: false } : null;
}

export function pairingButton(ref, references, prompt) {
  if (ref.kind !== 'video' || !MOTION_ROLES.has(ref.role) || heroNames(references).length < 2) return null;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'reference-pair-button';
  button.dataset.action = 'pairing';
  const count = performerSpec(ref, references, prompt)?.pairs.length || 0;
  button.textContent = count ? `✓ ${count} пары` : 'Связать героев';
  button.setAttribute('aria-label', `Кого заменяет видео ${ref.name}`);
  return button;
}

export function openPairingDialog(ref, heroes, prompt, onSave) {
  const dialog = document.createElement('dialog');
  dialog.className = 'reference-pair-dialog';
  const form = document.createElement('form');
  form.method = 'dialog';
  const heading = document.createElement('h2');
  heading.textContent = 'Кого заменяет видео';
  const description = document.createElement('p');
  description.textContent = `${ref.name} · укажи исполнителя и рост героя относительно первого героя. Например, 67% для заметно меньшего героя.`;
  const existing = performerSpec(ref, heroes.map(owner => ({ kind: 'image', role: 'Лицо героя', owner })), prompt)?.pairs || [];
  const rows = heroes.map((hero, index) => {
    const label = document.createElement('label');
    label.textContent = hero;
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 80;
    input.required = true;
    input.value = existing.find(pair => nameKey(pair.owner) === nameKey(hero))?.performer || `performer ${String.fromCharCode(65 + index)}`;
    input.setAttribute('aria-label', `Исполнитель для ${hero}`);
    const scale = document.createElement('input');
    scale.type = 'number'; scale.min = '25'; scale.max = '200'; scale.step = '1';
    scale.placeholder = index === 0 ? '100' : 'Например, 67';
    scale.value = existing.find(pair => nameKey(pair.owner) === nameKey(hero))?.relative_scale_percent || '';
    scale.setAttribute('aria-label', `Рост ${hero} в процентах от первого героя`);
    label.append(input, document.createTextNode(' Рост от первого героя, %'), scale);
    return { hero, label, input, scale };
  });
  const keep = document.createElement('label');
  keep.className = 'reference-pair-keep';
  const keepInput = document.createElement('input');
  keepInput.type = 'checkbox';
  keepInput.checked = Boolean(ref.performers?.keep_others);
  keep.append(keepInput, document.createTextNode(' Оставить остальных исполнителей в кадре'));
  const actions = document.createElement('div');
  actions.className = 'reference-pair-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'Отмена';
  cancel.addEventListener('click', () => dialog.close());
  const save = document.createElement('button');
  save.type = 'submit';
  save.textContent = 'Сохранить пары';
  actions.append(cancel, save);
  form.append(heading, description, ...rows.map(row => row.label), keep, actions);
  form.addEventListener('submit', event => {
    event.preventDefault();
    const pairs = rows.map(row => ({ owner: row.hero, performer: row.input.value.trim(),
      ...(row.scale.value ? { relative_scale_percent: Number(row.scale.value) } : {}) }));
    if (new Set(pairs.map(pair => pair.performer.toLocaleLowerCase())).size !== pairs.length) {
      rows[0].input.setCustomValidity('Исполнители должны различаться');
      rows[0].input.reportValidity();
      return;
    }
    onSave({ pairs, keep_others: keepInput.checked });
    dialog.close();
  });
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  dialog.append(form);
  document.body.append(dialog);
  dialog.showModal();
}
