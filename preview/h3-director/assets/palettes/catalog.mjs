// Original Studio palette scenes. Swatches are sampled from the corresponding
// generated preview image, while grading parameters are editorial directions.
// They are not measured camera LUTs or native H3 color-grading node settings.
const paletteEnglish = {
  neutral_cafe: ['Natural light', 'Balanced charcoal, cream and wood tones without a color cast.'],
  amber_dusk: ['Amber dusk', 'Desert sunset with dark earth tones and localized amber highlights.'],
  blue_hour: ['Blue hour', 'Cool predawn city with warm practical lights retained.'],
  teal_amber_rain: ['Teal and amber', 'Rainy night with teal depth and amber streetlights.'],
  mist_silver: ['Silver mist', 'Muted pine greens and cool silver-gray fog.'],
  monochrome_geometry: ['Monochrome geometry', 'Clean tonal structure with readable shadows and no color.'],
  pastel_garden: ['Pastel garden', 'Soft garden light, olive midtones and gentle floral accents.'],
  vivid_market: ['Vivid market', 'Bright fruit against teal structure and warm sand.'],
  burgundy_velvet: ['Burgundy velvet', 'Deep burgundy interior with localized golden light.'],
  sage_coast: ['Sage coast', 'Calm grasses, sand and a muted sea sky.'],
  electric_violet: ['Electric violet', 'Contrasting stage light in violet, pink and deep black.'],
  peach_modern: ['Peach and azure', 'Sunny architecture with peach walls and turquoise water.'],
};
const palette = (id, title, image, description, swatches, config, prompt) => ({
  id, title, titleEn: paletteEnglish[id][0], image: `./assets/palettes/${image}.jpg`,
  description, descriptionEn: paletteEnglish[id][1],
  swatches, config: { ...config, swatches }, prompt,
  provenance: 'H3 Studio original generated preview; swatches sampled locally',
});

export const PALETTE_CATALOG = [
  palette('neutral_cafe', 'Естественный свет', 'neutral-cafe',
    'Сбалансированные серые, кремовые и древесные тона без цветового сдвига.',
    ['#18150b','#282417','#473927','#565957','#797976','#878785','#aaa9a7','#c8c9c8'],
    { temperature:'neutral', saturation:'natural', contrast:'medium', shadows:'neutral charcoal', highlights:'soft warm white' },
    'Natural balanced color grade: neutral charcoal shadows, clean warm-white highlights, restrained wood and skin tones; retain realistic exposure and texture.'),
  palette('amber_dusk', 'Янтарный закат', 'amber-motel',
    'Пустынный закат: тёмная земля и янтарные блики.',
    ['#27180b','#472a17','#4a332a','#57382a','#573c34','#c8885b','#d89665','#f5ab68'],
    { temperature:'warm', saturation:'moderate', contrast:'medium', shadows:'deep warm brown', highlights:'amber peach' },
    'Warm dusk grade with deep earthy-brown shadows and amber-peach highlights; keep subjects readable and avoid uniform orange tint.'),
  palette('blue_hour', 'Синий час', 'blue-bridge',
    'Холодный предрассветный город с сохранёнными тёплыми источниками.',
    ['#081419','#162a38','#1b3c55','#264966','#38668b','#3c6c94','#3e729a','#447aa4'],
    { temperature:'cool', saturation:'moderate', contrast:'medium', shadows:'deep navy', highlights:'steel blue' },
    'Predawn blue-hour grade: navy shadows and steel-blue ambient light; preserve localized warm practical lights instead of turning every object blue.'),
  palette('teal_amber_rain', 'Бирюза и янтарь', 'teal-alley',
    'Дождливая ночь: бирюзовая глубина и янтарный свет фонарей.',
    ['#060705','#160c04','#051518','#271506','#482406','#783a05','#02637b','#c76605'],
    { temperature:'split', saturation:'moderate', contrast:'high', shadows:'dark teal', highlights:'amber practical lights' },
    'Rainy night split-tone grade: dark teal shadows and localized amber practical highlights, reflected on wet surfaces; preserve believable skin colors.'),
  palette('mist_silver', 'Серебряный туман', 'mist-forest',
    'Приглушённые хвойные зелёные и прохладный серый туман.',
    ['#272c24','#3a4438','#4a5758','#586467','#697478','#8a939b','#9aa4ac','#abb4bc'],
    { temperature:'cool', saturation:'low', contrast:'low', shadows:'muted pine green', highlights:'silver gray fog' },
    'Low-saturation mist grade with muted pine-green shadows and soft silver-gray highlights; maintain atmospheric depth without crushing foreground detail.'),
  palette('monochrome_geometry', 'Чёрно-белая геометрия', 'monochrome-stairs',
    'Чистая тональная структура без цвета, с читаемыми тенями.',
    ['#0c0d0c','#161615','#272726','#474846','#777876','#a7a8a6','#c9cac9','#d8d8d7'],
    { temperature:'neutral', saturation:'none', contrast:'high', shadows:'rich black', highlights:'clean silver white' },
    'Black-and-white grade with rich but legible blacks, crisp silver highlights and clearly separated midtones; prioritize shape and shadow geometry.'),
  palette('pastel_garden', 'Пастельный сад', 'pastel-greenhouse',
    'Мягкий садовый свет: оливковые полутона и нежные цветочные акценты.',
    ['#37381a','#585629','#766947','#988968','#a79779','#b7aa96','#c7baa7','#d6cab8'],
    { temperature:'warm', saturation:'low', contrast:'low', shadows:'soft sage olive', highlights:'creamy floral white' },
    'Soft pastel garden grade with sage-olive shadows, gentle pink floral accents and creamy highlights; keep contrast low but preserve subject separation.'),
  palette('vivid_market', 'Яркий рынок', 'vivid-market',
    'Сочные фрукты на фоне бирюзы и тёплого песка.',
    ['#281906','#025955','#584629','#936739','#c76a02','#358a98','#d9a26a','#86c2d4'],
    { temperature:'split', saturation:'high', contrast:'medium', shadows:'deep teal', highlights:'sunlit citrus yellow' },
    'Vibrant controlled color grade: deep teal structure and sunlit citrus, coral and tropical fruit accents; keep each object’s hue distinct without clipping.'),
  palette('burgundy_velvet', 'Бордовый бархат', 'burgundy-theater',
    'Глубокий бордовый зал и локальное золотое освещение.',
    ['#090100','#180300','#260601','#360703','#381701','#451c02','#572803','#763a08'],
    { temperature:'warm', saturation:'moderate', contrast:'high', shadows:'burgundy black', highlights:'localized antique gold' },
    'Luxurious theater grade with deep burgundy-black shadows, warm velvet reds and localized antique-gold light; reveal texture in dark materials.'),
  palette('sage_coast', 'Шалфейный берег', 'sage-coast',
    'Спокойные травы, песок и матовое морское небо.',
    ['#574727','#886939','#7b8876','#949c85','#a5a78c','#b9b498','#c7c7ab','#e6dab9'],
    { temperature:'neutral', saturation:'low', contrast:'low', shadows:'sage and olive', highlights:'sandstone cream' },
    'Calm coastal grade with desaturated sage and olive shadows, pale sandstone highlights and natural sea haze; avoid saturated postcard blues.'),
  palette('electric_violet', 'Электрический фиолет', 'violet-stage',
    'Контрастный сценический свет: фиолетовый, розовый и глубокий чёрный.',
    ['#06020b','#1a0426','#270538','#380846','#470c56','#681875','#763bd4','#d866d6'],
    { temperature:'cool', saturation:'high', contrast:'high', shadows:'deep violet black', highlights:'magenta lilac beams' },
    'Night-stage grade with deep violet-black negative space and distinct magenta and lilac light beams; preserve beam separation and highlight detail.'),
  palette('peach_modern', 'Персик и лазурь', 'peach-pool',
    'Солнечная архитектура с персиковыми стенами и лазурной водой.',
    ['#685738','#b96c45','#78877a','#5b9bab','#69a8b6','#76b5bb','#c7a884','#f5e9da'],
    { temperature:'split', saturation:'moderate', contrast:'medium', shadows:'soft terracotta', highlights:'aqua and peach sunlight' },
    'Retro summer grade: warm peach and terracotta architecture against clean turquoise water and soft cream sunlight; keep the split natural, not neon.'),
];

export const PALETTE_BY_ID = Object.fromEntries(PALETTE_CATALOG.map((entry) => [entry.id, entry]));
export function displayPalette(entry, locale = 'ru') {
  if (!entry) return { title: '', description: '' };
  return String(locale).toLowerCase().startsWith('en')
    ? { title: entry.titleEn || entry.title, description: entry.descriptionEn || entry.description }
    : { title: entry.title, description: entry.description };
}
