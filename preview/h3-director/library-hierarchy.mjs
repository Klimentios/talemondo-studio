const IDENTITY_ROLES = new Set(['Лицо героя', 'Персонаж целиком']);
const WARDROBE_ROLE = 'Костюм';

export function hierarchy(info) {
  return {
    franchise: String(info.franchise || '').trim(),
    character: String(info.characterName || '').trim(),
    look: String(info.look || '').trim(),
  };
}

export function matchesHierarchy(info, { franchise = '', character = '', look = '' } = {}) {
  const value = hierarchy(info);
  return (!franchise || value.franchise === franchise) && (!character || value.character === character)
    && (!look || value.look === look);
}

// A selected character uses the canonical face/body plus only the chosen outfit.
// Other looks and unowned costumes are never silently mixed into a render.
export function characterReferenceSet(assets, metadataFor, franchise, character, look = '') {
  if (!franchise || !character) return [];
  const candidates = assets.filter((asset) => asset.kind === 'image').map((asset) => ({ asset, info: metadataFor(asset) }))
    .filter(({ info }) => info.franchise === franchise && info.characterName === character && !info.hidden);
  const costumes = candidates.filter(({ asset, info }) => (info.preferredRole || asset.role) === WARDROBE_ROLE
    && (look ? info.look === look : !info.look));
  const identities = candidates.filter(({ asset, info }) => {
    const role = info.preferredRole || asset.role;
    if (!IDENTITY_ROLES.has(role) || (info.look && info.look !== look)) return false;
    // A base full-body photo may carry a different outfit. Keep its face, but
    // require a full-body photo explicitly tagged for the selected look.
    return !(look && costumes.length && role === 'Персонаж целиком' && !info.look);
  });
  return [...identities, ...costumes];
}
