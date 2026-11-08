// «Монтажный лист»: the whole film (all scenes, chosen takes) as one edit sheet for /api/edl/*.
// Transitions are the director schema's shot.transition_in values; a seam takes the user's choice,
// else the incoming shot's transition_in, else the outgoing shot's transition_out, else a cut.
import { directorFieldOptions } from './director-config.mjs?v=20261007-camera-options-5';
import { DEFAULT_ASPECT, aspectRatio } from './aspects.mjs?v=20261106-aspects-1';
import { planFilm } from './film-assembly.mjs?v=20261101-keyframe-1';

export const SHEET_DEFAULTS = Object.freeze({ aspect_ratio: DEFAULT_ASPECT, fade_in: 0, fade_out: 0.5, music_asset_id: '',
  music_volume: 0.35, music_duck: true, auto_sfx: true, loudness: true, film_look: false,
  look: '', look_intensity: 0.6, match_color: true, transitions: {} });

// Seam looks (xfade) with Russian names; the rest of the server's list shows as is.
export const EFFECT_NAMES = { fade: 'Растворение', dissolve: 'Пиксельное растворение', fadeblack: 'Через чёрное',
  fadewhite: 'Вспышка белым', hblur: 'Смаз движения', zoomin: 'Наезд в кадр', slideleft: 'Сдвиг влево',
  slideright: 'Сдвиг вправо', slideup: 'Сдвиг вверх', slidedown: 'Сдвиг вниз', smoothleft: 'Плавная шторка влево',
  smoothright: 'Плавная шторка вправо', circleopen: 'Круг раскрывается', circleclose: 'Круг закрывается',
  pixelize: 'Пикселизация', radial: 'Радиальная шторка', squeezeh: 'Сжатие', distance: 'Дистанция',
  wipeleft: 'Шторка влево', wiperight: 'Шторка вправо', coverleft: 'Наплыв слева', revealright: 'Открытие вправо' };
// Titles per shot (local_studio/motion.py templates): what the text and the second line mean for each.
export const GRAPHIC_NAMES = { NameCard: 'Титр героя (на стоп-кадре)', KineticTitle: 'Кинетический заголовок',
  LowerThird: 'Плашка с именем', TitleCard: 'Титульная карточка' };

function graphicFor(row, index) {
  const { template, text = '', line = '' } = row.graphic || {};
  if (!GRAPHIC_NAMES[template] || !text.trim()) return null;
  const clip = index + 1;
  if (template === 'NameCard') return row.freeze
    ? { template, clip, anchor: 'freeze', duration: row.freeze, props: { name: text, line } }
    : { template, clip, anchor: 'end', duration: 2, props: { name: text, line } };
  if (template === 'KineticTitle') return { template, clip, offset: 0.2, duration: 2.2, props: { text } };
  if (template === 'LowerThird') return { template, clip, offset: 0.5, duration: 2.5, props: { name: text, title: line } };
  return { template, clip, offset: 0.3, duration: 3, props: { title: text, subtitle: line } };
}

export const ACCENT_NAMES = { shake: 'Тряска и наезд', flash: 'Вспышка', glitch: 'Глитч', chroma: 'Цветовой сдвиг' };

// Shot-card fields the cinematographer's check reads; enum fields pass only with a schema value.
const ENUM_FIELDS = ['framing', 'angle', 'purpose', 'subject_view', 'composition', 'subject_side', 'look_room',
  'transition_out'];
const TEXT_FIELDS = ['title', 'camera_motion', 'motivation', 'emotion', 'intent', 'light_source'];
const PATTERNS = { axis_side: ['A', 'B', 'crossing'], screen_direction: ['left_to_right', 'right_to_left', 'toward', 'away'],
  eyeline: ['left', 'right', 'camera', 'up', 'down'] };

export const transitionOptions = () => directorFieldOptions('shot', 'transition_in');
const known = (key) => new Set(directorFieldOptions('shot', key).map((option) => option.value));

export function sheetSettings(project) {
  const saved = project?.editSheet || {};
  return { ...SHEET_DEFAULTS, ...saved, transitions: { ...(saved.transitions || {}) } };
}

function shotCard(cut, scene, sceneIndex) {
  const source = cut.sceneShot || {};
  const card = { location: String(scene.title || `Сцена ${sceneIndex + 1}`).slice(0, 120),
    duration: Math.min(Math.max(Number(cut.duration) || 5, 0.1), 60) };
  for (const key of ENUM_FIELDS) if (known(key).has(source[key])) card[key] = source[key];
  for (const key of TEXT_FIELDS) if (typeof source[key] === 'string' && source[key]) card[key] = source[key].slice(0, key === 'title' ? 120 : 200);
  for (const [key, values] of Object.entries(PATTERNS)) if (values.includes(source[key])) card[key] = source[key];
  const lens = Number(source.lens_mm);
  if (Number.isInteger(lens) && lens >= 8 && lens <= 600) card.lens_mm = lens;
  if (source.key_action === true) card.key_action = true;
  if (Array.isArray(source.characters)) card.characters = source.characters.filter((name) => typeof name === 'string').slice(0, 12);
  return card;
}

/** One row per shot of the film, in film order, with the transition that will be used. */
export function sheetRows(scenes, settings = SHEET_DEFAULTS) {
  const values = new Set(transitionOptions().map((option) => option.value));
  const rows = [];
  let previous = null;
  for (const [sceneIndex, scene] of (Array.isArray(scenes) ? scenes : []).entries()) {
    for (const [cutIndex, cut] of (Array.isArray(scene.cuts) ? scene.cuts : []).entries()) {
      const chosen = settings.transitions?.[cut.id];
      const planned = cut.sceneShot?.transition_in || previous?.sceneShot?.transition_out || 'cut';
      let transition = values.has(chosen?.type) ? chosen.type : values.has(planned) ? planned : 'cut';
      if (!rows.length && transition !== 'fade') transition = 'cut';
      const seconds = Number(chosen?.seconds);
      const freeze = Number(chosen?.freeze);
      rows.push({ cutId: cut.id, jobId: cut.shotJobId || '', number: rows.length + 1,
        effect: typeof chosen?.effect === 'string' ? chosen.effect : '',
        accent: ACCENT_NAMES[chosen?.accent] ? chosen.accent : '',
        graphic: chosen?.graphic && typeof chosen.graphic === 'object' ? { template: String(chosen.graphic.template || ''),
          text: String(chosen.graphic.text || '').slice(0, 120), line: String(chosen.graphic.line || '').slice(0, 160) } : null,
        freeze: Number.isFinite(freeze) && freeze > 0 ? Math.min(freeze, 4) : 0,
        exposure: Number.isFinite(Number(chosen?.exposure)) ? Math.min(Math.max(Number(chosen.exposure), -2), 2) : 0,
        label: `${scene.title || `Сцена ${sceneIndex + 1}`} · шот ${cutIndex + 1}`,
        planned: Number(cut.duration) || 0, transition, auto: !values.has(chosen?.type),
        seconds: Number.isFinite(seconds) && seconds >= 0 && chosen?.seconds !== '' && chosen?.seconds != null ? seconds : null,
        card: shotCard(cut, scene, sceneIndex) });
      previous = cut;
    }
  }
  return rows;
}

/** Edit sheet for /api/edl/plan and /api/edl/renders, or the reasons it cannot be built yet. */
export function buildSheet(scenes, jobsById, settings = SHEET_DEFAULTS) {
  const film = planFilm(scenes, jobsById);
  if (!film.ready) return { ready: false, issues: film.issues, body: null };
  const rows = sheetRows(scenes, settings);
  const clips = rows.map((row, index) => ({ job_id: row.jobId, label: row.label, transition_in: row.transition,
    ...(row.seconds != null ? { transition_duration: row.seconds } : {}),
    ...(row.effect && index ? { transition_effect: row.effect } : {}),
    ...(row.freeze ? { freeze_end: row.freeze } : {}),
    ...(row.accent ? { accents: [{ type: row.accent }] } : {}),
    ...(row.exposure ? { grade: { exposure: row.exposure } } : {}), shot: row.card }));
  const body = { version: 1, aspect_ratio: aspectRatio(settings.aspect_ratio) ? settings.aspect_ratio : DEFAULT_ASPECT, clips,
    fade_in: clamp(settings.fade_in, 0, 3), fade_out: clamp(settings.fade_out, 0, 5),
    target_seconds: film.seconds || undefined, auto_sfx: settings.auto_sfx !== false,
    loudness: settings.loudness === false ? null : -14,
    ...(settings.film_look ? { grain: 0.35, vignette: true } : {}) };
  if (settings.look) Object.assign(body, { look: settings.look, look_intensity: clamp(settings.look_intensity, 0, 1) });
  if (settings.match_color !== false && clips.length > 1) body.match_color = true;
  const graphics = rows.map(graphicFor).filter(Boolean);
  if (graphics.length) body.graphics = graphics;
  if (settings.music_asset_id) {
    body.music = { asset_id: settings.music_asset_id, volume: clamp(settings.music_volume, 0, 2),
      duck: settings.music_duck !== false };
  }
  return { ready: true, issues: [], body };
}

function clamp(value, low, high) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(Math.max(number, low), high) : low;
}

/** Findings of the server plan as short Russian lines: errors first. */
export function planNotes(plan) {
  const notes = (plan?.warnings || []).map((text) => ({ level: 'warning', text }));
  for (const finding of plan?.shot_review?.findings || []) {
    if (finding.severity === 'tip') continue;
    notes.push({ level: finding.severity, text: `Шот ${finding.shot}: ${finding.message}${finding.fix ? ` — ${finding.fix}` : ''}` });
  }
  return notes.sort((a, b) => (a.level === 'error' ? 0 : 1) - (b.level === 'error' ? 0 : 1));
}
