export function newAudioProject() {
  return {
    version: 1, title: 'Новый звуковой проект', mode: 'video',
    source: { kind: 'none', id: '' }, duration: 15, output_format: 'wav',
    lip_sync: false, lip_sync_target: '',
    lines: [{ id: crypto.randomUUID(), speaker: 'Герой', text: '', start: 0,
      voice_mode: 'preset', voice_asset_id: '', voice_description: '', direction: '', language: 'ru' }],
    mix: { source_level: 1, speech_level: 1, music_level: .55, effects_level: .7,
      music_asset_id: '', effects_asset_id: '', music_prompt: '', music_lyrics: '',
      music_bpm: null, music_keyscale: '', music_timesignature: '', music_seed: -1,
      effects_prompt: '', ducking: true },
  };
}

export function timelineRows(project) {
  const duration = Math.max(1, Number(project.duration) || 15);
  const rows = [];
  if (project.mode === 'video' && project.source.kind !== 'none' && Number(project.mix.source_level) > 0) rows.push({ name: 'Оригинал', type: 'source', start: 0, length: duration });
  project.lines.forEach((line, index) => {
    if (!line.text.trim()) return;
    rows.push({ name: line.speaker.trim() || `Голос ${index + 1}`, type: 'speech',
      start: Math.min(duration, Math.max(0, Number(line.start) || 0)),
      length: Math.min(duration, Math.max(1, Math.ceil(line.text.length / 12))) });
  });
  if (project.mix.music_asset_id || project.mix.music_prompt.trim()) rows.push({ name: 'Музыка', type: 'music', start: 0, length: duration });
  if (project.mix.effects_asset_id || project.mix.effects_prompt.trim()) rows.push({ name: 'Эффект', type: 'effects', start: 0, length: duration });
  return rows;
}

export function draftChecklist(project) {
  const issues = [];
  if (project.mode === 'video' && project.source.kind === 'none') issues.push('выбрать исходное видео');
  if (project.lines.some((line) => line.text.trim() && line.voice_mode === 'reference' && !line.voice_asset_id)) issues.push('прикрепить голосовой референс');
  if (!project.lines.some((line) => line.text.trim()) && !project.mix.music_asset_id &&
      !project.mix.music_prompt.trim() && !project.mix.effects_asset_id &&
      !(project.mode === 'video' && project.source.kind !== 'none' && Number(project.mix.source_level) > 0)) {
    issues.push('добавить речь, музыку, эффект или исходный звук');
  }
  if (project.mix.music_asset_id && project.mix.music_prompt.trim()) issues.push('выбрать готовую музыку или создание по описанию');
  if (project.mix.music_prompt.trim() && Number(project.duration) < 10) issues.push('длина музыки от 10 секунд');
  if (project.mix.music_prompt.trim() && Number(project.duration) > 600) issues.push('длина музыки до 600 секунд');
  if (String(project.mix.music_lyrics || '').trim() && !project.mix.music_prompt.trim()) issues.push('описать стиль музыки');
  if (project.mix.music_prompt.trim()) {
    const bpm = project.mix.music_bpm;
    if (bpm !== null && bpm !== undefined && bpm !== '' && (!Number.isInteger(Number(bpm)) || Number(bpm) < 30 || Number(bpm) > 300)) issues.push('темп музыки от 30 до 300 BPM');
    if (project.mix.music_keyscale && !/^[A-G](?:#|b)?(?: ?(?:Major|minor)|m)?$/.test(project.mix.music_keyscale)) issues.push('проверь тональность музыки');
    if (!['', '2', '3', '4', '6'].includes(String(project.mix.music_timesignature ?? ''))) issues.push('выбрать музыкальный размер');
    const seed = Number(project.mix.music_seed ?? -1);
    if (!Number.isInteger(seed) || seed < -1 || seed > 2147483647) issues.push('seed музыки вне диапазона');
  }
  if (project.lip_sync && !project.lip_sync_target.trim()) issues.push('указать говорящего в кадре');
  return issues;
}
