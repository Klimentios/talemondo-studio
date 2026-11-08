const genericName = /^(?:image|images|img|screenshot|screen shot|untitled|без названия)(?:[ ._\-]|$)/i;

export function inferredLibraryType(asset) {
  if (asset.kind === 'audio') return 'other';
  if (asset.kind === 'video') return 'motion';
  if (['Лицо героя', 'Персонаж целиком'].includes(asset.role)) return 'characters';
  if (asset.role === 'Костюм') return 'clothes';
  if (asset.role === 'Предмет') return 'props';
  if (asset.role === 'Окружение') return 'locations';
  if (['Стиль', 'Свет', 'Цвет'].includes(asset.role)) return 'styles';
  return 'other';
}

export function inferredLibraryCategory(asset, type = inferredLibraryType(asset)) {
  if (type === 'characters') return asset.role === 'Персонаж целиком' ? 'Персонажи / полный рост' : 'Персонажи / лицо';
  if (type === 'clothes') return 'Костюмы';
  if (type === 'props') return 'Предметы';
  if (type === 'locations') return 'Окружения';
  if (type === 'styles') return 'Стили';
  if (type === 'motion') return asset.role === 'Движение' ? 'Движение' : 'Видео';
  return asset.kind === 'audio' ? 'Аудио' : 'На разбор';
}

export function needsLibraryReview(asset, info) {
  return !info.catalogId && (genericName.test(info.name) || /^\d+\s*\./.test(info.name)
    || /^\d+$/.test(info.category) || info.category === 'На разбор');
}

export function collapseLibraryCopies(assets, metadataFor, showCopies = false) {
  if (showCopies) return { assets, collapsed: 0 };
  const chosen = new Map();
  for (const asset of assets) {
    const info = metadataFor(asset);
    // A full-body photo may deliberately serve both body and wardrobe slots.
    // Collapse repeated imports within one character role, not across roles.
    const purpose = info.franchise && info.characterName
      ? `${info.franchise}:${info.characterName}:${info.preferredRole || asset.role}` : '';
    const key = `${asset.kind}:${asset.sha256 || asset.asset_id}:${purpose}`;
    const previous = chosen.get(key);
    if (!previous || (needsLibraryReview(previous, metadataFor(previous)) && !needsLibraryReview(asset, info))) chosen.set(key, asset);
  }
  return { assets: [...chosen.values()], collapsed: assets.length - chosen.size };
}
