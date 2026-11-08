import { aspectRatio } from './aspects.mjs?v=20261106-aspects-1';
import motionPolicy from './motion-reference-policy.json?v=20261015-1' with { type: 'json' };
export { addActingVoiceDirection } from './voice-direction.mjs?v=20261004-audit-1';
export const modes = {
  replace: 'Замена персонажа',
  realistic: 'Реализм',
  animation3d: '3D-мультфильм',
  animation2d: '2D-мультфильм',
};

export function defaultStyleAdapterForMode(mode) {
  return ({ animation3d: 'style3d', animation2d: 'cartoon2d' })[mode] || 'none';
}

export function effectiveStyleAdapter(mode, selected = 'none', hasImage = false, hasVideo = false) {
  // The server's precompiled 3D path binds this specific LoRA. Reflect it in
  // the Studio request and fail early when the GPU has not confirmed the file.
  // Drafts saved before the cleanup may still name the removed sketch2d/characterswap LoRAs.
  return ({ animation3d: 'style3d', animation2d: 'cartoon2d' })[mode] || 'none';
}

export function canSubmitStyleAdapter(adapter, enabledAdapters) {
  return adapter === 'none' || (['style3d', 'cartoon2d'].includes(adapter) && enabledAdapters.includes(adapter));
}

export const SCALE_CONTINUITY_DIRECTION = `SUBJECT SCALE AND HEIGHT CONTINUITY: Use the supplied character references to establish each recurring subject's canonical body size, proportions and relative standing height, and keep those ratios fixed through every frame and cut. A domestic cat remains clearly cat-sized and visibly smaller than its adult owner; never enlarge it to human size. If adult men and adult women share a ground plane and the references or story do not set their heights, make the adult woman slightly shorter, about 95% of the adult man's standing height. Preserve any explicit sizes or relationships in the user's prompt or references over that default. Apparent frame size may change only when camera-to-subject distance changes with coherent perspective. No unexplained scaling, morphing, or size jumps.`;

export function addScaleContinuityDirection(prompt) {
  const text = String(prompt ?? '').trim();
  if (!text || text.includes('SUBJECT SCALE AND HEIGHT CONTINUITY:')) return text;
  return `${text}\n\n${SCALE_CONTINUITY_DIRECTION}`;
}

const REFERENCE_ROLE_DIRECTIONS = {
  'Лицо героя': "defines this character's face and identity only",
  'Персонаж целиком': "defines this character's full-body design and silhouette",
  'Костюм': 'defines wardrobe and carried props for the named hero only; ignore the pictured face, body identity, background, lighting and framing',
  'Стиль': 'defines rendering style, palette and line/material treatment only',
  'Окружение': 'defines the location and set dressing only',
  'Предмет': 'defines the referenced object, its visible shape, material and scale only; ignore the background, people, lighting and camera angle',
  'Свет': 'defines lighting only: key and fill direction, softness, contrast and color temperature; do not copy its subject, location or composition',
  'Цвет': 'defines color grade and palette only; do not copy its subject, location or composition',
  'Кадр сцены': 'anchors this scene composition at its requested frame',
  'Композиция': 'is a blurred grey layout sketch: it guides only where the figures stand, the shot size and the camera angle; every person keeps the face, hair and clothing of their own picture',
  'Исходное видео': 'provides source-scene context; preserve only the requested attributes',
  'Движение': motionPolicy.motion_only_instruction,
  'Движение и камера': motionPolicy.role_instruction,
  'Актёрская игра': "provides this character's facial expression, head motion, gesture and timing only; do not copy the driving performer, voice, wardrobe or background",
  'Камера': 'provides camera framing and movement only',
  'Ритм сцены': 'provides edit rhythm and event timing only',
  'Голос': 'provides voice characteristics only',
  'Музыка': 'provides music style and rhythm only',
  'Звуковой эффект': 'provides sound-effect character only',
  'Атмосфера': 'provides ambience only',
};

export function addReferenceRoleDirections(prompt, references = []) {
  const text = String(prompt ?? '').trim();
  if (!text) return text;
  const directives = references.flatMap((ref) => {
    const direction = REFERENCE_ROLE_DIRECTIONS[ref.role];
    if (!direction) return [];
    const tag = referenceTag(ref);
    const owner = ['Костюм', 'Актёрская игра', 'Движение', 'Движение и камера', 'Голос'].includes(ref.role) && ref.owner ? ` for ${ref.owner} only` : '';
    const line = `${tag}${owner} ${direction}`;
    return text.includes(line) ? [] : [line];
  });
  if (!directives.length) return text;
  return `${text}\n\nREFERENCE ROLE CONTRACT: ${directives.join('; ')}.`;
}

export function createProject() {
  return {
    mode: 'replace',
    profile: 'bf16',
    aspect: '9:16',  // every STUDIO video is vertical (TikTok 9:16)
    seed: '',
    quality: 'custom',
    megapixels: 0.8,
  steps: 24,
    refImageSize: 'match',  // about −14 % render time at the same look; accepted recipes keep their own size
    styleAdapter: 'none',
    cacheMode: 'safe',
    samplingProfile: 'standard',
    swapLora: 'v1',
    useVideoAudio: false,
    cameraEffect: 'none',
    guideFrames: null,
    actingMode: 'auto',
    actingVoice: 'auto',
    actingOwner: '',
    actingTimbre: '',
    actingTexture: 'auto',
    actingAccent: '',
    actingEmotion: 'auto',
    actingPace: 'auto',
    actingVoices: {},
    references: [],
    library: {},
    direction: { camera: '', lighting: '', palette: '', style: '' },
    director: {},
    directorFavorites: [],
    directorRecent: [],
    customDirectorPresets: [],
    referenceCounters: { image: 0, video: 0, audio: 0 },
    cuts: [{ id: 'cut-1', duration: 5, prompt: '' }],
  };
}

export function effectiveResolution(aspect, quality, customMegapixels = 0.98) {
  const targets = { compact: 0.5, quick: 0.8, balanced: 0.9, max: 0.98 };
  const mp = quality === 'custom' ? Number(customMegapixels) : targets[quality];
  // Any «W:H» format: which ones the GPU takes is its capabilities list (aspects.mjs), checked again by the Mac queue.
  const ratio = aspectRatio(aspect);
  if (!ratio || !Number.isFinite(mp) || mp < 0.2 || mp > 0.98) {
    throw new Error('Недопустимое разрешение H3');
  }
  const target = mp * 1024 * 1024;
  const width = Math.round(Math.sqrt(target * ratio) / 32) * 32;
  const height = Math.round(Math.sqrt(target / ratio) / 32) * 32;
  if (width * height > 1344 * 768) throw new Error('Превышен нативный лимит H3');
  return { width, height, pixels: width * height, selectorMp: mp };
}

export function effectiveFrameCount(seconds) {
  if (!Number.isInteger(seconds) || seconds < 4 || seconds > 15) {
    throw new Error('Длительность H3: 4–15 секунд');
  }
  const raw = Math.max(5, Math.round(seconds * 24));
  return raw + (5 - raw % 17 + 17) % 17;
}

const tagNames = { image: 'Picture', video: 'Video', audio: 'Audio' };
const supportedTypes = {
  'image/png': 'image', 'image/jpeg': 'image', 'image/webp': 'image',
  'video/mp4': 'video', 'video/quicktime': 'video', 'video/webm': 'video',
  'audio/mpeg': 'audio', 'audio/mp3': 'audio', 'audio/wav': 'audio',
  'audio/x-wav': 'audio', 'audio/mp4': 'audio', 'audio/x-m4a': 'audio',
  'audio/flac': 'audio', 'audio/ogg': 'audio',
};

export function mediaKind(file) {
  return supportedTypes[file.type?.toLowerCase()] || null;
}

export function referenceTag(ref) {
  return `<${tagNames[ref.kind]} ${ref.number}>`;
}

export function referenceDisplayTag(ref, references) {
  if (ref.kind === 'video' && ref.role === 'Продолжение') return '↳ Продолжение · без тега';
  return referenceTag(ref);
}

export function addReference(project, reference) {
  if (!tagNames[reference.kind]) throw new Error('Unsupported reference kind');
  return renumberReferences({ ...project, references: [...project.references, reference] }, project.references);
}

export function removeReference(project, id) {
  return renumberReferences({ ...project, references: project.references.filter((ref) => ref.id !== id) }, project.references);
}

export function renumberReferences(project, previous = project.references, previousUseVideoAudio = project.useVideoAudio) {
  const counts = { image: 0, video: 0, audio: 0 };
  const soundtrackCount = project.useVideoAudio ? project.references.filter((ref) => ref.kind === 'video').length : 0;
  const references = project.references.map((ref) => ({ ...ref,
    number: ref.kind === 'audio' ? soundtrackCount + ++counts.audio : ++counts[ref.kind],
  }));
  const currentById = new Map(references.map((ref) => [ref.id, ref]));
  const replacements = new Map(previous.filter((ref) => Number.isInteger(ref.number)).map((ref) => {
    const next = currentById.get(ref.id);
    return [referenceTag(ref), next ? referenceTag(next) : `⟦удалённый ${tagNames[ref.kind]} ${ref.number}⟧`];
  }));
  if (previousUseVideoAudio) {
    const oldVideos = previous.filter((ref) => ref.kind === 'video');
    oldVideos.forEach((ref, index) => {
      const oldNumber = Number.isInteger(ref.soundtrack_number) ? ref.soundtrack_number : index + 1;
      const next = currentById.get(ref.id);
      replacements.set(`<Audio ${oldNumber}>`, next && project.useVideoAudio
        ? `<Audio ${next.number}>` : `⟦удалённый Audio ${oldNumber}⟧`);
    });
  }
  const activeTags = new Set(references.map(referenceTag));
  const cuts = project.cuts.map((cut) => ({ ...cut, prompt: cut.prompt.replace(/<(?:Picture|Video|Audio) \d+>/g,
    (tag) => replacements.get(tag) || (previous.length ? `⟦неизвестный ${tag.slice(1, -1)}⟧` : tag))
    .replace(/⟦неизвестный ((?:Picture|Video|Audio) \d+)⟧/g, (marker, label) => {
      const tag = `<${label}>`;
      return activeTags.has(tag) ? tag : marker;
    }) }));
  return { ...project, references, cuts, referenceCounters: counts };
}

export function findMentionQuery(text, caret) {
  const before = text.slice(0, caret);
  const match = /(^|[\s(>.,;:!?])@([^\s@<>]*)$/.exec(before);
  return match ? { start: caret - match[2].length - 1, query: match[2] } : null;
}

export function insertReferenceMention(text, selectionStart, selectionEnd, triggerStart, tag) {
  const start = triggerStart ?? selectionStart;
  const updated = text.slice(0, start) + tag + text.slice(selectionEnd);
  return { text: updated, caret: start + tag.length };
}

export function mentionedReferences(prompt, references) {
  const byTag = new Map(references.map((ref) => [referenceTag(ref), ref]));
  const found = [];
  const seen = new Set();
  for (const match of prompt.matchAll(/<(?:Picture|Video|Audio) \d+>/g)) {
    const ref = byTag.get(match[0]);
    if (ref && !seen.has(ref.id)) {
      found.push(ref);
      seen.add(ref.id);
    }
  }
  return found;
}

export function addCut(project) {
  const nextId = Math.max(0, ...project.cuts.map((cut) => Number(cut.id.split('-')[1]) || 0)) + 1;
  return { ...project, cuts: [...project.cuts, { id: `cut-${nextId}`, duration: 5, prompt: '' }] };
}

export function removeCut(project, id) {
  if (project.cuts.length === 1) return project;
  return { ...project, cuts: project.cuts.filter((cut) => cut.id !== id) };
}

export function compileBrief(project) {
  const pictureCount = { image: 0, video: 0, audio: 0 };
  const references = project.references.map((ref) => {
    pictureCount[ref.kind] = Math.max(pictureCount[ref.kind], ref.number || pictureCount[ref.kind] + 1);
    const tag = ref.number ? referenceTag(ref) : `<${tagNames[ref.kind]} ${pictureCount[ref.kind]}>`;
    return `${tag} ${ref.role} — ${ref.name}`;
  });
  const cuts = project.cuts.map((cut, index) => `CUT ${index + 1} · ${cut.duration} сек\n${cut.prompt.trim() || 'Сцена пока не описана.'}`);
  return [
    `Режим: ${modes[project.mode]}`,
    'Модель: MiniMax H3 Ref2VA BF16',
    `Формат: ${project.aspect}`,
    '',
    'Референсы:',
    ...(references.length ? references : ['Не добавлены']),
    '',
    cuts.join('\n\n'),
  ].join('\n');
}
