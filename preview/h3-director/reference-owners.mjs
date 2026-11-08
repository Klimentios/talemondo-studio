import { pictureDeclarations } from './reference-declarations.mjs?v=20261014-heading-1';
export function subjectNames(prompt) {
  const text = String(prompt || '');
  const explicit = [...text.matchAll(/^<Subject \d+>\s+is\s+([^,.:\n]+)/gm)]
    .map((match) => match[1].trim());
  if (explicit.length) return explicit;
  const definitions = text.split(/^subject_definitions:\s*$/m)[1]?.split(/^\w[\w -]*:\s*$/m)[0] || '';
  const plain = [...new Set([...definitions.matchAll(/^([\p{Lu}][\p{L}\p{N}_-]{1,39})\s+is\b/gmu)]
    .map((match) => match[1]))];
  // Short Studio briefs often bind identity/costume directly to <Picture N>
  // without a subject_definitions block. Only accept explicit "for NAME" bindings.
  const pictures = [...text.matchAll(/^<Picture \d+>[^\n]*?\bfor\s+([\p{L}\p{N}_-]+)(?=[\s.,:;!?]|$)/gmu)]
    .map((match) => match[1].trim());
  // Offer explicitly named actors in prose as choices too (e.g. HERO is seated).
  // This only populates the picker; it does not assign an asset to a hero.
  const actors = [...text.matchAll(/\b([A-Z][A-Z0-9_-]{1,39})\s+(?:is|wears|remains|sits|drives|runs|walks|enters|exits|jumps|opens|holds|looks)\b/g)].map(match => match[1]);
  return [...new Set([...plain, ...pictures, ...pictureDeclarations(text).map(item => item.owner).filter(Boolean), ...actors])];
}

const sameName = (a, b) => String(a).trim().toUpperCase() === String(b).trim().toUpperCase();

export function referenceOwnerLabels(references, prompt) {
  const subjects = subjectNames(prompt);
  const labels = new Map();
  if (subjects.length === 1) {
    for (const ref of references) {
      // One hero found in the text never takes over a picture saved for another hero («ALEX walks to <Picture 2>
      // NOVA» names only ALEX as an actor; NOVA's photo stays NOVA's).
      if (ref.owner && !sameName(ref.owner, subjects[0])) continue;
      if (['Лицо героя', 'Персонаж целиком', 'Костюм', 'Движение', 'Движение и камера', 'Актёрская игра', 'Голос'].includes(ref.role))
        labels.set(ref.id, subjects[0]);
    }
  }
  return labels;
}

// Keep a visible choice even for one subject; do not guess between multiple heroes.
export function referenceOwnerControlState(ref, prompt) {
  const names = subjectNames(prompt);
  const value = ref.manualOwner
    ? (names.length > 1 && !names.includes(ref.owner) ? '' : ref.owner || '')
    : (names.length === 1 ? (ref.owner && !sameName(ref.owner, names[0]) ? ref.owner : names[0]) : names.length > 1 ? '' : ref.owner || '');
  if (names.length === 1 && value && !names.includes(value)) names.push(value);
  return { kind: names.length ? 'select' : 'input', names, value };
}
