import React from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';

interface MarkdownMathProps {
  children: string;
}

/**
 * Normalize LaTeX delimiters (`\[...\]` -> `$$...$$`, `\(...\)` -> `$...$`) outside code
 * blocks/inline code so standard LLM mathematical expressions render crisply via KaTeX.
 */
function normalizeMathDelimiters(markdown: string): string {
  if (!markdown || !markdown.includes('\\')) {
    return markdown;
  }
  const segments = markdown.split(/(```[\s\S]*?```|`[^`\n]+`)/g);
  return segments
    .map((seg, idx) => {
      if (idx % 2 === 1) return seg;
      return seg
        .replace(/\\\[([\s\S]*?)\\\]/g, (_m, inner: string) => `\n$$\n${inner.trim()}\n$$\n`)
        .replace(/\\\(([^\n]*?)\\\)/g, (_m, inner: string) => `$${inner.trim()}$`);
    })
    .join('');
}

export const MarkdownMath: React.FC<MarkdownMathProps> = ({ children }) => {
  const normalized = React.useMemo(() => normalizeMathDelimiters(children || ''), [children]);
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
      {normalized}
    </ReactMarkdown>
  );
};
