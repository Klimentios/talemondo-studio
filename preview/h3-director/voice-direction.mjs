export function addActingVoiceDirection(prompt, project = {}) {
  const voices = { ...(project.actingVoices || {}) };
  if (!Object.keys(voices).length && project.actingOwner) voices[project.actingOwner] = {
    voice: project.actingVoice, timbre: project.actingTimbre, texture: project.actingTexture,
    accent: project.actingAccent, emotion: project.actingEmotion, pace: project.actingPace,
  };
  let text = String(prompt || '').trim();
  const references = project.references || [];
  if (references.some((ref) => ref.kind === 'video')) {
    // Video-reference jobs only (picture-only jobs keep the golden graph):
    // no casting for a scene that says nobody talks, nor for saved heroes absent from this job.
    if (NO_SPEECH.test(text)) return text;
    const present = new Set(references.filter((ref) => ref.kind === 'image' && ref.owner)
      .map((ref) => String(ref.owner).toLocaleLowerCase()));
    for (const owner of Object.keys(voices)) if (!present.has(owner.toLocaleLowerCase())) delete voices[owner];
  }
  for (const [owner, settings] of Object.entries(voices)) {
    const subject = referenceSubjectName(text, owner, project.references || []);
    text = addOneVoiceDirection(text, subject, settings, owner);
  }
  return text;
}

// Mirrors local_studio/store.py _NO_SPEECH.
export const NO_SPEECH = /\bno\s+(?:talking|dialogue|dialog|speech|lines|spoken\s+words)\b|\bwithout\s+(?:dialogue|speech|words)\b|\b(?:nobody|no\s+one)\s+speaks\b|\bsilent\s+(?:video|scene|shot)\b|без\s+(?:слов|реплик|диалог|речи)|никто\s+не\s+говорит|молча/iu;

function escaped(value) { return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

function referenceSubjectName(prompt, owner, references) {
  const images = references.filter((ref) => ref.kind === 'image');
  for (const [index, ref] of images.entries()) {
    if (String(ref.owner || '').toLocaleLowerCase() !== owner.toLocaleLowerCase()) continue;
    const number = ref.number || index + 1;
    const match = String(prompt).match(new RegExp(`<Picture\\s+${number}>\\s+defines\\s+(?!(?:this|the|a|an)\\b)([\\p{L}\\p{N}_-]+)`, 'iu'));
    if (match) return match[1];
  }
  return owner;
}

function addOneVoiceDirection(prompt, owner, settings, oldOwner = owner) {
  // Only replace our generated one-line casting block; retain all screenplay text.
  const strip = (text, name) => text.replace(new RegExp(`(?:\\r?\\n){0,2}^VOICE CAST FOR ${escaped(name)}:[^\\r\\n]*`, 'gmu'), '');
  const text = strip(strip(String(prompt || ''), owner), oldOwner).trim();
  const description = text.match(new RegExp(`(?:^|\\n)${escaped(owner)}\\s+(?:speaks|speaking)\\s+([^\\n]{0,500}\\bvoice\\b[^\\n]{0,500})`, 'imu'))?.[1];
  const voice = {
    none: 'This character does not speak.',
    adult_male: 'Use a stable adult male voice for this character.',
    adult_female: 'Use a stable adult female voice for this character.',
    child: 'Use an age-appropriate child voice for this character, never an adult voice.',
    cat: 'Use species-appropriate cat vocalizations unless the story explicitly calls for speech.',
    dog: 'Use species-appropriate dog vocalizations unless the story explicitly calls for speech.',
  }[settings.voice];
  const timbre = String(settings.timbre || '').trim();
  const texture = {warm:'warm and soft',bright:'bright and clear',low:'low and full',raspy:'raspy',breathy:'breathy',nasal:'nasal',dry:'dry and restrained'}[settings.texture];
  const emotion = {restrained:'restrained',tender:'tender',tense:'tense',angry:'angry',afraid:'afraid',joyful:'joyful',ironic:'ironic'}[settings.emotion];
  const pace = {slow:'slow with pauses',measured:'measured',quick:'quick and energetic',broken:'broken and hesitant'}[settings.pace];
  const accent = String(settings.accent || '').trim();
  if (!owner || (!description && !voice && !timbre && !texture && !emotion && !pace && !accent)) return text;
  const casting = description
    ? `Follow the voice explicitly specified for ${owner} in this scene: ${description}. This scene description has priority over saved voice presets.`
    : `${voice || 'Follow the voice described in the scene; keep it suited to the character.'} ${texture ? `Stable timbre: ${texture}.` : ''} ${timbre ? `Timbre and manner: ${timbre}.` : ''} ${accent ? `Pronunciation/accent: ${accent}.` : ''} ${emotion ? `Emotional starting point: ${emotion}; follow later story beats.` : ''} ${pace ? `Speech rhythm: ${pace}.` : ''}`;
  const line = `VOICE CAST FOR ${owner}: ${casting} Keep this character's voice identity stable while varying emotion, pauses, stress and intonation according to each dialogue beat. Do not swap voices between speakers.`;
  return `${text}\n\n${line}`;
}
