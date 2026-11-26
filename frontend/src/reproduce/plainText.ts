import type { SelectedContext } from './types';

/**
 * Strip Markdown & inline LaTeX markers for plain-text UI containers
 * (banners, tooltips, single-line status pills, and discussion summary boxes)
 * without emitting trailing ellipses (`…` or `...`).
 */
export function toPlainUiText(raw: string | null | undefined, maxChars = 360): string {
  const str = String(raw || '').trim();
  if (!str) return '';

  const cleaned = str
    .replace(/```[a-zA-Z0-9_-]*/g, ' ')
    .replace(/^\s*#{1,6}\s+([^\n]+)/gm, '$1 — ')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*(?:[-*+]|\d+\.)\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/(\*\*|__|~~|\*|`)/g, '')
    .replace(/\$([^$]{1,40})\$/g, '$1')
    .replace(/\s+/g, ' ')
    .replace(/(?:\.{3,}|…)+\s*$/g, '')
    .trim();

  if (cleaned.length <= maxChars) return cleaned;

  const windowStr = cleaned.slice(0, maxChars);
  const sentenceMatches = [...windowStr.matchAll(/[.!?。；;](?=\s|$)/g)];
  if (sentenceMatches.length > 0) {
    const lastMatch = sentenceMatches[sentenceMatches.length - 1];
    const endIdx = (lastMatch.index ?? 0) + 1;
    if (endIdx >= 36) {
      return windowStr.slice(0, endIdx).trim();
    }
  }
  const lastSpace = windowStr.lastIndexOf(' ');
  const clipped = lastSpace > 24 ? windowStr.slice(0, lastSpace) : windowStr;
  return clipped.replace(/[\s,;:(\-—]+$/g, '').trim();
}

function pickCleanWords(text: string, maxChars: number): string {
  const clean = text.trim();
  if (!clean) return '';
  if (clean.length <= maxChars) return clean;
  const words = clean.split(/\s+/);
  let out = '';
  for (const w of words) {
    const next = out ? `${out} ${w}` : w;
    if (next.length > maxChars) break;
    out = next;
  }
  return out || clean.slice(0, maxChars).trim();
}

/**
 * Format a concise single-line label for `selected-context-chip` without ellipses.
 */
export function formatShortContextChip(ctx: SelectedContext, rawLabel: string): string {
  const plain = toPlainUiText(rawLabel, 120);
  if (ctx.kind === 'collaboration') {
    const stripped = plain
      .replace(/^Table:\s*/i, '')
      .split(/[:—]/)[0]
      .replace(/\b(review|discussion|comparability)\b/gi, '')
      .replace(/\s+/g, ' ')
      .trim();
    const base = pickCleanWords(stripped || 'Discussion', 16);
    return `${base} Table`;
  }
  if (ctx.kind === 'agent') {
    if (/^Briefing:/i.test(plain)) {
      const topic = plain.replace(/^Briefing:\s*/i, '').split(/[&:—]/)[0].trim();
      return `Briefing · ${pickCleanWords(topic || 'Report', 12)}`;
    }
    const shortAgent = plain.replace(/\s+Agent$/i, '').trim();
    return pickCleanWords(shortAgent || 'Agent', 18);
  }
  return pickCleanWords(plain || ctx.id, 18);
}
