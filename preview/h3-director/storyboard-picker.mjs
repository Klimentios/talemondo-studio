import { collapseLibraryCopies, inferredLibraryCategory, inferredLibraryType } from './library-quality.mjs?v=20261024-1';

export const START_FRAME_TYPES = Object.freeze([
  ['all','Все изображения'],['locations','Локации'],['characters','Персонажи'],
  ['clothes','Костюмы'],['props','Предметы'],['styles','Стили'],['other','Другие'],
]);

export function startFrameChoices(assets, library = {}, {query = '', type = 'all'} = {}) {
  const info = (asset) => {
    const saved = library[asset.asset_id] || {};
    const inferred = inferredLibraryType(asset);
    return {
      ...saved,
      name: saved.name || asset.original_name,
      type: saved.type || inferred,
      category: saved.category || inferredLibraryCategory(asset, inferred),
    };
  };
  const visible = assets.filter((asset) => asset.kind === 'image' && !info(asset).hidden);
  const unique = collapseLibraryCopies(visible, info).assets;
  const search = query.trim().toLocaleLowerCase('ru');
  return unique.map((asset) => ({asset, info: info(asset)}))
    .filter(({info: item}) => (type === 'all' || item.type === type)
      && (!search || `${item.name} ${item.category}`.toLocaleLowerCase('ru').includes(search)));
}
