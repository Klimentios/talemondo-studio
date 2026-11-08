// Human title for a finished H3 job. Prompts often start with IR section headers («subject_definitions:»),
// duration lines or picture labels, so the first line is not a usable name.
const HEADER = /^(?:[a-z_]+:|duration\b.*|picture\s+\d+\b.*|<[^>]+>\s*(?:is|defines)\b.*)$/i;

function clip(text, max) {
  const clean = text.replace(/^\[[^\]]*\]\s*/, '').replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max + 1);
  const space = cut.lastIndexOf(' ');
  return `${cut.slice(0, space > max * 0.6 ? space : max).replace(/[,.;:—-]+$/, '')}…`;
}

export function jobNames(prompt = '') {
  const names = [];
  for (const match of String(prompt).matchAll(/<(?:Subject|Picture)\s+\d+>\s+(?:is|defines)\s+([\p{Lu}][\p{L}\p{N}_-]{1,30})/gu)) {
    const name = match[1];
    if (/^(?:an?|the|ENVIRONMENT|LOCATION)$/i.test(name) || names.some((n) => n.toUpperCase() === name.toUpperCase())) continue;
    names.push(name);
  }
  return names;
}

export function jobTitle(job, max = 60) {
  const prompt = String(job?.original_prompt || '');
  const lines = prompt.split('\n').map((line) => line.trim()).filter(Boolean);
  const summaryAt = lines.findIndex((line) => /^summary:$/i.test(line));
  let text = summaryAt >= 0 ? (lines[summaryAt + 1] || '').split(/(?<=[.!?])\s/)[0] : '';
  if (!text) text = lines.find((line) => !HEADER.test(line)) || '';
  const names = jobNames(prompt);
  // «<Subject 1>» reads as the hero's name; other tags («<Video 1>») as plain words.
  text = text.replace(/<Subject\s+(\d+)>/gi, (tag, n) => names[n - 1] || 'герой').replace(/<(Video|Picture|Audio)\s+(\d+)>/gi, '$1 $2');
  let title = text ? clip(text, max) : '';
  if (names.length && !names.some((name) => title.toUpperCase().includes(name.toUpperCase()))) title = title ? `${names.slice(0, 2).join(' и ')} · ${title}` : names.slice(0, 3).join(', ');
  return clip(title, max + 12) || `Ролик ${String(job?.job_id || '').slice(0, 8)}`;
}
