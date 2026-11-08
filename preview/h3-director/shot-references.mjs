// «Сюжет»: references of one shot. Every shot gets the scene materials, but the faces and
// costumes of heroes who are not in this shot are left out, so H3 does not put an absent hero
// into the frame. Conservative: nothing is dropped unless the scene has 2+ heroes, the shot text
// names at least one of them, and the dropped hero has no voice / video bound to them (the Mac
// store needs a face for every owned voice) and no <Picture N> tag in the shot text.
// Picture numbers must stay consecutive (app/reference_manifest.py), so the kept pictures are
// renumbered and the shot prompt's tags are rewritten with the same map.
const HERO_IMAGE_ROLES = new Set(['Лицо героя', 'Персонаж целиком', 'Костюм']);
const FACE_ROLES = new Set(['Лицо героя', 'Персонаж целиком']);
const TAG = { image: 'Picture', video: 'Video', audio: 'Audio' };

const fold = (value) => String(value || '').trim().toLocaleLowerCase('ru');
const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function namesHero(text, owner) {
  const name = fold(owner);
  if (!name) return false;
  return new RegExp(`(^|[^\\p{L}\\p{N}])${escape(name)}($|[^\\p{L}\\p{N}])`, 'u').test(fold(text));
}

export function normalizeLegacyIr(prompt) {
  if (!/^\s*subject_definitions\s*:/i.test(prompt)) return prompt;
  const sections = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description', 'overall_soundscape', 'non_diegetic_music'];
  const matches = [...String(prompt).matchAll(/\b(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music)\s*:/gi)];
  if (matches.length !== sections.length || matches.some((match, i) => match[1].toLowerCase() !== sections[i])) return prompt;
  return matches.map((match, i) => {
    let body = prompt.slice(match.index + match[0].length, matches[i + 1]?.index ?? prompt.length).trim();
    if (i === 1 && !body.startsWith('[')) body = `[reference generation] ${body}`;
    return `${sections[i]}:\n${body}`;
  }).join('\n\n');
}

// Old scene drafts declared every franchise picture in subject_definitions and
// retention_analysis, even when a close-up explicitly excluded that hero.
// Remove only those metadata clauses; a tag in the actual action still blocks.
export function pruneAbsentHeroMetadata(prompt, references, castOwners) {
  if (!Array.isArray(castOwners)) return prompt;
  prompt = normalizeLegacyIr(prompt);
  const cast = new Set(castOwners.map(fold));
  const absent = (references || []).filter(ref => ref.kind === 'image' && HERO_IMAGE_ROLES.has(ref.role)
    && ref.owner && !cast.has(fold(ref.owner)));
  if (!absent.length) return prompt;
  const tags = new Set(absent.map(ref => `<Picture ${ref.number}>`));
  const stripClauses = (body) => body.split(';').map(clause => clause.trim())
    .filter(clause => clause && ![...tags].some(tag => clause.includes(tag))).join('; ');
  if (!/^\s*subject_definitions\s*:/i.test(prompt)) {
    const marker = /\[Shot\s+\d+\]|\bdetailed_description\s*:/i.exec(prompt);
    if (!marker) return prompt;
    let head = prompt.slice(0, marker.index);
    head = stripClauses(head);
    return head + prompt.slice(marker.index);
  }
  if (!/^subject_definitions:\n/m.test(prompt)) return String(prompt).replace(/^(subject_definitions|retention_analysis):([^\n]*)/gim,
    (_whole, section, body) => `${section}: ${stripClauses(body)}`);
  return String(prompt).replace(/^(subject_definitions|retention_analysis):\n([\s\S]*?)(?=\n\n[a-z_]+:|$)/gim, (_whole, section, body) => {
    return `${section}:\n${stripClauses(body)}`;
  });
}

// Where a shot happens: its location, else the head of its «is the environment:» line (scene_kit.shot_place).
export function shotPlace(prompt, location = '') {
  const declared = /is the environment:\s*([^\n.:;]*)/i.exec(String(prompt || ''))?.[1]?.trim() || '';
  return [String(location || '').trim(), declared].filter(Boolean).join(' ');
}

// The shot's place against the passport (scene_kit.breakdown_findings): '' when it is a passport place with a
// picture or no place is stated, otherwise what the launch will refuse.
export function placeNote(prompt, location = '', places = []) {
  const where = shotPlace(prompt, location);
  if (!where || !places?.length) return '';
  const here = places.filter((place) => [place.name, ...(place.aliases || [])].some((alias) => namesHero(where, alias)));
  if (!here.length) return `Место «${where}» не описано в паспорте: без картинки H3 рисует его заново в каждом шоте. Добавь в паспорт место с картинкой.`;
  if (!here.some((place) => place.assets?.length)) return `У места «${here[0].name}» в паспорте нет картинки — добавь её.`;
  return '';
}

// «Окружение» pictures of a passport place that is not this shot's place (31.10: the apartment picture went into
// balcony shots and H3 drew the living room). A picture of no known place, a shot with no stated place and a
// picture the prompt tags itself stay — the Mac gate (scene_kit.place_findings) refuses that last contradiction.
export function placeOff(references, prompt, location = '', places = []) {
  const where = shotPlace(prompt, location);
  if (!where || !places?.length) return [];
  return (references || []).filter((ref) => {
    if (ref.kind !== 'image' || ref.role !== 'Окружение') return false;
    if (String(prompt || '').includes(`<Picture ${ref.number}>`)) return false;
    const own = places.filter((place) => (place.assets || []).includes(ref.id));
    return own.length && !own.some((place) => [place.name, ...(place.aliases || [])].some((alias) => namesHero(where, alias)));
  }).map((ref) => ref.id);
}

// `off`: materials the user switched off for this one shot on the canvas (cut.offRefs). They leave the shot
// after the cast pass, and every kind is renumbered from the original numbers, so tags stay consistent.
export function shotReferences(references, text, requiredAssetIds = [], castOwners = null, off = []) {
  const offIds = new Set(off || []);
  const offRefs = (references || []).filter((ref) => offIds.has(ref.id));
  if (!offRefs.length) return castReferences(references, text, requiredAssetIds, castOwners);
  for (const ref of offRefs) {
    const label = [ref.role, ref.owner].filter(Boolean).join(' ') || ref.name || 'материал';
    if (requiredAssetIds.includes(ref.id)) throw new Error(`ShotSpec требует «${label}», а он выключен для этого шота`);
    if (String(text || '').includes(`<${TAG[ref.kind]} ${ref.number}>`) ||
        (ref.kind === 'image' && new RegExp(`\\bPicture\\s+${ref.number}\\b`, 'i').test(text || '')))
      throw new Error(`Промпт шота ссылается на «${label}», а он выключен для этого шота`);
  }
  const base = castReferences(references, text, requiredAssetIds, castOwners);
  const original = new Map((references || []).map((ref) => [ref.id, ref.number]));
  const counts = { image: 0, video: 0, audio: 0 };
  const renumber = new Map();
  const kept = base.references.filter((ref) => !offIds.has(ref.id)).map((ref) => {
    if (typeof ref.number !== 'number') return ref;
    const number = ++counts[ref.kind];
    const from = original.get(ref.id) ?? ref.number;
    if (from !== number) renumber.set(`<${TAG[ref.kind]} ${from}>`, `<${TAG[ref.kind]} ${number}>`);
    return number === ref.number ? ref : { ...ref, number };
  });
  return { references: kept, renumber, dropped: [...base.dropped, ...offRefs] };
}

function castReferences(references, text, requiredAssetIds = [], castOwners = null) {
  const refs = references || [];
  const heroes = [...new Set(refs.filter((ref) => ref.kind === 'image' && FACE_ROLES.has(ref.role) && ref.owner)
    .map((ref) => fold(ref.owner)))];
  const unchanged = { references: refs, renumber: new Map(), dropped: [] };
  const explicit = Array.isArray(castOwners);
  const declared = explicit ? [...new Set(castOwners.map(fold).filter(Boolean))] : [];
  if (explicit) {
    const missing = declared.filter((owner) => !heroes.includes(owner));
    if (missing.length) throw new Error(`У шота нет фото героев: ${missing.join(', ')}`);
    const dropped = refs.filter((ref) => ref.owner && (HERO_IMAGE_ROLES.has(ref.role) || ref.role === 'Голос')
      && !declared.includes(fold(ref.owner)));
    for (const ref of dropped) {
      if (requiredAssetIds.includes(ref.id)) throw new Error(`ShotSpec требует ${ref.owner}, которого нет в составе шота`);
      if (String(text || '').includes(`<${TAG[ref.kind]} ${ref.number}>`) ||
          (ref.kind === 'image' && new RegExp(`\\bPicture\\s+${ref.number}\\b`, 'i').test(text || '')))
        throw new Error(`Промпт шота ссылается на ${ref.owner}, которого нет в составе шота`);
    }
    if (!dropped.length) return unchanged;
    const gone = new Set(dropped.map((ref) => ref.id));
    const counts = { image: 0, video: 0, audio: 0 };
    const renumber = new Map();
    const kept = refs.filter((ref) => !gone.has(ref.id)).map((ref) => {
      const number = ++counts[ref.kind];
      if (number === ref.number) return ref;
      renumber.set(`<${TAG[ref.kind]} ${ref.number}>`, `<${TAG[ref.kind]} ${number}>`);
      return { ...ref, number };
    });
    return { references: kept, renumber, dropped };
  }
  if (heroes.length < 2) return unchanged;
  const present = heroes.filter((hero) => namesHero(text, hero));
  if (!present.length) return unchanged;
  const bound = new Set(refs.filter((ref) => ref.kind !== 'image' && ref.owner).map((ref) => fold(ref.owner)));
  const tagged = (ref) => String(text || '').includes(`<${TAG[ref.kind]} ${ref.number}>`);
  for (const ref of refs) if (ref.owner && tagged(ref)) bound.add(fold(ref.owner));
  const dropped = refs.filter((ref) => ref.kind === 'image' && HERO_IMAGE_ROLES.has(ref.role) && ref.owner
    && !requiredAssetIds.includes(ref.id) && !present.includes(fold(ref.owner)) && !bound.has(fold(ref.owner)));
  if (!dropped.length) return unchanged;
  const gone = new Set(dropped.map((ref) => ref.id));
  // Only pictures are dropped, so only pictures move; video and audio numbers stay as they are.
  let picture = 0;
  const renumber = new Map();
  const kept = refs.filter((ref) => !gone.has(ref.id)).map((ref) => {
    if (ref.kind !== 'image' || typeof ref.number !== 'number') return ref;
    picture += 1;
    if (picture === ref.number) return ref;
    renumber.set(`<Picture ${ref.number}>`, `<Picture ${picture}>`);
    return { ...ref, number: picture };
  });
  return { references: kept, renumber, dropped };
}

// One pass, so <Picture 3>→<Picture 2> and <Picture 2>→<Picture 1> never chain.
export function renumberTags(prompt, renumber) {
  if (!renumber?.size) return prompt;
  return String(prompt).replace(/<(?<tagKind>Picture|Video|Audio)\s+(?<tagNumber>\d+)>|\bPicture\s+(?<bareNumber>\d+)\b/gi,
    (match, _kind, _number, _bare, _offset, _source, groups) => {
      const original = groups.bareNumber ? `<Picture ${groups.bareNumber}>` : `<${groups.tagKind} ${groups.tagNumber}>`;
      const replacement = renumber.get(original);
      return replacement ? (groups.bareNumber ? replacement.slice(1, -1) : replacement) : match;
    });
}
