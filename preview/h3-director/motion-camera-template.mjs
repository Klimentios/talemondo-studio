import policy from './motion-reference-policy.json?v=20261015-1' with { type: 'json' };
import { subjectNames } from './reference-owners.mjs?v=20261029-scene-run-2';

export function motionReferenceTemplate(references, prompt = '', { useVideoAudio = false, variant = 'motion_camera', cameraBrief = '', sceneBrief = '' } = {}) {
  if (!['motion_camera', 'motion_camera_scene', 'motion_only'].includes(variant)) throw new Error('Неизвестный шаблон движения.');
  const motionOnly = variant === 'motion_only';
  const videos = references.filter(r => r.kind === 'video' && r.role !== 'Продолжение');
  if (videos.length !== 1 || !['Движение', 'Движение и камера'].includes(videos[0].role)) throw new Error('Выбери одно видео движения.');
  const images = references.filter(r => r.kind === 'image');
  if (variant === 'motion_camera_scene' && !images.some(r => r.role === 'Окружение')) throw new Error('Добавь фото своей локации с ролью «Окружение».');
  const names = subjectNames(prompt);
  const identities = images.filter(r => ['Лицо героя', 'Персонаж целиком'].includes(r.role));
  const explicitOwners = [...new Set(identities.map(r => String(r.owner || '').trim()).filter(Boolean))];
  const fallbackOwner = explicitOwners.length === 1 ? explicitOwners[0] : names.length === 1 ? names[0] : '';
  const owner = r => String(r.owner || fallbackOwner).trim();
  if (!identities.length) throw new Error('Добавь фото героя с ролью «Лицо героя» или «Персонаж целиком».');
  if (identities.some(r => !owner(r))) throw new Error('Назначь владельца каждому фото героя.');
  const heroes = [...new Set(identities.map(owner))];
  const picture = r => `<Picture ${images.indexOf(r) + 1}>`;
  const definitions = [], retention = [];
  for (const [i, name] of heroes.entries()) {
    const looks = identities.filter(r => owner(r) === name).map(picture).join(' and ');
    const clothes = images.filter(r => r.role === 'Костюм' && owner(r) === name).map(picture).join(' and ');
    definitions.push(`<Subject ${i + 1}> is ${name}, whose identity and visible anatomical features come from ${looks}${clothes ? ` and whose costume comes from ${clothes}` : ''}.`);
    retention.push(`<Subject ${i + 1}>: fully_preserved — identity, hair or fur and visible anatomical features from ${looks}; body design follows the character references and brief${clothes ? `; costume and accessories from ${clothes}` : ''}.`);
  }
  const costumes = images.filter(r => r.role === 'Костюм');
  if (costumes.some(r => !heroes.includes(owner(r)))) throw new Error('Назначь костюм одному из выбранных героев.');
  if (heroes.length > 1 && !(videos[0].performers?.pairs?.length === heroes.length)) throw new Error('Свяжи каждого героя с его исполнителем в видео.');
  const pairs = videos[0].performers?.pairs || [];
  if (heroes.length > 1 && (new Set(pairs.map(p => p.owner)).size !== heroes.length || pairs.some(p => !heroes.includes(p.owner) || !p.performer))) throw new Error('Проверь пары героев и исполнителей.');
  const anchor = pairs[0]?.owner;
  const pairing = pairs.length ? pairs.map(p => `${p.owner} performs the actions of ${p.performer}` +
    (anchor && p.owner !== anchor && p.relative_scale_percent ? ` and remains ${p.relative_scale_percent}% of ${anchor}'s standing height including costume` : '')).join('; ') +
    '. Each named hero retains only their own face, fur and costume throughout every crossing and overlap.' : '';
  const cast = videos[0].performers?.keep_others === true ? ' Keep unassigned source performers.' : ' Only the assigned target heroes appear.';
  definitions.push(motionOnly ? '<Video 1> supplies only choreography, timing, gestures and facial expressions. Its performer is separate from the target character.' : '<Video 1> supplies performance and camera movement only. Source performers, location design, lighting and graphics do not define the target appearance.');
  retention.push(motionOnly ? '<Video 1>: attribute_transfer — choreography, timing, gestures and facial expressions only; scene and camera follow the assigned references and brief.' : '<Video 1>: attribute_transfer — choreography, timing, expressions and camera movement only; create an original unbranded scene.');
  const sceneOverrides = [];
  for (const ref of images.filter(r => !identities.includes(r) && r.role !== 'Костюм')) {
    const attributes = { 'Окружение': 'location and set layout', 'Свет': 'lighting', 'Стиль': 'rendering style', 'Цвет': 'color palette', 'Предмет': 'the referenced prop', 'Композиция': 'the explicitly assigned composition' };
    if (!attributes[ref.role]) throw new Error(`Роль «${ref.role}» требует собственного промпта; шаблон не изменён.`);
    definitions.push(`${picture(ref)} supplies only ${attributes[ref.role]}.`);
    retention.push(`${picture(ref)}: attribute_transfer — ${attributes[ref.role]} only.`);
    sceneOverrides.push(`Use ${picture(ref)} for ${attributes[ref.role]}; create the remaining scene attributes independently.`);
  }
  const audio = references.filter(r => r.kind === 'audio');
  if (useVideoAudio) {
    definitions.push('<Audio 1> is the soundtrack of <Video 1>.');
    retention.push('<Audio 1>: reference — sound and rhythm from the source soundtrack.');
  }
  audio.forEach((r, i) => {
    const tag = `<Audio ${i + 1 + (useVideoAudio ? 1 : 0)}>`;
    definitions.push(`${tag} supplies the assigned ${r.role || 'sound'} reference.`);
    retention.push(`${tag}: reference — the assigned sound attributes.`);
  });
  const music = audio.find(r => r.role === 'Музыка');
  return [
    'subject_definitions:\n\n' + definitions.join('\n'),
    `summary:\n\n[${['reference generation', ...(audio.length ? ['audio reference'] : []), ...(useVideoAudio ? ['audio reuse'] : [])].join(' + ')}] The assigned picture characters perform the choreography of <Video 1> ${motionOnly ? 'within the assigned scene and camera' : 'with its camera movement in an original unbranded scene'}. ` + pairing + cast,
    'retention_analysis:\n\n' + retention.join('\n'),
    'detailed_description:\n\n[Shot 1] Start at the beginning of the selected video excerpt. Follow its observed action order, timing, rhythm, gestures, facial expressions and physical contacts through the end of the shot. ' + policy.identity_rule + ' ' + (motionOnly ? (cameraBrief || policy.motion_only_camera_rule) + ' ' + (sceneBrief || (images.some(r => r.role === 'Окружение') ? 'Use the assigned location reference.' : 'Use a neutral studio floor with an uncluttered background and soft neutral light.')) + ' Camera, location and lighting are independent of the motion video. Render clean scene footage without source overlays.' : policy.camera_rule + ' ' + policy.scene_rule) + ' ' + sceneOverrides.join(' '),
    'overall_soundscape:\n\n' + (useVideoAudio ? 'Use <Audio 1> for the source sound and rhythm.' : 'Movement-related sounds follow visible physical contacts. No added dialogue.'),
    'non_diegetic_music:\n\n' + (music ? `Use <Audio ${audio.indexOf(music) + 1 + (useVideoAudio ? 1 : 0)}> for music.` : useVideoAudio ? 'Follow the referenced soundtrack.' : 'No added music.'),
  ].join('\n\n');
}

export function motionCameraTemplate(references, prompt = '', options = {}) {
  return motionReferenceTemplate(references, prompt, options);
}
