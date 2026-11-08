// Non-blocking prompt checks shown under the composer. Pure functions: tested without a browser.
// They never change the prompt or block a submission.

export const PROMPT_SOFT_LIMIT = 6000; // MiniMax guide: 350–500 words; far longer text dilutes priorities

const CYRILLIC = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'i', к: 'k', л: 'l',
  м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch',
  ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
};

/** Latin-letter form shared by Cyrillic and Latin spellings: «Нова» and NOVA → "nova". */
export function nameKey(text) {
  return String(text || '').toLowerCase()
    .replace(/[а-яё]/g, (letter) => CYRILLIC[letter] ?? letter)
    .replace(/ph/g, 'f').replace(/kh/g, 'h').replace(/ck/g, 'k').replace(/x/g, 'ks').replace(/w/g, 'v')
    .replace(/c(?=[eiy])/g, 's').replace(/c/g, 'k').replace(/y/g, 'i');
}

function nameStem(key) {
  // Russian case endings («Новой», «Алисой») and final vowels differ between spellings.
  const stem = key.replace(/[aeiou]+$/, '');
  return stem.length >= 3 ? stem : key;
}

export function ownerMentioned(prompt, owner) {
  const stem = nameStem(nameKey(owner).replace(/[^a-z0-9]+/g, ' ').trim());
  if (!stem) return true;
  const text = ` ${nameKey(prompt).replace(/[^a-z0-9]+/g, ' ')} `;
  return text.includes(` ${stem}`);
}

export function promptWarnings(prompt, references = [], { en = false } = {}) {
  const warnings = [];
  const text = String(prompt || '');
  if (!text.trim()) return warnings;
  const owners = [...new Set(references.map((ref) => String(ref.owner || '').trim()).filter(Boolean))];
  const missing = owners.filter((owner) => !ownerMentioned(text, owner));
  if (missing.length) {
    warnings.push(en
      ? `The prompt never names ${missing.join(', ')}: H3 links a reference to its character by that name.`
      : `В промпте не упомянут${missing.length > 1 ? 'ы' : ''} ${missing.join(', ')}: H3 связывает референс с героем по этому имени.`);
  }
  if (text.length > PROMPT_SOFT_LIMIT) {
    warnings.push(en
      ? `The prompt is ${text.length} characters. MiniMax recommends 350–500 words; a much longer text dilutes priorities.`
      : `Промпт — ${text.length} символов. MiniMax советует 350–500 слов; слишком длинный текст размывает приоритеты.`);
  }
  return warnings;
}
