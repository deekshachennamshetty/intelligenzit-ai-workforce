/* A small, dependency-free markdown renderer.
   Input is escaped before any inline formatting is applied, so backend content
   is rendered as text and never as markup. */

import { escapeHtml } from './dom.js';

function inline(raw) {
  return escapeHtml(raw)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

export function markdownToHtml(source) {
  const text = String(source || '');
  if (!text.trim()) return '<p class="muted">No content.</p>';

  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let listType = null;
  let listDepth = 0;
  let paragraph = [];
  let inCode = false;
  let code = [];

  const flushParagraph = () => {
    if (paragraph.length) { out.push(`<p>${inline(paragraph.join(' '))}</p>`); paragraph = []; }
  };
  const flushList = () => {
    while (listDepth > 0) { out.push(`</${listType}>`); listDepth -= 1; }
    listType = null;
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
        code = []; inCode = false;
      } else { flushParagraph(); flushList(); inCode = true; }
      continue;
    }
    if (inCode) { code.push(line); continue; }

    if (!line.trim()) { flushParagraph(); flushList(); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph(); flushList(); out.push('<hr>'); continue;
    }

    if (/^\s*>\s?/.test(line)) {
      flushParagraph(); flushList();
      out.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`);
      continue;
    }

    // Tables: a header row followed by a delimiter row.
    if (line.includes('|') && lines[i + 1] && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      flushParagraph(); flushList();
      const cells = (row) =>
        row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
      const header = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(cells(lines[i])); i += 1;
      }
      i -= 1;
      out.push(
        '<table><thead><tr>'
        + header.map((h) => `<th>${inline(h)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')
        + '</tbody></table>');
      continue;
    }

    const bullet = line.match(/^(\s*)[-*+]\s+(.*)$/);
    const ordered = line.match(/^(\s*)\d+[.)]\s+(.*)$/);
    if (bullet || ordered) {
      flushParagraph();
      const match = bullet || ordered;
      const want = bullet ? 'ul' : 'ol';
      const depth = Math.min(3, Math.floor(match[1].replace(/\t/g, '  ').length / 2) + 1);
      if (listType !== want) { flushList(); out.push(`<${want}>`); listType = want; listDepth = 1; }
      while (depth > listDepth) { out.push(`<${want}>`); listDepth += 1; }
      while (depth < listDepth) { out.push(`</${want}>`); listDepth -= 1; }
      out.push(`<li>${inline(match[2])}</li>`);
      continue;
    }

    paragraph.push(line.trim());
  }

  if (inCode && code.length) out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
  flushParagraph();
  flushList();
  return out.join('\n');
}
