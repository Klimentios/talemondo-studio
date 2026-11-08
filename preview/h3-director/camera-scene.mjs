// Extract only explicitly stated placement numbers; other cinematography stays in the story.
export function sceneCameraPlacement(prompt) {
  const line = String(prompt || '').split(/\r?\n/).find((text) =>
    /\bcamera\b/i.test(text) && /above (?:the )?floor/i.test(text) && /\bfrom\b/i.test(text));
  if (!line) return null;
  const height = line.match(/(\d+(?:\.\d+)?)\s*(cm|m)\s+above (?:the )?floor/i);
  const distance = line.match(/(\d+(?:\.\d+)?)\s*m\s+from\b/i);
  if (!height || !distance) return null;
  const h = Number(height[1]) / (height[2].toLowerCase() === 'cm' ? 100 : 1);
  const d = Number(distance[1]);
  if (!(h >= 0 && h <= 10 && d >= 0.5 && d <= 50)) return null;
  // Non-numerical three-quarter direction uses the existing gentle off-axis convention.
  const orbit = /front[- ]left/i.test(line) ? -22 : /front[- ]right/i.test(line) ? 22 : undefined;
  return { height: h, distance: d, orbit };
}
