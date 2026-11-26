import React, { useEffect, useRef } from 'react';

export const WaveProgressText: React.FC<{ text: string; title?: string }> = ({ text, title }) => {
  const chars = Array.from(text);
  const charRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    charRefs.current = charRefs.current.slice(0, chars.length);
    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      for (let i = 0; i < chars.length; i += 1) {
        const el = charRefs.current[i];
        if (el) el.style.opacity = '0.75';
      }
      return undefined;
    }

    let rafId = 0;
    const start = performance.now();
    const speed = 16;
    const halfWidth = 2.2;
    const minOpacity = 0.2;
    const maxOpacity = 0.8;
    const spanLen = chars.length + halfWidth * 2 + 1.5;

    const tick = (now: number) => {
      const elapsedSec = (now - start) / 1000;
      const head = ((elapsedSec * speed) % spanLen) - halfWidth;

      for (let i = 0; i < chars.length; i += 1) {
        const el = charRefs.current[i];
        if (!el) continue;
        const d = Math.abs(i - head);
        const boost = d < halfWidth ? Math.cos((d / halfWidth) * (Math.PI / 2)) ** 2 : 0;
        el.style.opacity = (minOpacity + (maxOpacity - minOpacity) * boost).toFixed(3);
      }
      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [text, chars.length]);

  return (
    <span className="wave-progress-text" title={title}>
      <span className="visually-hidden">{text}</span>
      <span aria-hidden="true">
        {chars.map((ch, idx) => (
          <span
            key={idx}
            ref={(el) => {
              charRefs.current[idx] = el;
            }}
            className="wave-char"
          >
            {ch}
          </span>
        ))}
      </span>
    </span>
  );
};
