// «Чат с агентом»: pure helpers for agent-chat.mjs. Server: local_studio/agent_chat.py.

export const MODES = [['read', 'Только смотреть'], ['act', 'Может действовать']];

const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function inline(text) {
  return escape(text)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

/** Small, safe Markdown: code blocks, headings, lists, bold, inline code, links; everything else escaped. */
export function renderMarkdown(source) {
  const out = [];
  const blocks = String(source || '').split(/```/);
  blocks.forEach((block, index) => {
    if (index % 2) {
      out.push(`<pre><code>${escape(block.replace(/^[a-z0-9_-]*\n/i, ''))}</code></pre>`);
      return;
    }
    let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const line of block.split('\n')) {
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (bullet || numbered) {
        const kind = bullet ? 'ul' : 'ol';
        if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
        out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      } else if (heading) {
        close();
        out.push(`<h4>${inline(heading[2])}</h4>`);
      } else if (line.trim()) {
        close();
        out.push(`<p>${inline(line)}</p>`);
      } else close();
    }
    close();
  });
  return out.join('');
}

/** Apply one streamed event to the live assistant message. */
export function applyEvent(message, event) {
  if (event.kind === 'text') message.text = `${message.text ? `${message.text}\n\n` : ''}${event.text}`;
  else if (event.kind === 'error') {
    message.error = event.text;
    // Claude repeats an API error as its answer text; the server drops it on save, do the same live.
    if (message.text && event.text.startsWith(message.text.trim().slice(0, 80))) message.text = '';
  }
  else if (event.kind === 'tool') {
    const existing = event.id && message.events.find((item) => item.id === event.id);
    if (existing) Object.assign(existing, Object.fromEntries(Object.entries(event).filter(([, v]) => v !== '' && v != null)));
    else message.events.push({ ...event });
  } else if (event.kind === 'done') message.seconds = event.seconds;
  return message;
}

/** Split an NDJSON stream chunk; returns [events, rest]. */
export function splitLines(buffer) {
  const parts = buffer.split('\n');
  const rest = parts.pop();
  const events = [];
  for (const part of parts) {
    if (!part.trim()) continue;
    try { events.push(JSON.parse(part)); } catch { /* partial or noise */ }
  }
  return [events, rest];
}

export function providerNote(info) {
  if (!info) return 'проверяю…';
  if (!info.installed) return 'не установлен';
  return info.logged_in ? 'вход по подписке' : (info.detail || 'нужен вход');
}

export const TOOL_TEXT = { started: 'выполняется', done: 'готово', error: 'ошибка' };
