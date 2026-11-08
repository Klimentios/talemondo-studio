// Presets fill real project fields. They never select an unavailable model.
export const MUSIC_PRESETS = Object.freeze([
  { id: 'cinematic', label: 'Кино', prompt: 'Cinematic orchestral score, warm strings, intimate piano, gradual emotional build, wide dynamic range.' },
  { id: 'electronic', label: 'Электроника', prompt: 'Modern electronic track, deep warm bass, crisp percussion, evolving synthesizers, atmospheric and energetic.' },
  { id: 'ambient', label: 'Атмосфера', prompt: 'Ambient soundscape, soft pads, spacious reverb, gentle movement, restrained melody.' },
  { id: 'pop', label: 'Поп', prompt: 'Contemporary pop arrangement, memorable chorus, polished drums, warm bass, bright synthesizers.' },
]);

export const VOICE_PRESETS = Object.freeze([
  { id: 'natural', label: 'Естественно', direction: 'Speak naturally and clearly, with conversational pacing.' },
  { id: 'calm', label: 'Спокойно', direction: 'Speak calmly and warmly, with gentle pauses.' },
  { id: 'dramatic', label: 'Драматично', direction: 'Speak with restrained dramatic tension and deliberate pauses.' },
  { id: 'energetic', label: 'Энергично', direction: 'Speak with lively energy and confident rhythm.' },
]);

export const MIX_PRESETS = Object.freeze([
  { id: 'dialogue', label: 'Диалог', source_level: .55, speech_level: 1, music_level: .25, effects_level: .45, ducking: true },
  { id: 'balanced', label: 'Сбалансировано', source_level: .8, speech_level: 1, music_level: .55, effects_level: .7, ducking: true },
  { id: 'music', label: 'Музыка впереди', source_level: .4, speech_level: .8, music_level: 1, effects_level: .55, ducking: false },
]);
