// Explicit authored photo assignments. Names are never inferred from attachment order.
export function pictureDeclarations(prompt) {
  const result = [];
  for (const match of String(prompt || '').matchAll(/^\s*<Picture\s+(\d+)>\s*(?:is\s+|[—–:-]\s*)([^\n]+)/gmi)) {
    const description = match[2].trim();
    let roles;
    if (/\b(face|facial)\b/i.test(description)) roles = ['Лицо героя'];
    else if (/\b(costume|wardrobe|outfit)\b/i.test(description)) roles = ['Костюм'];
    else if (/\b(environment|spatial|composition|location)\b/i.test(description)) roles = ['Композиция', 'Окружение'];
    else if (/\b(full.body|body appearance|silhouette|character)\b/i.test(description)) roles = ['Персонаж целиком'];
    else if (/\b(vehicle|car|prop|object)\b/i.test(description)) roles = ['Предмет', 'Окружение', 'Композиция'];
    else continue;
    const forName = /\bfor\s+([\p{L}\p{N}_-]+)(?=[\s.,:;!?]|$)/iu.exec(description)?.[1];
    const heading = /^(.+?)\s+(?:FACE|FACIAL|IDENTITY|COSTUME|WARDROBE|OUTFIT|FULL.BODY|CHARACTER)\b/i.exec(description)?.[1].trim();
    const owner = forName || (heading && /^[\p{L}\p{N}_ -]{1,80}$/u.test(heading) ? heading : '');
    result.push({ number: Number(match[1]), roles, owner });
  }
  return result;
}
