// Studio art-direction presets. A visual choice is compiled into a concrete
// instruction. Camera specifications are catalog facts, not H3 sensor emulation.
import { PALETTE_CATALOG } from './assets/palettes/catalog.mjs';
import { DIRECTOR_SCHEMA } from './director-config.mjs?v=20261007-camera-options-5';
const titleEnById = {
  'camera.alexa35':'ARRI ALEXA 35','camera.sony_fx6':'Sony FX6','camera.ursa_cine':'URSA Cine 12K LF',
  'camera.sony_burano':'Sony BURANO','camera.canon_c400':'Canon EOS C400',
  'camera.alexa_mini_lf':'ARRI ALEXA Mini LF','camera.red_v_raptor_x':'RED V-RAPTOR XL [X]',
  'camera.panavision_dxl2':'Panavision Millennium DXL2','camera.documentary_rig':'Shoulder documentary rig',
  'camera.modern':'Modern cinema','camera.film35':'35 mm film','camera.film16':'16 mm film',
  'camera.documentary':'Documentary','camera.vintage':'Vintage cinema','camera.clean':'Clean digital',
  'lens.18':'18 mm · ultra wide','lens.24':'24 mm · wide','lens.35':'35 mm · natural',
  'lens.40':'40 mm · observational','lens.50':'50 mm · standard','lens.70_zoom':'70 mm · fixed zoom',
  'lens.75':'75 mm · portrait','lens.85':'85 mm · portrait','lens.135':'135 mm · telephoto',
  'lens.macro':'Macro','lens.anamorphic':'Anamorphic',
  'shot.wide':'Wide shot','shot.full':'Full body','shot.medium':'Medium shot','shot.close':'Close-up',
  'shot.extreme':'Extreme close-up','shot.low':'Low angle','shot.high':'High angle',
  'shot.over_shoulder':'Over the shoulder','shot.two_shot':'Two shot','shot.point_of_view':'Point of view',
  'shot.insert':'Insert · detail','shot.overhead':'Overhead','shot.dutch':'Dutch angle',
  'movement.static':'Locked off','movement.push':'Dolly in','movement.pull':'Dolly out',
  'movement.track':'Follow','movement.side':'Lateral tracking','movement.orbit':'Orbit',
  'movement.pan':'Pan','movement.tilt':'Tilt','movement.handheld':'Handheld','movement.crane':'Crane up',
  'focus.auto':'Track subject','focus.eyes':'Eyes','focus.object':'Object',
  'focus.deep':'Deep focus','focus.shallow':'Shallow depth','focus.rack':'Rack focus',
  'focus.foreground':'Sharp foreground','focus.background':'Sharp background',
  'focus.near_to_far':'Near to far','focus.far_to_near':'Far to near',
  'lighting.natural':'Soft daylight','lighting.golden':'Golden hour','lighting.window':'Side window light',
  'lighting.practical':'Practical lamps','lighting.rim':'Rim light','lighting.silhouette':'Silhouette',
  'lighting.lowkey':'Hard side light','lighting.neon':'Motivated neon','lighting.overcast':'Overcast exterior',
  'lighting.rembrandt':'Rembrandt triangle','lighting.butterfly':'Butterfly portrait',
  'lighting.top':'Hard overhead light','lighting.screen':'Screen glow','lighting.police':'Police lights',
  'lighting.moonlight':'Moonlight',
  'color.neutral':'Neutral','color.warm':'Warm cinema','color.cool':'Cool cinema',
  'color.teal_orange':'Teal and amber','color.desaturated':'Desaturated',
  'color.monochrome':'Black and white','color.pastel':'Pastel','color.vivid':'Vivid',
  'style.realism':'Cinematic realism','style.documentary':'Documentary',
  'style.commercial':'Commercial','style.film':'Film texture','style.animation3d':'3D animation',
  'style.animation2d':'2D animation','style.noir':'Noir','style.scifi':'Science fiction',
  'style.neon_thriller':'Neon thriller','style.intimate_drama':'Intimate drama',
  'style.epic_landscape':'Epic landscape','style.modern_noir':'Modern noir',
  'style.kinetic_action':'Kinetic action','style.warm_memory':'Warm memory',
  'style.product_luxury':'Luxury product','style.fantasy_3d':'Fantasy 3D animation',
  'style.graphic_2d':'Graphic 2D animation','style.observational_realism':'Observational realism',
  'acting.human_natural':'Human · natural','acting.human_expressive':'Human · expressive',
  'acting.animal_natural':'Animal · natural','acting.animal_humanlike':'Animal · humanlike',
  'acting.objective_subtext':'Stanislavski · objective','acting.listening_reaction':'Meisner · listening',
  'acting.physical_theatre':'Lecoq · movement','acting.comic_timing':'Comic timing',
  'acting.thriller':'Thriller · suppressed tension','acting.horror':'Horror · fear and shock',
  'acting.drama':'Drama · intimate truth','acting.heist':'Heist · controlled pressure',
};
const specEnById = {
  'camera.alexa35':'Super 35 · 27.99 × 19.22 mm · 4608 × 3164',
  'camera.sony_fx6':'Full frame · 10.2 effective MP · body 114 × 116 × 153 mm',
  'camera.ursa_cine':'Large format · 35.64 × 23.32 mm',
  'camera.sony_burano':'Full frame · 35.9 × 24 mm · PL / E',
  'camera.canon_c400':'Full frame · 6K · RF · 19.2 effective MP',
  'camera.alexa_mini_lf':'LF Open Gate · 36.70 × 25.54 mm · 4448 × 3096',
  'camera.red_v_raptor_x':'8K VV · global shutter · 40.96 × 21.60 mm',
  'camera.panavision_dxl2':'8K · 40.96 × 21.60 mm · 35 MP',
  'camera.documentary_rig':'Operator setup · natural shoulder stabilization',
  'lens.18':'18 mm · wide scene coverage','lens.40':'40 mm · moderate perspective',
  'lens.75':'75 mm · portrait perspective','lens.70_zoom':'70 mm · fixed zoom position',
};
const cameraLensDescriptionsRu = {
  'camera.alexa35': 'Чистый цифровой кадр Super 35 с мягкими светами и естественными тонами кожи.',
  'camera.sony_fx6': 'Лёгкий документальный характер: ясные детали, естественный контраст.',
  'camera.ursa_cine': 'Крупноформатный цифровой кадр с читаемой фактурой и сдержанным цветом.',
  'camera.sony_burano': 'Крупноформатный характер с точной композицией и мягкой передачей светов.',
  'camera.canon_c400': 'Ровный цифровой кадр с естественной кожей и ясными деталями героя.',
  'camera.alexa_mini_lf': 'Спокойная крупноформатная композиция, естественные полутона и бережные света.',
  'camera.red_v_raptor_x': 'Контрастный чёткий кадр для динамичного действия с читаемой географией.',
  'camera.panavision_dxl2': 'Широкая постановочная композиция с выверенным тональным разделением.',
  'camera.documentary_rig': 'Съёмка с плеча на уровне человека: живое следование и понятное пространство.',
  'camera.modern': 'Чистый современный цифровой характер и плавный переход в светах.',
  'camera.film35': 'Тонкая плёночная фактура 35 мм и мягкие яркие участки.',
  'camera.film16': 'Заметное мелкое зерно и органичная мягкость 16 мм.',
  'camera.documentary': 'Наблюдательная съёмка с естественной экспозицией и свободным кадром.',
  'camera.vintage': 'Сдержанная мягкость и органичный контраст старого кино.',
  'camera.clean': 'Чёткий цифровой кадр без нарочитой стилизации.',
  'lens.18': 'Очень широкий угол: больше пространства в кадре, без растяжения лиц у краёв.',
  'lens.24': 'Широкий угол с хорошо читаемой средой и пространственной глубиной.',
  'lens.35': 'Естественная перспектива: герой и окружающая среда вместе.',
  'lens.40': 'Умеренный угол для близкого наблюдения с сохранением контекста.',
  'lens.50': 'Уравновешенная перспектива между героем и фоном.',
  'lens.70_zoom': 'Фиксированное положение зума 70 мм: плотнее кадр и спокойнее фон.',
  'lens.75': 'Портретная перспектива с ясным лицом и мягким отделением фона.',
  'lens.85': 'Портретный ракурс с визуальным отделением героя от фона.',
  'lens.135': 'Узкий угол, который визуально сближает планы.',
  'lens.macro': 'Очень близкая деталь с малой глубиной резкости.',
  'lens.anamorphic': 'Широкий анаморфный характер с овальным боке и лёгким горизонтальным бликом.',
};
const p = (category, id, name, prompt, parameters, tone = '') => ({
  id: `${category}.${id}`, category, name, description: prompt, prompt,
  titleEn: titleEnById[`${category}.${id}`], descriptionEn: prompt,
  specEn: specEnById[`${category}.${id}`],
  descriptionRu: cameraLensDescriptionsRu[`${category}.${id}`],
  parameters, tags: [name.toLowerCase(), id.replaceAll('_', ' ')], tone, source: 'builtin',
});
const cameraMoveField = DIRECTOR_SCHEMA.categories.movement.find((field) => field.key === 'camera_move');
const movementLabel = (key, value) => DIRECTOR_SCHEMA.categories.movement
  .find((field) => field.key === key)?.options?.find((option) => option.value === value)?.label?.ru || '';
const cameraMovementPresets = (cameraMoveField?.options || []).map((move) => {
  const parameters = {
    movement: move.camera_path,
    camera_move: move.value,
    speed: move.speed,
    ...(move.direction !== 'none' ? { direction: move.direction } : {}),
  };
  const tone = move.category === 'Human Camera' ? 'slate'
    : move.category === 'Specials' ? 'violet'
      : move.category === 'Zoom/Lens' ? 'amber' : 'blue';
  return {
    ...p('movement', move.value, move.label.ru, move.prompt, parameters, tone),
    descriptionRu: `${move.label.ru}. Путь камеры: ${movementLabel('movement', move.camera_path)}. Скорость: ${movementLabel('speed', move.speed).toLowerCase()}.`,
    titleEn: move.label.en,
    tags: [move.category.toLowerCase(), move.label.en.toLowerCase(), move.value.replaceAll('_', ' ')],
    cameraMoveCategory: move.category,
  };
});
const withImage = (preset, path) => ({ ...preset, image: `./assets/${path}` });
// Old camera-look IDs remain readable in saved shots. New selections use the
// physical camera catalog; their thumbnails must show camera equipment.
const legacyCameraPhotoCredits = {
  'cameras/alexa-35.jpg': ['Rémi Simonnin · CC BY-SA 4.0', 'https://commons.wikimedia.org/wiki/File:Alexa_35_(3).jpg'],
  'cameras/sony-fx6.jpg': ['Hidalgo944 · CC BY-SA 4.0', 'https://commons.wikimedia.org/wiki/File:Sony_Cinema_line_FX6.jpg'],
  'cameras/ursa-cine-12k-lf.jpg': ['TurnOnTheNight · CC BY-SA 4.0', 'https://commons.wikimedia.org/wiki/File:Blackmagic_URSA_Cine_12K_LF_20250726122053.jpg'],
};
const legacyCamera = (preset, path) => ({
  ...withImage(preset, path), legacy: true,
  spec: 'Сохранённый пресет характера съёмки · выбери модель камеры выше',
  specEn: 'Saved capture look · choose a camera model above',
  ...(legacyCameraPhotoCredits[path]
    ? { imageCredit: legacyCameraPhotoCredits[path][0], imageSource: legacyCameraPhotoCredits[path][1] }
    : { imageCredit: 'TaleMondo · иллюстрация камеры; пресет не выбирает эту модель' }),
});
const lightingImage = (preset, path, descriptionRu) => ({ ...withImage(preset, path), descriptionRu });
// Keep old IDs readable in saved drafts without showing duplicate, image-less
// palette recipes next to the new photographed collection.
const legacyColorImage = {
  neutral: 'neutral_cafe', warm: 'amber_dusk', cool: 'blue_hour',
  teal_orange: 'teal_amber_rain', desaturated: 'mist_silver',
  monochrome: 'monochrome_geometry', pastel: 'pastel_garden', vivid: 'vivid_market',
};
const legacyColor = (...args) => ({
  ...p('color', ...args), legacy: true,
  image: PALETTE_CATALOG.find((entry) => entry.id === legacyColorImage[args[0]])?.image,
  descriptionRu: PALETTE_CATALOG.find((entry) => entry.id === legacyColorImage[args[0]])?.description,
});
export const DIRECTOR_CATEGORIES = [
  ['camera', 'Камера', 'Характер съёмки'], ['lens', 'Объектив', 'Перспектива и глубина'],
  ['shot', 'Кадр', 'Крупность и угол'], ['movement', 'Движение', 'Путь камеры'],
  ['focus', 'Фокус', 'Что остаётся резким'], ['lighting', 'Свет', 'Источники и контраст'],
  ['color', 'Цвет', 'Цветовой баланс'], ['style', 'Стиль', 'Визуальный язык'],
  ['acting', 'Актёрская игра', 'Мимика, диалог и голос'],
];
const categoryEnglish = {
  camera:['Camera','Capture character'], lens:['Lens','Perspective and depth'],
  shot:['Shot','Framing and angle'], movement:['Movement','Camera path'],
  focus:['Focus','What stays sharp'], lighting:['Lighting','Sources and contrast'],
  color:['Color','Color balance'], style:['Style','Visual language'],
  acting:['Acting','Expression, dialogue and voice'],
};
export function displayDirectorCategory(category, locale = 'ru') {
  const entry = DIRECTOR_CATEGORIES.find(([key]) => key === category);
  if (!entry) return { title: category, description: '' };
  const localized = String(locale).toLowerCase().startsWith('en') ? categoryEnglish[category] : entry.slice(1);
  return { title: localized[0], description: localized[1] };
}
const SHOT_FOCUS_DESCRIPTIONS_RU = {
  'shot.wide': 'Герой и пространство; сначала покажи, где происходит действие.',
  'shot.full': 'Герой с головы до ног; читаются поза и перемещение.',
  'shot.medium': 'Герой по пояс или чуть шире; жесты и обстановка в балансе.',
  'shot.close': 'Лицо крупно; акцент на реакции и эмоции.',
  'shot.extreme': 'Одна деталь занимает кадр и несёт смысл сцены.',
  'shot.low': 'Камера ниже героя и смотрит вверх.',
  'shot.high': 'Камера выше героя и смотрит вниз.',
  'shot.over_shoulder': 'Смотрим через плечо одного героя на другого.',
  'shot.two_shot': 'Два героя в одном кадре; видна их связь и дистанция.',
  'shot.point_of_view': 'Камера показывает то, что видит герой.',
  'shot.insert': 'Крупная врезка значимого предмета или действия.',
  'shot.overhead': 'Камера строго над сценой; видна геометрия пространства.',
  'shot.dutch': 'Горизонт намеренно наклонён для напряжения.',
  'focus.auto': 'Главный герой остаётся резким во время движения.',
  'focus.eyes': 'Глаза резкие, фон мягкий.',
  'focus.object': 'Выбранный предмет остаётся резким.',
  'focus.deep': 'Передний, средний и дальний планы читаются одновременно.',
  'focus.shallow': 'Герой резкий, фон мягко размыт.',
  'focus.rack': 'Резкость переходит с детали впереди на героя.',
  'focus.foreground': 'Резкая деталь впереди; дальний герой размыт.',
  'focus.background': 'Дальний герой резкий; ближний план размыт.',
  'focus.near_to_far': 'Сначала резкий ближний план, затем дальний.',
  'focus.far_to_near': 'Сначала резкий дальний план, затем ближний.',
};
export const DIRECTOR_PRESETS = [
  ...PALETTE_CATALOG.map((entry) => ({
    ...p('color', entry.id, entry.title, entry.prompt, entry.config, 'slate'),
    description: entry.description, image: entry.image,
    titleEn: entry.titleEn, descriptionEn: entry.descriptionEn,
    imageCredit: 'H3 Studio · оригинальная иллюстрация',
  })),
  { ...p('camera','alexa35','ARRI ALEXA 35','Clean digital cinema image with natural skin tones, gentle highlight rolloff and restrained contrast.',
      { reference_camera:'ARRI ALEXA 35', sensor_width_mm:27.99, sensor_height_mm:19.22, photosites:[4608,3164], sensor_type:'ALEV 4 CMOS', lens_mount:'LPL', dynamic_range_stops:17, capture:'digital', contrast:'natural' },'slate'),
    image:'./assets/cameras/alexa-35.jpg', spec:'Super 35 · 27,99 × 19,22 мм · 4608 × 3164',
    source:'https://www.arri.com/en/cine-systems/cine-cameras/legacy-cine-cameras/alexa-35',
    imageCredit:'Rémi Simonnin · CC BY-SA 4.0', imageSource:'https://commons.wikimedia.org/wiki/File:Alexa_35_(3).jpg' },
  { ...p('camera','sony_fx6','Sony FX6','Natural full-frame cinema image with clear detail and restrained documentary contrast.',
      { reference_camera:'Sony FX6', sensor_format:'full-frame', sensor_type:'35 mm full-frame', effective_megapixels:10.2, body_mm:[114,116,153], lens_mount:'Sony E', capture:'digital' },'blue'),
    image:'./assets/cameras/sony-fx6.jpg', spec:'Full frame · 10,2 Мп эфф. · корпус 114 × 116 × 153 мм',
    source:'https://www.sony.com/electronics/support/camcorders-interchangeable-lens-camcorders/ilme-fx6v/specifications',
    imageCredit:'Hidalgo944 · CC BY-SA 4.0', imageSource:'https://commons.wikimedia.org/wiki/File:Sony_Cinema_line_FX6.jpg' },
  { ...p('camera','ursa_cine','URSA Cine 12K LF','Large-format digital cinema image with detailed textures and restrained natural color.',
      { reference_camera:'Blackmagic URSA Cine 12K LF', sensor_width_mm:35.64, sensor_height_mm:23.32, lens_mount:'PL (interchangeable EF)', dynamic_range_stops:16, capture:'digital', sensor_format:'large format' },'amber'),
    image:'./assets/cameras/ursa-cine-12k-lf.jpg', spec:'Large format · 35,64 × 23,32 мм',
    source:'https://www.blackmagicdesign.com/products/blackmagicursacine/techspecs',
    imageCredit:'TurnOnTheNight · CC BY-SA 4.0', imageSource:'https://commons.wikimedia.org/wiki/File:Blackmagic_URSA_Cine_12K_LF_20250726122053.jpg' },
  { ...p('camera','sony_burano','Sony BURANO','Large-format digital cinema direction with composed full-frame perspective, natural skin tones and controlled highlight detail.',
      { reference_camera:'Sony BURANO', sensor_width_mm:35.9, sensor_height_mm:24, effective_megapixels:41.9, lens_mount:'PL / Sony E', dynamic_range_stops:16, capture:'digital', sensor_format:'full-frame' },'blue'),
    image:'./assets/cameras/sony-burano.jpg', spec:'Full frame · 35,9 × 24 мм · PL / E',
    source:'https://pro.sony/ue_US/products/digital-cinema-cameras/burano',
    imageCredit:'TurnOnTheNight · CC BY-SA 4.0', imageSource:'https://commons.wikimedia.org/wiki/File:Sony_CineAlta_BURANO_with_Sony_FE_24-70mm_F2.8_GM_II_20250726120012.jpg' },
  { ...p('camera','canon_c400','Canon EOS C400','Natural full-frame digital cinema direction with clear subject detail, restrained contrast and balanced skin tones.',
      { reference_camera:'Canon EOS C400', sensor_format:'full-frame', effective_megapixels:19.2, sensor_type:'back-illuminated stacked CMOS', lens_mount:'Canon RF', dynamic_range_stops:16, capture:'digital' },'slate'),
    image:'./assets/cameras/canon-c400.jpg', spec:'Full frame · 6K · RF · 19,2 Мп эфф.',
    source:'https://www.usa.canon.com/shop/p/eos-c400',
    imageCredit:'TaurusEmerald · CC BY-SA 4.0', imageSource:'https://commons.wikimedia.org/wiki/File:Canon_EOS_C400_Camera.jpg' },
  { ...p('camera','alexa_mini_lf','ARRI ALEXA Mini LF','Large-format cinematic direction: measured exposure, natural midtones, controlled highlights and calm image texture.',
      { reference_camera:'ARRI ALEXA Mini LF', sensor_format:'large format', sensor_width_mm:36.70, sensor_height_mm:25.54, photosites:[4448,3096], lens_mount:'LPL', capture:'digital', contrast:'natural', detail:'natural' },'slate'),
    image:'./assets/cameras/mini-lf-illustration.jpg', spec:'LF Open Gate · 36,70 × 25,54 мм · 4448 × 3096',
    source:'https://www.arri.com/en/cine-systems/cine-cameras/alexa-mini-lf',
    imageCredit:'H3 Studio · иллюстрация типа корпуса, не фото ARRI' },
  { ...p('camera','red_v_raptor_x','RED V-RAPTOR XL [X]','Precise large-format digital direction with clear fine detail, decisive framing and high-contrast action readability.',
      { reference_camera:'RED V-RAPTOR XL [X] 8K VV', sensor_format:'large format', sensor_width_mm:40.96, sensor_height_mm:21.60, capture:'digital', contrast:'high', detail:'crisp' },'blue'),
    image:'./assets/cameras/v-raptor-x-illustration.jpg', spec:'8K VV · global shutter · 40,96 × 21,60 мм',
    source:'https://docs.red.com/955-0227/955-0227_V1.7%20Rev-A%20RED%20PS%2C%20V-RAPTOR%20XL%20%5BX%5D%208K%20VV%20Operation%20Guide%20HTML/Content/B_TechSpecs/Specs_V-RAPTOR_XL%20%5BX%5D.htm',
    imageCredit:'H3 Studio · иллюстрация типа корпуса, не фото RED' },
  { ...p('camera','panavision_dxl2','Panavision Millennium DXL2','Large-format feature-film direction with composed scale, deliberate tonal separation and restrained cinematic texture.',
      { reference_camera:'Panavision Millennium DXL2', sensor_format:'large format', sensor_width_mm:40.96, sensor_height_mm:21.60, capture:'digital', contrast:'medium', detail:'natural' },'amber'),
    image:'./assets/cameras/dxl2-illustration.jpg', spec:'8K · 40,96 × 21,60 мм · 35 Мп',
    source:'https://www.panavision.com/camera-and-optics/dxl2',
    imageCredit:'H3 Studio · иллюстрация типа корпуса, не фото Panavision' },
  { ...p('camera','documentary_rig','Плечевая документальная','Observational shoulder-mounted camera direction: follow the action at human height with natural response and readable geography.',
      { capture:'documentary', stabilization:'natural', contrast:'natural', detail:'natural' },'slate'),
    image:'./assets/cameras/documentary-rig-illustration.jpg', spec:'Операторский сетап · естественная ручная стабилизация',
    imageCredit:'H3 Studio · оригинальная иллюстрация' },
  legacyCamera(p('camera','modern','Современное кино','Clean modern digital cinema image with natural highlight rolloff.',{capture:'digital',contrast:'natural'},'slate'), 'cameras/alexa-35.jpg'),
  legacyCamera(p('camera','film35','35-мм плёнка','Subtle 35 mm film texture, organic grain and gentle highlight rolloff.',{capture:'film35',grain:'subtle'},'amber'), 'cameras/mini-lf-illustration.jpg'),
  legacyCamera(p('camera','film16','16-мм плёнка','Visible fine grain and soft organic 16 mm film character.',{capture:'film16',grain:'fine'},'ochre'), 'cameras/documentary-rig-illustration.jpg'),
  legacyCamera(p('camera','documentary','Документальная','Observational documentary camera, natural exposure and unforced framing.',{capture:'documentary',stabilization:'natural'},'slate'), 'cameras/sony-fx6.jpg'),
  legacyCamera(p('camera','vintage','Винтажное кино','Vintage cinema image with restrained softness and organic contrast.',{capture:'vintage',contrast:'soft'},'sepia'), 'cameras/dxl2-illustration.jpg'),
  legacyCamera(p('camera','clean','Чистый digital','Clean digital image with crisp detail and restrained grading.',{capture:'digital',detail:'crisp'},'blue'), 'cameras/ursa-cine-12k-lf.jpg'),
  withImage(p('lens','24','24 мм · широкий','Wide 24 mm lens perspective with clear environment and believable spatial depth.',{focal_length_mm:24,look:'wide'},'blue'), 'lenses/lens-24.jpg'),
  withImage(p('lens','35','35 мм · средний','Natural 35 mm lens perspective with context around the subject.',{focal_length_mm:35,look:'natural'},'slate'), 'lenses/lens-35.jpg'),
  withImage(p('lens','50','50 мм · стандарт','Natural 50 mm lens perspective, balanced subject and environment.',{focal_length_mm:50,look:'standard'},'amber'), 'lenses/lens-50.jpg'),
  withImage(p('lens','85','85 мм · портрет','85 mm portrait lens look with compressed background and gentle subject separation.',{focal_length_mm:85,look:'portrait'},'violet'), 'lenses/lens-85.jpg'),
  withImage(p('lens','135','135 мм · теле','Long 135 mm lens look with compressed distance and narrow field of view.',{focal_length_mm:135,look:'telephoto'},'slate'), 'lenses/lens-135.jpg'),
  withImage(p('lens','macro','Макро','Macro lens look for a close detail with shallow spatial depth.',{look:'macro'},'green'), 'lenses/lens-macro.jpg'),
  withImage(p('lens','anamorphic','Анаморфный','Wide anamorphic cinema look with subtle horizontal flare and oval background bokeh.',{look:'anamorphic'},'amber'), 'lenses/lens-anamorphic.jpg'),
  { ...withImage(p('lens','18','18 мм · сверхширокий','Very wide 18 mm perspective for a spatially legible environment; keep people proportionate and avoid stretched faces at frame edges.',{focal_length_mm:18,look:'wide'},'blue'), 'lenses/lens-18.jpg'),
    spec:'18 мм · широкий охват сцены', source:'https://www.arri.com/en/cine-lenses/signature-lenses/signature-primes-zooms/signature-primes/technical-data-signature-prime', imageCredit:'H3 Studio · условное изображение объектива' },
  { ...withImage(p('lens','40','40 мм · наблюдательный','Natural 40 mm perspective: maintain an intimate human scale while preserving enough setting for spatial context.',{focal_length_mm:40,look:'natural'},'slate'), 'lenses/lens-40.jpg'),
    spec:'40 мм · умеренная перспектива', source:'https://www.arri.com/en/cine-lenses/signature-lenses/signature-primes-zooms/signature-primes/technical-data-signature-prime', imageCredit:'H3 Studio · условное изображение объектива' },
  { ...withImage(p('lens','75','75 мм · портретный','Portrait-oriented 75 mm perspective: isolate a readable face and maintain calm background separation without excessive blur.',{focal_length_mm:75,look:'portrait'},'amber'), 'lenses/lens-75.jpg'),
    spec:'75 мм · портретная перспектива', source:'https://www.arri.com/en/cine-lenses/signature-lenses/signature-primes-zooms/signature-primes/technical-data-signature-prime', imageCredit:'H3 Studio · условное изображение объектива' },
  { ...withImage(p('lens','70_zoom','70 мм · на зуме','Set the zoom lens to a fixed 70 mm field of view for this shot; keep framing steady and separate the subject from a quieter background.',{focal_length_mm:70,look:'telephoto'},'slate'), 'lenses/lens-70-zoom.jpg'),
    spec:'70 мм · статичная позиция зума', source:'https://www.zeiss.com/photonics-and-optics/us/cinematography/lenses/supreme-zoom-radiance-lenses.html', imageCredit:'H3 Studio · условное изображение зума' },
  withImage(p('shot','wide','Общий','Wide shot establishing the subject in its environment.',{framing:'wide',angle:'eye'},'blue'), 'shot-focus/shot-wide.jpg'),
  withImage(p('shot','full','В полный рост','Full-body framing; keep head and feet visible.',{framing:'full',angle:'eye'},'slate'), 'shot-focus/shot-full.jpg'),
  withImage(p('shot','medium','Средний','Medium shot, readable body language and surroundings.',{framing:'medium',angle:'eye'},'amber'), 'shot-focus/shot-medium.jpg'),
  withImage(p('shot','close','Крупный','Close-up of the subject with readable expression.',{framing:'close',angle:'eye'},'violet'), 'shot-focus/shot-close.jpg'),
  withImage(p('shot','extreme','Деталь','Extreme close-up of one meaningful detail.',{framing:'extreme_close',angle:'eye'},'ochre'), 'shot-focus/shot-extreme.jpg'),
  withImage(p('shot','low','Нижний ракурс','Low camera angle with coherent perspective.',{framing:'medium',angle:'low'},'amber'), 'shot-focus/shot-low.jpg'),
  withImage(p('shot','high','Верхний ракурс','High camera angle looking down with coherent perspective.',{framing:'medium',angle:'high'},'blue'), 'shot-focus/shot-high.jpg'),
  withImage(p('shot','over_shoulder','Через плечо','Over-the-shoulder composition with foreground and subject clearly separated.',{framing:'over_shoulder'},'slate'), 'shot-focus/shot-over-shoulder.jpg'),
  withImage(p('shot','two_shot','Два героя','Balanced two-shot: keep both subjects in the same composition with readable eyelines and spatial relationship.',{framing:'two_shot',angle:'eye'},'amber'), 'shot-focus/shot-two.jpg'),
  withImage(p('shot','point_of_view','От первого лица','Point-of-view shot from the active character; show what they see while preserving the established screen direction.',{framing:'point_of_view',angle:'eye'},'violet'), 'shot-focus/shot-pov.jpg'),
  withImage(p('shot','insert','Врезка · деталь','Insert shot of the named story-relevant object or action, with clear scale and continuity to the surrounding scene.',{framing:'insert',angle:'eye'},'ochre'), 'shot-focus/shot-insert.jpg'),
  withImage(p('shot','overhead','Строго сверху','Overhead shot looking vertically downward; reveal the geometry and positions of subjects in the space.',{framing:'wide',angle:'overhead'},'blue'), 'shot-focus/shot-overhead.jpg'),
  withImage(p('shot','dutch','Наклонный горизонт','Deliberate Dutch angle: tilt the camera horizon visibly while keeping the subject and action legible.',{framing:'medium',angle:'dutch'},'green'), 'shot-focus/shot-dutch.jpg'),
  p('movement','static','Статичная','Locked-off camera; preserve stable framing through the shot.',{movement:'static',speed:'none'},'slate'),
  p('movement','push','Наезд','Slow controlled dolly-in toward the primary subject while keeping focus on them.',{movement:'dolly',direction:'forward',speed:'slow',subject_lock:true},'amber'),
  p('movement','pull','Отъезд','Slow controlled dolly-out revealing more of the environment.',{movement:'dolly',direction:'backward',speed:'slow'},'blue'),
  p('movement','track','Следование','Smooth tracking shot following the primary subject at a consistent distance.',{movement:'tracking',subject_lock:true,stabilization:'smooth'},'violet'),
  p('movement','side','Боковое следование','Smooth lateral tracking alongside the subject.',{movement:'truck',direction:'side',subject_lock:true},'blue'),
  p('movement','orbit','Орбита','Slow camera arc around the primary subject with consistent scale.',{movement:'orbit',speed:'slow',subject_lock:true},'violet'),
  p('movement','pan','Панорама','Controlled camera pan revealing the scene from left to right.',{movement:'pan',direction:'right',speed:'slow'},'amber'),
  p('movement','tilt','Наклон','Controlled upward camera tilt revealing the subject and setting.',{movement:'tilt',direction:'up',speed:'slow'},'ochre'),
  p('movement','handheld','Ручная камера','Subtle handheld movement with believable human inertia.',{movement:'handheld',stabilization:'natural'},'slate'),
  p('movement','crane','Кран вверх','Smooth rising crane movement revealing the wider space.',{movement:'crane',direction:'up'},'blue'),
  ...cameraMovementPresets,
  withImage(p('focus','auto','По герою','Keep the primary subject clearly in focus throughout.',{target:'subject',behavior:'tracking'},'violet'), 'shot-focus/focus-auto.jpg'),
  withImage(p('focus','eyes','По глазам','Keep the visible subject eyes sharp; background falls softly out of focus.',{target:'eyes',depth:'shallow'},'violet'), 'shot-focus/focus-eyes.jpg'),
  withImage(p('focus','object','По предмету','Keep the named foreground object sharp and readable.',{target:'object'},'amber'), 'shot-focus/focus-object.jpg'),
  withImage(p('focus','deep','Глубокий','Deep focus with foreground, subject and background legible.',{depth:'deep'},'blue'), 'shot-focus/focus-deep.jpg'),
  withImage(p('focus','shallow','Малая ГРИП','Shallow depth of field with a sharp subject and soft background.',{depth:'shallow'},'ochre'), 'shot-focus/focus-shallow.jpg'),
  withImage(p('focus','rack','Перевод фокуса','Deliberate rack focus from foreground detail to primary subject.',{behavior:'rack',from:'foreground',to:'subject'},'green'), 'shot-focus/focus-rack.jpg'),
  withImage(p('focus','foreground','Резкий передний план','Lock focus on the named foreground detail; keep the more distant subject deliberately soft.',{target:'object',depth:'shallow',behavior:'locked'},'blue'), 'shot-focus/focus-foreground.jpg'),
  withImage(p('focus','background','Резкий дальний план','Keep the distant main subject sharp while the near foreground remains visibly soft and frames the view.',{target:'subject',depth:'shallow',behavior:'locked'},'amber'), 'shot-focus/focus-background.jpg'),
  withImage(p('focus','near_to_far','С ближнего на дальний','Begin with the named foreground detail sharp, then deliberately pull focus to the subject behind it.',{behavior:'rack',from:'foreground',to:'background',depth:'shallow'},'ochre'), 'shot-focus/focus-foreground-to-subject.jpg'),
  withImage(p('focus','far_to_near','С дальнего на ближний','Begin with the distant subject sharp, then deliberately pull focus to the named foreground subject or object.',{behavior:'rack',from:'background',to:'foreground',depth:'shallow'},'violet'), 'shot-focus/focus-background-to-subject.jpg'),
  lightingImage(p('lighting','natural','Мягкий день','Broad diffused daylight wraps the subject, with natural shadow falloff and gentle ambient fill.',{source:'daylight',softness:'soft',contrast:'natural',fill:'medium',temperature:'neutral'},'blue'), 'lighting/natural.jpg', 'Рассеянный дневной свет мягко освещает героя; тени естественные.'),
  lightingImage(p('lighting','golden','Золотой час','Low warm sun grazes the subject from one side; preserve long readable shadows and a gentle warm edge.',{source:'sun',direction:'side',softness:'medium',temperature:'warm',contrast:'medium',fill:'low'},'amber'), 'lighting/golden.jpg', 'Низкое тёплое солнце сбоку и длинные мягкие тени.'),
  lightingImage(p('lighting','window','Боковой из окна','The window motivates a soft side key; let light fall naturally across the face into a darker room.',{source:'window',direction:'side',softness:'soft',contrast:'medium',fill:'low'},'slate'), 'lighting/window.jpg', 'Мягкий свет из окна сбоку; дальняя сторона лица темнее.'),
  lightingImage(p('lighting','practical','Практические лампы','Visible practical lamps motivate warm pools of light and believable spill without flattening the shadows.',{source:'practical',temperature:'warm',softness:'soft',contrast:'medium',fill:'low'},'amber'), 'lighting/practical.jpg', 'Лампы видны в кадре и создают тёплые световые пятна.'),
  lightingImage(p('lighting','rim','Контровой контур','A strong back edge outlines the subject against the background; retain enough face detail to read expression.',{source:'backlight',direction:'back',softness:'hard',rim:true,fill:'low',contrast:'high'},'ochre'), 'lighting/rim.jpg', 'Свет сзади обводит контур героя; лицо остаётся читаемым.'),
  lightingImage(p('lighting','silhouette','Чистый силуэт','Bright motivated backlight makes a legible subject silhouette while the front remains almost unlit.',{source:'backlight',direction:'back',softness:'hard',fill:'low',key:'low',contrast:'high'},'violet'), 'lighting/silhouette.jpg', 'Яркий свет сзади; спереди герой почти не освещён.'),
  lightingImage(p('lighting','lowkey','Жёсткий боковой','A narrow hard side key sculpts the face; keep the opposite side dark with very little fill.',{source:'practical',direction:'side',softness:'hard',key:'low',fill:'low',contrast:'high'},'blue'), 'lighting/lowkey.jpg', 'Узкий боковой свет лепит лицо, другая сторона в тени.'),
  lightingImage(p('lighting','neon','Неоновый мотив','Visible neon signs motivate controlled magenta and cyan spill on wet surfaces and the subject.',{source:'neon',direction:'side',softness:'medium',contrast:'medium',saturation:'controlled',fill:'low'},'violet'), 'lighting/neon.jpg', 'Неоновые вывески окрашивают героя и мокрые поверхности.'),
  lightingImage(p('lighting','overcast','Пасмурный экстерьер','Cloud cover acts as a broad source: soft, even illumination with gentle shadows and natural color.',{source:'overcast',softness:'soft',contrast:'low',fill:'high',temperature:'cool'},'slate'), 'lighting/overcast.jpg', 'Облака дают ровный мягкий свет и слабые тени.'),
  lightingImage(p('lighting','rembrandt','Треугольник света','A controlled three-quarter side key leaves a small illuminated triangle on the far cheek; use restrained fill.',{source:'practical',direction:'side',softness:'medium',key:'medium',fill:'low',contrast:'high'},'amber'), 'lighting/rembrandt.jpg', 'Боковой ключ оставляет небольшой световой треугольник на щеке.'),
  lightingImage(p('lighting','butterfly','Фронтальный портрет','A broad high frontal key illuminates both eyes and creates a soft compact shadow beneath the nose and chin.',{source:'practical',direction:'front',softness:'soft',key:'high',fill:'medium',contrast:'low'},'slate'), 'lighting/butterfly.jpg', 'Мягкий фронтальный свет и короткая тень под носом.'),
  lightingImage(p('lighting','top','Жёсткий верхний','A single motivated overhead source creates a tight pool of hard light; surroundings fall into shadow.',{source:'practical',direction:'top',softness:'hard',key:'high',fill:'low',contrast:'high'},'ochre'), 'lighting/top.jpg', 'Один верхний источник выделяет героя, фон уходит в тень.'),
  // Night light presets (screen glow, police light sweeps, moonlight): no photo yet.
  { ...p('lighting','screen','Свет экрана','The only key is a monitor or TV screen in front of the subject: a cool soft glow on the face that flickers with the picture; the room behind falls into blue-grey darkness.',{source:'practical',direction:'front',softness:'soft',temperature:'cool',key:'low',fill:'low',contrast:'high'},'blue'), descriptionRu: 'Единственный ключ — экран перед героем: холодное мягкое свечение на лице мерцает вместе с картинкой, комната позади уходит в сине-серую тьму.' },
  { ...p('lighting','police','Мигалки','Off-screen police lights sweep alternating red and blue across faces and wet surfaces in a steady rhythm; between sweeps the scene drops back to dim tungsten street light.',{source:'neon',direction:'side',softness:'hard',saturation:'high',key:'medium',fill:'low',contrast:'high',reference_image:false},'violet'), descriptionRu: 'Мигалки за кадром попеременно заливают лица и мокрые поверхности красным и синим; между вспышками — тусклый тёплый уличный свет.' },
  { ...p('lighting','moonlight','Лунный свет','Cool moonlight through a window lays a soft blue-silver key and the shadow of the window frame across the subject; the room stays dark with faint fill, and the eyes still catch a highlight.',{source:'window',direction:'side',softness:'medium',temperature:'cool',key:'low',fill:'low',contrast:'high'},'slate'), descriptionRu: 'Холодный лунный свет из окна: мягкий серебристо-синий ключ и тень рамы на герое, комната тёмная, в глазах остаётся блик.' },
  legacyColor('neutral','Нейтральный','Natural balanced colors without strong tint.',{temperature:'neutral',saturation:'natural',contrast:'medium',swatches:['#161d24','#34414c','#60717b','#9ba9ad','#c5b9a4','#e2d3ba','#f5e9d7','#3f5259']},'slate'),
  legacyColor('warm','Тёплый кино','Warm amber highlights and restrained earth-tone shadows.',{temperature:'warm',saturation:'moderate',contrast:'medium',swatches:['#201b20','#4a322c','#7b4d35','#aa6940','#d69456','#efb978','#f1d2a2','#ede0c9']},'amber'),
  legacyColor('cool','Холодный кино','Cool blue shadows and restrained neutral skin tones.',{temperature:'cool',saturation:'moderate',contrast:'medium',swatches:['#111925','#1c3045','#31506a','#50728a','#7894a4','#a8b8bf','#d3d6d1','#e4d6c6']},'blue'),
  legacyColor('teal_orange','Бирюза и янтарь','Controlled teal shadows and warm amber highlights without oversaturation.',{temperature:'split',saturation:'moderate',contrast:'medium',swatches:['#10262c','#1b4450','#2a6670','#4d8d90','#bd6a3b','#de8c4d','#f0b46d','#f4d5aa']},'cyan'),
  legacyColor('desaturated','Приглушённый','Low-saturation color grade with preserved skin and subject readability.',{saturation:'low',contrast:'medium',swatches:['#24282d','#424b4d','#66716d','#89938a','#ada99b','#b8aa96','#d0c0ab','#e0d3c1']},'slate'),
  legacyColor('monochrome','Чёрно-белый','Monochrome tonal image with clear luminance separation.',{saturation:'none',contrast:'medium',swatches:['#111111','#2d2d2d','#474747','#636363','#888888','#aaaaaa','#d0d0d0','#f0f0f0']},'mono'),
  legacyColor('pastel','Пастельный','Soft pastel palette with gentle contrast and muted highlights.',{saturation:'low',contrast:'low',swatches:['#695d70','#9b8399','#c5a6b3','#e0bfc5','#e9d5cb','#bfc9c3','#a9bac2','#f1e8df']},'rose'),
  legacyColor('vivid','Яркий','Vivid controlled palette with subject colors kept coherent.',{saturation:'high',contrast:'medium',swatches:['#192a2b','#245c69','#2a8b8d','#b64c58','#e17642','#efb845','#cfda56','#ece6c5']},'green'),
  p('style','realism','Кинореализм','Photorealistic cinematic rendering with stable material and anatomy.',{rendering:'photoreal'},'slate'),
  p('style','documentary','Документальный','Natural documentary visual treatment, restrained and observational.',{rendering:'documentary'},'blue'),
  p('style','commercial','Рекламный','Polished commercial image with precise product and material detail.',{rendering:'commercial'},'amber'),
  p('style','film','Плёночный','Organic film texture with restrained color and soft highlight rolloff.',{rendering:'film'},'ochre'),
  p('style','animation3d','3D анимация','Coherent stylized 3D animation with stable designed forms.',{rendering:'animation3d'},'violet'),
  p('style','animation2d','2D анимация','Coherent drawn 2D animation with stable linework and flat colors.',{rendering:'animation2d'},'rose'),
  p('style','noir','Нуар','Cinematic noir treatment with hard shadow geometry and restrained color.',{rendering:'noir'},'mono'),
  p('style','scifi','Научная фантастика','Designed science-fiction visual world with consistent materials and lighting.',{rendering:'scifi'},'cyan'),
  // Original art-direction recipes. These are H3 prompt cues, not hardware profiles or GPU nodes.
  p('style','neon_thriller','Неоновый триллер','Grounded night thriller: motivated neon and street practicals, wet-surface reflections, restrained cyan shadows and magenta accents; hold spatial continuity through deliberate tracking and clean eyelines.',
    {rendering:'photoreal',genre:'thriller',lighting:'motivated_neon',palette:['cyan','magenta','near_black'],camera_grammar:'deliberate_tracking',contrast:'high'},'violet'),
  p('style','intimate_drama','Камерная драма','Intimate dramatic scene: natural skin tones, soft side light, close readable expressions, gentle background separation and restrained camera motion; preserve subtle acting beats.',
    {rendering:'photoreal',genre:'drama',lighting:'soft_side',palette:['neutral','warm_skin'],camera_grammar:'restrained_close',contrast:'medium'},'rose'),
  p('style','epic_landscape','Эпический простор','Epic environmental composition: show the subject small against a coherent large landscape, use broad atmospheric light and a measured rising reveal while keeping scale believable.',
    {rendering:'photoreal',genre:'epic',lighting:'atmospheric',palette:['earth','sky'],camera_grammar:'wide_reveal',subject_scale:'small_in_frame'},'blue'),
  p('style','modern_noir','Современный нуар','Modern noir: strong motivated side light, deep but legible shadows, precise silhouettes and limited color; use stillness or one controlled push-in to build tension.',
    {rendering:'photoreal',genre:'noir',lighting:'hard_side',palette:['charcoal','single_accent'],camera_grammar:'controlled_push',contrast:'high'},'mono'),
  p('style','kinetic_action','Динамичный экшен','Readable kinetic action: follow the main subject with coherent screen direction, energetic but smooth camera movement, short visual beats and clear geography; avoid random shake.',
    {rendering:'photoreal',genre:'action',lighting:'motivated',camera_grammar:'subject_tracking',motion_energy:'high',continuity:'screen_direction'},'amber'),
  p('style','warm_memory','Тёплое воспоминание','Warm memory sequence: soft late-afternoon light, muted amber and cream tones, gentle organic texture, slow observation and natural imperfect moments without excessive blur.',
    {rendering:'photoreal',genre:'memory',lighting:'soft_warm',palette:['amber','cream','muted_brown'],camera_grammar:'slow_observational',texture:'subtle_grain'},'ochre'),
  p('style','product_luxury','Премиальный продукт','Premium product film: clean controlled reflections, readable material detail, deliberate macro-to-medium framing, quiet camera arcs and restrained high-contrast color.',
    {rendering:'photoreal',genre:'product',lighting:'controlled_reflections',camera_grammar:'macro_to_medium',detail:'material',contrast:'controlled_high'},'slate'),
  p('style','fantasy_3d','Сказочная 3D-анимация','Coherent stylized 3D fantasy animation: designed character silhouettes, tactile materials, soft luminous world lighting and expressive but stable motion across the shot.',
    {rendering:'animation3d',genre:'fantasy',lighting:'soft_luminous',camera_grammar:'expressive_stable',materials:'tactile_stylized'},'violet'),
  p('style','graphic_2d','Графичная 2D-анимация','Graphic 2D animation: consistent hand-drawn contours, deliberate limited palette, clear silhouette poses, flat color planes and readable motion timing without unintended 3D depth.',
    {rendering:'animation2d',genre:'graphic',palette:['limited'],camera_grammar:'clear_silhouettes',linework:'consistent',motion_timing:'readable'},'rose'),
  p('style','observational_realism','Наблюдательный реализм','Observational realism: naturally motivated available light, patient human-scale framing, ordinary environment detail and unobtrusive camera movement; keep the action candid and believable.',
    {rendering:'photoreal',genre:'observational',lighting:'available',camera_grammar:'patient_human_scale',contrast:'natural'},'green'),
  p('acting','human_natural','Человек · естественно','Human characters act with readable but natural facial expression, body language and dialogue. Match each voice to the character age and identity; make emotional shifts audible through pacing, pitch and intonation without random voice swaps.',
    {cast:'human',performance:'natural',dialogue:'natural',voice_casting:'match_character'},'blue'),
  p('acting','human_expressive','Человек · выразительно','Human characters deliver distinct, emotionally responsive dialogue. Shape anticipation, emphasis, pauses and release in each spoken line; keep each speaker voice stable and appropriate to age and identity.',
    {cast:'human',performance:'dramatic',dialogue:'expressive',voice_casting:'distinct'},'rose'),
  p('acting','animal_natural','Животное · естественно','Animal characters show species-appropriate expression, posture, ears, eyes, muzzle and movement. Use natural animal vocalizations, never a human adult voice unless the story explicitly asks for speech.',
    {cast:'animal',performance:'natural',dialogue:'natural',voice_casting:'match_character'},'green'),
  p('acting','animal_humanlike','Животное · как человек','The animal intentionally performs humanlike dramatic beats while retaining its species anatomy and scale. If it speaks in the story, give it its own stable character voice with emotional intonation; do not assign a random adult voice or copy another speaker.',
    {cast:'animal',performance:'anthropomorphic',dialogue:'expressive',voice_casting:'distinct'},'amber'),
  p('acting','objective_subtext','Станиславский · цель','Act from a concrete objective in the given circumstances. Let the character pursue what they want through playable actions; show subtext through restrained pauses, gaze and changes of tactic rather than explanatory gestures. Shape spoken lines by the same objective.',
    {cast:'mixed',performance:'natural',dialogue:'natural',voice_casting:'distinct'},'blue'),
  p('acting','listening_reaction','Мейснер · слушание','Prioritize listening to the scene partner. Let a visible or audible cue land first, then allow a specific reaction before the next line or movement. Keep reactions spontaneous, grounded and continuous from beat to beat.',
    {cast:'mixed',performance:'natural',dialogue:'expressive',voice_casting:'distinct'},'rose'),
  p('acting','physical_theatre','Лекок · движение','Build the performance from clear physical action, weight, balance, rhythm and spatial relationship. Let gesture lead vocal energy while keeping the character anatomy and scale consistent. Use controlled stillness between actions.',
    {cast:'mixed',performance:'dramatic',dialogue:'expressive',voice_casting:'distinct'},'amber'),
  p('acting','comic_timing','Комедийный ритм','Play a clear intention with precise setup, expectation, interruption and reaction. Hold the beat before the payoff; keep faces and voices truthful, with no continuous mugging or random exaggeration.',
    {cast:'mixed',performance:'comedic',dialogue:'natural',voice_casting:'distinct'},'green'),
  p('acting','thriller','Триллер · скрытое напряжение','Play controlled suspicion. Start with ordinary behavior; let small delays, withheld eye contact, breath changes and clipped replies reveal mounting danger. Build tension across beats without constant panic or shouting.',
    {cast:'mixed',performance:'dramatic',dialogue:'quiet',voice_casting:'distinct'},'violet'),
  p('acting','horror','Хоррор · страх и шок','Let the character notice an anomaly, doubt it, then recognize the threat. Use a precise progression from stillness and shallow breath to involuntary reaction; reserve the strongest vocal and physical burst for the true shock beat. Recover unevenly afterward.',
    {cast:'mixed',performance:'dramatic',dialogue:'expressive',voice_casting:'distinct'},'mono'),
  p('acting','drama','Драма · близкая правда','Play an intimate conflict through subtext, listening and restrained shifts of intention. Let breath and pauses carry emotion before words; keep dialogue clear, personal and free of theatrical overstatement.',
    {cast:'mixed',performance:'natural',dialogue:'quiet',voice_casting:'distinct'},'rose'),
  p('acting','heist','Ограбление · под давлением','Play professional composure under increasing pressure. Keep gestures economical and dialogue low and specific; mark each new risk with a brief processing beat, then a decisive adjustment in tempo and attention.',
    {cast:'mixed',performance:'natural',dialogue:'quiet',voice_casting:'distinct'},'amber'),
].map((preset) => preset.category === 'movement' && !preset.parameters.camera_move ? { ...preset, legacy: true } : preset)
 .map((preset) => SHOT_FOCUS_DESCRIPTIONS_RU[preset.id]
  ? { ...preset, descriptionRu: SHOT_FOCUS_DESCRIPTIONS_RU[preset.id] }
  : preset);
export const DIRECTOR_CATEGORY_KEYS = Object.fromEntries(DIRECTOR_CATEGORIES.map(([key, name]) => [key, name]));
// Russian card descriptions for presets whose prompt is English (the prompt itself stays English for H3).
const DESCRIPTION_RU = {
 "movement.static": "Камера неподвижна, кадр стабилен весь план.",
 "movement.push": "Медленный плавный наезд на героя, фокус удерживается на нём.",
 "movement.pull": "Медленный плавный отъезд, постепенно открывается окружение.",
 "movement.track": "Плавное следование за героем на постоянной дистанции.",
 "movement.side": "Плавное боковое движение рядом с героем.",
 "movement.orbit": "Медленный облёт вокруг героя с неизменным масштабом.",
 "movement.pan": "Контролируемая панорама слева направо, раскрывающая сцену.",
 "movement.tilt": "Контролируемый наклон камеры вверх: открываются герой и место.",
 "movement.handheld": "Лёгкая ручная камера с естественной человеческой инерцией.",
 "movement.crane": "Плавный подъём крана, открывающий широкое пространство.",
 "style.realism": "Фотореалистичная кинокартинка со стабильными материалами и анатомией.",
 "style.documentary": "Естественная документальная подача, сдержанная и наблюдательная.",
 "style.commercial": "Отполированная рекламная картинка с точной детализацией продукта и материалов.",
 "style.film": "Органичная плёночная фактура, сдержанный цвет и мягкий уход светов.",
 "style.animation3d": "Цельная стилизованная 3D-анимация со стабильными формами.",
 "style.animation2d": "Цельная рисованная 2D-анимация: стабильный контур и плоские цвета.",
 "style.noir": "Кинонуар: жёсткая геометрия теней и сдержанный цвет.",
 "style.scifi": "Продуманный научно-фантастический мир с единой логикой материалов и света.",
 "style.neon_thriller": "Ночной триллер: мотивированный неон и уличные источники, мокрые отражения, циановые тени и маджента-акценты; осознанное ведение и ясная геометрия взглядов.",
 "style.intimate_drama": "Камерная драма: естественная кожа, мягкий боковой свет, читаемые крупные планы, деликатное отделение фона и сдержанная камера.",
 "style.epic_landscape": "Эпический простор: маленький герой в большом цельном пейзаже, широкий атмосферный свет и размеренное раскрытие вверх.",
 "style.modern_noir": "Современный нуар: сильный мотивированный боковой свет, глубокие, но читаемые тени, точные силуэты и ограниченная палитра.",
 "style.kinetic_action": "Читаемый экшен: следование за героем с понятным направлением в кадре, энергичная, но плавная камера, ясная география без случайной тряски.",
 "style.warm_memory": "Тёплое воспоминание: мягкий предвечерний свет, приглушённые янтарные и кремовые тона, лёгкая органичная фактура, медленное наблюдение.",
 "style.product_luxury": "Премиальный продукт: чистые контролируемые блики, читаемые материалы, переход от макро к среднему плану, спокойные дуги камеры.",
 "style.fantasy_3d": "Сказочная 3D-анимация: продуманные силуэты, тактильные материалы, мягкое светящееся освещение мира и выразительное, но стабильное движение.",
 "style.graphic_2d": "Графичная 2D-анимация: ровный рисованный контур, ограниченная палитра, ясные позы-силуэты, плоские цветовые плоскости без лишней 3D-глубины.",
 "style.observational_realism": "Наблюдательный реализм: естественный доступный свет, кадр на уровне человека, обычные детали среды и незаметная камера."
};
export function displayDirectorPreset(preset, locale = 'ru') {
  if (!preset) return { title: '', description: '', spec: '' };
  const english = String(locale).toLowerCase().startsWith('en');
  return {
    title: english ? (preset.titleEn || preset.name) : preset.name,
    description: (preset.category === 'movement' && preset.legacy ? (english ? 'Saved preset. ' : 'Прежний пресет. ') : '') +
      (english ? (preset.descriptionEn || preset.prompt || preset.description || '') :
        (preset.descriptionRu || DESCRIPTION_RU[preset.id] || preset.description || '')),
    spec: english ? (preset.specEn || preset.spec || '') : (preset.spec || ''),
  };
}
export function getPreset(id, custom = []) { return [...DIRECTOR_PRESETS, ...custom].find((item) => item.id === id); }
export function validateDirectorState(state = {}, custom = [], mode = '') {
  const warnings = [];
  const movement = getPreset(state.movement, custom)?.parameters?.movement;
  const focus = getPreset(state.focus, custom)?.parameters;
  if (movement === 'static' && /handheld|tracking|orbit|dolly|crane/.test(getPreset(state.camera, custom)?.prompt || '')) warnings.push('Статичная камера противоречит выбранному характеру съёмки.');
  if (focus?.depth === 'deep' && getPreset(state.lens, custom)?.parameters?.look === 'macro') warnings.push('Глубокий фокус и макрообъектив могут конфликтовать.');
  if (getPreset(state.lens, custom)?.parameters?.look === 'macro' && getPreset(state.shot, custom)?.parameters?.framing === 'wide') warnings.push('Макрообъектив и общий план противоречат друг другу.');
  const rendering = getPreset(state.style, custom)?.parameters?.rendering;
  if ((mode === 'realistic' && ['animation3d', 'animation2d'].includes(rendering)) ||
      (mode === 'animation3d' && ['animation2d', 'photoreal'].includes(rendering)) ||
      (mode === 'animation2d' && ['animation3d', 'photoreal'].includes(rendering))) warnings.push('Стиль противоречит выбранному сценарию генерации.');
  return warnings;
}
export function compileDirectorState(prompt, state = {}, custom = [], cameraOverrides = {}) {
  const text = String(prompt ?? '').replace(/\n\nDIRECTOR STATE — apply only where compatible with the described action and reference roles:\n[^]*?(?=\n\n|$)/, '').trim();
  if (!text) return text;
  // H3 reads English only: the Russian card titles (Камера, Свет, Актёрская игра…) reached 34 of 109 GPU prompts by 31.10.
  const lines = DIRECTOR_CATEGORIES.flatMap(([key]) => {
    const label = categoryEnglish[key][0];
    const preset = getPreset(state[key], custom);
    if (preset?.category !== key) return [];
    const speedOverride = key === 'movement' && ['slow', 'medium', 'fast'].includes(cameraOverrides.speed)
      ? `\nMovement speed: ${cameraOverrides.speed}; this overrides any speed wording in the selected movement preset.` : '';
    return [`${label}: ${preset.prompt}${speedOverride}`];
  });
  return lines.length ? `${text}\n\nDIRECTOR STATE — apply only where compatible with the described action and reference roles:\n${lines.join('\n')}` : text;
}
