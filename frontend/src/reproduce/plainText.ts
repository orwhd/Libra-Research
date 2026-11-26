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

const TRAILING_STOPWORDS = /\s+(?:and|or|of|the|a|an|for|in|on|to|with|&|by|at)$/i;

/**
 * Condense verbose agent output into a short, scannable line for compact popovers:
 * - replaces long quoted paper titles with "the paper" and drops long parentheticals,
 * - keeps whole sentences while they fit in `maxChars`, otherwise clips at a word boundary,
 * - never emits ellipses or dangling connector words.
 */
export function toCompactLine(raw: string | null | undefined, maxChars = 110): string {
  let t = toPlainUiText(raw, 4000)
    .replace(/['"“‘][^'"”’]{24,}['"”’]/g, 'the paper')
    .replace(/\s*\([^)]{4,90}\)/g, '')
    .replace(/\s+([,.;:!?])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  if (!t) return '';
  if (t.length <= maxChars) return t;

  const sentences = t.match(/[^.!?。]+[.!?。]+(?=\s|$)|[^.!?。]+$/g) || [t];
  let out = '';
  let used = 0;
  for (const s of sentences) {
    const next = out ? `${out} ${s.trim()}` : s.trim();
    if (next.length > maxChars) break;
    out = next;
    used += 1;
  }
  if (out.length >= maxChars * 0.45) return out;

  // Too little fits as whole sentences: add the next sentence clipped at a clause boundary
  const pending = sentences[used]?.trim() || '';
  const budget = maxChars - (out ? out.length + 1 : 0);
  const clipped = clipAtClause(pending, budget);
  return out ? (clipped ? `${out} ${clipped}` : out) : clipped || clipAtClause(t, maxChars);
}

function clipAtClause(text: string, maxChars: number): string {
  if (!text || maxChars < 12) return '';
  if (text.length <= maxChars) return text;
  const windowStr = text.slice(0, maxChars);
  const clauseIdx = Math.max(windowStr.lastIndexOf(', '), windowStr.lastIndexOf('; '), windowStr.lastIndexOf(': '));
  let t = clauseIdx >= maxChars * 0.35 ? windowStr.slice(0, clauseIdx) : pickCleanWords(text, maxChars);
  t = t.replace(/[\s,;:(\-—&]+$/g, '');
  while (TRAILING_STOPWORDS.test(t)) t = t.replace(TRAILING_STOPWORDS, '');
  return t;
}
