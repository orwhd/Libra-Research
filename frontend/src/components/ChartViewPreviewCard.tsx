import React, { useId, useMemo } from 'react';
import type { TrendingChartIconKind, TrendingChartItem } from '../types';

interface ChartViewPreviewCardProps {
  id: string;
  item: TrendingChartItem;
  isoWeek?: string;
  visible: boolean;
}

const ALLOWED_SVG_TAGS = new Set([
  'svg',
  'g',
  'path',
  'circle',
  'rect',
  'line',
  'polyline',
  'polygon',
  'ellipse',
  'text',
  'tspan',
  'defs',
  'linearGradient',
  'radialGradient',
  'stop',
  'clipPath',
]);

const ALLOWED_ANIM_CLASSES = new Set([
  'chart-preview-svg',
  'preview-anim-pulse',
  'preview-anim-flow',
  'preview-anim-phase-a',
  'preview-anim-phase-b',
]);

const SVG_ATTR_TO_REACT: Record<string, string> = {
  viewBox: 'viewBox',
  fill: 'fill',
  'fill-opacity': 'fillOpacity',
  stroke: 'stroke',
  'stroke-width': 'strokeWidth',
  'stroke-dasharray': 'strokeDasharray',
  'stroke-dashoffset': 'strokeDashoffset',
  'stroke-linecap': 'strokeLinecap',
  'stroke-linejoin': 'strokeLinejoin',
  'stroke-opacity': 'strokeOpacity',
  opacity: 'opacity',
  d: 'd',
  cx: 'cx',
  cy: 'cy',
  r: 'r',
  rx: 'rx',
  ry: 'ry',
  x: 'x',
  y: 'y',
  x1: 'x1',
  y1: 'y1',
  x2: 'x2',
  y2: 'y2',
  width: 'width',
  height: 'height',
  points: 'points',
  transform: 'transform',
  id: 'id',
  offset: 'offset',
  'stop-color': 'stopColor',
  'stop-opacity': 'stopOpacity',
  'text-anchor': 'textAnchor',
  'font-size': 'fontSize',
  'font-weight': 'fontWeight',
  'letter-spacing': 'letterSpacing',
  'clip-path': 'clipPath',
};

const PRIMITIVE_TAGS = new Set([
  'path',
  'circle',
  'rect',
  'line',
  'polyline',
  'polygon',
  'ellipse',
]);

const UNSAFE_SVG_VALUE_RE = /(javascript:|data:|https?:|expression\()/i;

function convertSvgNode(
  el: Element,
  key: string,
  primitiveCounter: { count: number }
): React.ReactElement | null {
  const tag = el.tagName;
  if (!ALLOWED_SVG_TAGS.has(tag)) return null;
  if (PRIMITIVE_TAGS.has(tag)) primitiveCounter.count += 1;

  const props: Record<string, unknown> = { key };
  for (const attr of Array.from(el.attributes)) {
    const val = attr.value.trim();
    if (!val || UNSAFE_SVG_VALUE_RE.test(val)) continue;
    if (attr.name === 'class') {
      const safeClasses = val
        .split(/\s+/)
        .filter((tok) => ALLOWED_ANIM_CLASSES.has(tok));
      if (safeClasses.length > 0) {
        props.className = safeClasses.join(' ');
      }
      continue;
    }
    const reactAttr = SVG_ATTR_TO_REACT[attr.name];
    if (!reactAttr) continue;
    if (
      (attr.name === 'fill' || attr.name === 'stroke' || attr.name === 'clip-path') &&
      /url\(/i.test(val) &&
      !/^url\(#[A-Za-z0-9_-]+\)$/.test(val)
    ) {
      continue;
    }
    props[reactAttr] = val;
  }

  if (tag === 'svg') {
    props.className = 'chart-preview-svg';
    props.viewBox = '0 0 280 124';
    props.fill = 'none';
    props['aria-hidden'] = true;
  }

  const children: React.ReactNode[] = [];
  for (let i = 0; i < el.childNodes.length; i += 1) {
    const child = el.childNodes[i];
    if (child.nodeType === Node.ELEMENT_NODE) {
      const converted = convertSvgNode(child as Element, `${key}-${i}`, primitiveCounter);
      if (converted) children.push(converted);
    } else if (
      (tag === 'text' || tag === 'tspan') &&
      (child.nodeType === Node.TEXT_NODE || child.nodeType === Node.CDATA_SECTION_NODE)
    ) {
      if (child.textContent) children.push(child.textContent.slice(0, 80));
    }
  }

  return React.createElement(tag, props, ...children);
}

const SafeSchematicSvg: React.FC<{ svg: string }> = ({ svg }) => {
  const rendered = useMemo(() => {
    if (!svg.trim() || typeof DOMParser === 'undefined') return null;
    try {
      const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
      if (doc.querySelector('parsererror')) return null;
      const root = doc.documentElement;
      if (!root || root.tagName.toLowerCase() !== 'svg') return null;
      const counter = { count: 0 };
      const el = convertSvgNode(root, 'svg-root', counter);
      if (!el || counter.count < 4) return null;
      return el;
    } catch {
      return null;
    }
  }, [svg]);

  return rendered;
};

const HEATMAP_MATRIX: number[][] = [
  [0.24, 0.42, 0.68, 0.52, 0.31],
  [0.36, 0.58, 0.74, 0.94, 0.64],
  [0.49, 0.82, 0.61, 0.45, 0.28],
  [0.18, 0.33, 0.56, 0.71, 0.86],
];

const HEATMAP_COLORS = ['#e2e8f0', '#bfdbfe', '#93c5fd', '#60a5fa', '#2563eb'];

function heatFill(value: number): string {
  if (value >= 0.85) return HEATMAP_COLORS[4];
  if (value >= 0.65) return HEATMAP_COLORS[3];
  if (value >= 0.45) return HEATMAP_COLORS[2];
  if (value >= 0.28) return HEATMAP_COLORS[1];
  return HEATMAP_COLORS[0];
}

const TrendPreviewSvg: React.FC<{ gradId: string }> = ({ gradId }) => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="#2563eb" stopOpacity="0.24" />
        <stop offset="100%" stopColor="#2563eb" stopOpacity="0.02" />
      </linearGradient>
    </defs>

    <g stroke="#e2e8f0" strokeDasharray="3 3" strokeWidth="1">
      <line x1="18" y1="32" x2="262" y2="32" />
      <line x1="18" y1="54" x2="262" y2="54" />
      <line x1="18" y1="76" x2="262" y2="76" />
      <line x1="18" y1="98" x2="262" y2="98" />
    </g>

    <rect x="18" y="6" width="94" height="16" rx="8" fill="#ffffff" stroke="#cbd5e1" />
    <rect x="20" y="8" width="44" height="12" rx="6" fill="#eff6ff" />
    <text x="42" y="16.5" textAnchor="middle" fontSize="7.5" fontWeight="700" fill="#2563eb">
      Volume
    </text>
    <text x="87" y="16.5" textAnchor="middle" fontSize="7.5" fontWeight="600" fill="#64748b">
      Growth %
    </text>

    <path
      d="M 22 92 C 44 88, 64 81, 84 76 C 108 70, 132 58, 156 49 C 180 40, 202 30, 224 26 C 238 24, 248 29, 260 34 L 260 100 L 22 100 Z"
      fill={`url(#${gradId})`}
    />
    <path
      d="M 22 96 C 60 91, 108 82, 156 72 C 204 62, 234 55, 260 51"
      stroke="#0ea5e9"
      strokeWidth="1.5"
      strokeDasharray="4 3"
      strokeLinecap="round"
    />
    <path
      d="M 22 92 C 44 88, 64 81, 84 76 C 108 70, 132 58, 156 49 C 180 40, 202 30, 224 26 C 238 24, 248 29, 260 34"
      stroke="#2563eb"
      strokeWidth="2.4"
      strokeLinecap="round"
    />

    <rect x="18" y="108" width="244" height="7" rx="3.5" fill="#e2e8f0" />
    <rect x="82" y="108" width="144" height="7" rx="3.5" fill="#93c5fd" />
    <rect x="80" y="106" width="5" height="11" rx="2" fill="#2563eb" />
    <rect x="223" y="106" width="5" height="11" rx="2" fill="#2563eb" />

    <g className="preview-anim-trend-1">
      <line x1="84" y1="24" x2="84" y2="100" stroke="#2563eb" strokeWidth="1.2" strokeDasharray="3 2" />
      <circle cx="84" cy="76" r="4.5" fill="#2563eb" stroke="#ffffff" strokeWidth="1.8" />
      <rect x="52" y="45" width="64" height="18" rx="6" fill="#0f172a" />
      <text x="84" y="57" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        2023 · 142
      </text>
    </g>

    <g className="preview-anim-trend-2">
      <line x1="156" y1="22" x2="156" y2="100" stroke="#2563eb" strokeWidth="1.2" strokeDasharray="3 2" />
      <circle cx="156" cy="49" r="4.5" fill="#2563eb" stroke="#ffffff" strokeWidth="1.8" />
      <rect x="122" y="18" width="68" height="18" rx="6" fill="#0f172a" />
      <text x="156" y="30" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        2025 · +38%
      </text>
    </g>

    <g className="preview-anim-trend-3">
      <line x1="224" y1="14" x2="224" y2="100" stroke="#2563eb" strokeWidth="1.2" strokeDasharray="3 2" />
      <circle cx="224" cy="26" r="4.5" fill="#2563eb" stroke="#ffffff" strokeWidth="1.8" />
      <rect x="180" y="6" width="64" height="17" rx="6" fill="#0f172a" />
      <text x="212" y="17.5" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        Peak · 310
      </text>
    </g>
  </svg>
);

const NetworkPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <rect x="12" y="8" width="54" height="16" rx="8" fill="#ffffff" stroke="#cbd5e1" />
    <text x="39" y="18.5" textAnchor="middle" fontSize="8" fontWeight="600" fill="#475569">
      Ties ≥ 2
    </text>

    <g className="preview-anim-network-dim">
      <g stroke="#cbd5e1" strokeWidth="1.2">
        <line x1="56" y1="48" x2="88" y2="30" />
        <line x1="56" y1="48" x2="48" y2="86" />
        <line x1="88" y1="30" x2="110" y2="56" />
        <line x1="48" y1="86" x2="96" y2="98" />
        <line x1="186" y1="34" x2="224" y2="52" />
        <line x1="224" y1="52" x2="222" y2="88" />
        <line x1="168" y1="96" x2="134" y2="108" />
        <line x1="134" y1="108" x2="96" y2="98" />
      </g>
      <circle cx="56" cy="48" r="5" fill="#93c5fd" />
      <circle cx="110" cy="56" r="4.5" fill="#a5b4fc" />
      <circle cx="96" cy="98" r="4.5" fill="#93c5fd" />
      <circle cx="224" cy="52" r="5" fill="#7dd3fc" />
      <circle cx="134" cy="108" r="4.5" fill="#a5b4fc" />
    </g>

    <g className="preview-anim-ego-edges" stroke="#2563eb" strokeLinecap="round">
      <line x1="136" y1="64" x2="88" y2="30" strokeWidth="2.1" />
      <line x1="136" y1="64" x2="186" y2="34" strokeWidth="2.4" />
      <line x1="136" y1="64" x2="222" y2="88" strokeWidth="1.8" />
      <line x1="136" y1="64" x2="168" y2="96" strokeWidth="2.2" />
      <line x1="136" y1="64" x2="48" y2="86" strokeWidth="1.7" />
    </g>

    <circle cx="88" cy="30" r="6" fill="#2563eb" />
    <circle cx="48" cy="86" r="5.5" fill="#2563eb" />
    <circle cx="186" cy="34" r="6.5" fill="#0ea5e9" />
    <circle cx="222" cy="88" r="5.5" fill="#0ea5e9" />
    <circle cx="168" cy="96" r="6" fill="#6366f1" />

    <circle
      className="preview-anim-ego-ring"
      cx="136"
      cy="64"
      r="13"
      stroke="#2563eb"
      strokeWidth="1.6"
      fill="#2563eb"
      fillOpacity="0.12"
    />
    <circle cx="136" cy="64" r="8" fill="#2563eb" stroke="#ffffff" strokeWidth="2" />

    <g className="preview-anim-ego-badge">
      <rect x="148" y="42" width="70" height="18" rx="6" fill="#0f172a" />
      <text x="183" y="54" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        Hub · 14 ties
      </text>
    </g>
  </svg>
);

const BundlingPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <g stroke="#cbd5e1" strokeWidth="1" opacity="0.75">
      <path d="M 58 49 Q 96 62 124 34" />
      <path d="M 58 49 Q 96 62 88 100" />
      <path d="M 102 23 Q 96 62 134 68" />
      <path d="M 102 23 Q 96 62 69 93" />
      <path d="M 133 50 Q 96 62 106 99" />
      <path d="M 122 89 Q 96 62 59 77" />
    </g>

    <g className="preview-anim-bundle-links" strokeWidth="1.9" strokeLinecap="round">
      <path d="M 65 36 Q 96 62 133 50" stroke="#2563eb" />
      <path d="M 65 36 Q 96 62 122 89" stroke="#0ea5e9" />
      <path d="M 80 26 Q 96 62 134 68" stroke="#2563eb" />
      <path d="M 80 26 Q 96 62 106 99" stroke="#6366f1" />
    </g>

    <path d="M 52 50 A 45 45 0 0 1 118 23" stroke="#2563eb" strokeWidth="4.5" strokeLinecap="round" />
    <path d="M 128 30 A 45 45 0 0 1 119 101" stroke="#0ea5e9" strokeWidth="4.5" strokeLinecap="round" />
    <path d="M 108 105 A 45 45 0 0 1 51 62" stroke="#6366f1" strokeWidth="4.5" strokeLinecap="round" />

    <g fill="#64748b">
      <circle cx="58" cy="49" r="2.3" />
      <circle cx="102" cy="23" r="2.3" />
      <circle cx="124" cy="34" r="2.3" />
      <circle cx="88" cy="100" r="2.3" />
      <circle cx="69" cy="93" r="2.3" />
      <circle cx="59" cy="77" r="2.3" />
    </g>
    <g className="preview-anim-bundle-nodes">
      <circle cx="65" cy="36" r="3.4" fill="#2563eb" stroke="#ffffff" strokeWidth="1" />
      <circle cx="80" cy="26" r="3.4" fill="#2563eb" stroke="#ffffff" strokeWidth="1" />
      <circle cx="133" cy="50" r="3" fill="#0ea5e9" />
      <circle cx="134" cy="68" r="3" fill="#0ea5e9" />
      <circle cx="122" cy="89" r="3" fill="#0ea5e9" />
      <circle cx="106" cy="99" r="3" fill="#6366f1" />
    </g>

    <rect x="164" y="22" width="102" height="80" rx="10" fill="#ffffff" stroke="#e2e8f0" />
    <text x="174" y="39" fontSize="8" fontWeight="700" fill="#475569">
      Bundle Tension
    </text>
    <text x="256" y="39" textAnchor="end" fontSize="8" fontWeight="700" fill="#2563eb">
      0.85
    </text>
    <rect x="174" y="47" width="82" height="5" rx="2.5" fill="#e2e8f0" />
    <rect x="174" y="47" width="68" height="5" rx="2.5" fill="#2563eb" />
    <circle cx="242" cy="49.5" r="4" fill="#2563eb" stroke="#ffffff" strokeWidth="1.5" />

    <rect x="174" y="68" width="82" height="22" rx="6" fill="#0f172a" />
    <text x="215" y="82" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
      Sector A ↔ 4 links
    </text>
  </svg>
);

const HeatmapPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <rect x="34" y="6" width="54" height="15" rx="7.5" fill="#ffffff" stroke="#cbd5e1" />
    <text x="61" y="16" textAnchor="middle" fontSize="7.5" fontWeight="600" fill="#475569">
      Sort: Peak
    </text>

    <text x="194" y="16" fontSize="7.5" fontWeight="700" fill="#64748b">
      Row Total
    </text>
    <rect x="194" y="26" width="38" height="13" rx="3" fill="#cbd5e1" />
    <rect x="194" y="46" width="64" height="13" rx="3" fill="#93c5fd" className="preview-anim-bar-r1" />
    <rect x="194" y="66" width="54" height="13" rx="3" fill="#93c5fd" className="preview-anim-bar-r2" />
    <rect x="194" y="86" width="46" height="13" rx="3" fill="#cbd5e1" />

    {['C1', 'C2', 'C3', 'C4'].map((label, rIdx) => (
      <text
        key={label}
        x="26"
        y={35 + rIdx * 20}
        textAnchor="end"
        fontSize="8"
        fontWeight="600"
        fill="#64748b"
      >
        {label}
      </text>
    ))}
    {['Q1', 'Q2', 'Q3', 'Q4', 'Q5'].map((label, cIdx) => (
      <text
        key={label}
        x={47 + cIdx * 29}
        y="114"
        textAnchor="middle"
        fontSize="8"
        fontWeight="600"
        fill="#64748b"
      >
        {label}
      </text>
    ))}

    {HEATMAP_MATRIX.map((row, rIdx) =>
      row.map((val, cIdx) => (
        <rect
          key={`${rIdx}-${cIdx}`}
          x={34 + cIdx * 29}
          y={24 + rIdx * 20}
          width="26"
          height="17"
          rx="3"
          fill={heatFill(val)}
        />
      ))
    )}

    <g className="preview-anim-heat-1">
      <rect
        x={34 + 3 * 29 - 1}
        y={24 + 1 * 20 - 1}
        width="28"
        height="19"
        rx="4"
        stroke="#0f172a"
        strokeWidth="2"
        fill="none"
      />
      <rect x="94" y="18" width="76" height="18" rx="6" fill="#0f172a" />
      <text x="132" y="30" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        C2 × Q4 · 92.4
      </text>
    </g>

    <g className="preview-anim-heat-2">
      <rect
        x={34 + 1 * 29 - 1}
        y={24 + 2 * 20 - 1}
        width="28"
        height="19"
        rx="4"
        stroke="#0f172a"
        strokeWidth="2"
        fill="none"
      />
      <rect x="44" y="38" width="76" height="18" rx="6" fill="#0f172a" />
      <text x="82" y="50" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        C3 × Q2 · 78.1
      </text>
    </g>
  </svg>
);

const FlowPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <path
      d="M 38 30 C 86 30, 92 26, 134 26 L 134 44 C 92 44, 86 46, 38 46 Z"
      fill="#93c5fd"
      fillOpacity="0.45"
    />
    <path
      d="M 38 74 C 86 74, 92 84, 134 84 L 134 102 C 92 102, 86 92, 38 92 Z"
      fill="#c7d2fe"
      fillOpacity="0.55"
    />
    <path
      d="M 146 66 C 188 66, 198 84, 242 84 L 242 104 C 198 104, 188 86, 146 86 Z"
      fill="#cbd5e1"
      fillOpacity="0.5"
    />

    <g className="preview-anim-flow">
      <path
        d="M 38 46 C 84 46, 94 50, 134 50 L 134 72 C 94 72, 84 64, 38 64 Z"
        fill="#2563eb"
        fillOpacity="0.34"
      />
      <path
        d="M 146 32 C 190 32, 196 42, 242 42 L 242 66 C 196 66, 190 58, 146 58 Z"
        fill="#0ea5e9"
        fillOpacity="0.38"
      />
      <path
        d="M 38 55 C 86 55, 96 61, 134 61"
        stroke="#2563eb"
        strokeWidth="2.2"
        strokeDasharray="6 4"
        strokeLinecap="round"
      />
      <path
        d="M 146 45 C 190 45, 196 54, 242 54"
        stroke="#0ea5e9"
        strokeWidth="2.2"
        strokeDasharray="6 4"
        strokeLinecap="round"
      />
    </g>

    <rect x="26" y="24" width="12" height="42" rx="3" fill="#2563eb" />
    <rect x="26" y="72" width="12" height="28" rx="3" fill="#6366f1" />
    <rect x="134" y="22" width="12" height="54" rx="3" fill="#0ea5e9" />
    <rect x="134" y="82" width="12" height="24" rx="3" fill="#64748b" />
    <rect x="242" y="34" width="12" height="36" rx="3" fill="#2563eb" />
    <rect x="242" y="78" width="12" height="28" rx="3" fill="#94a3b8" />

    <rect x="96" y="10" width="88" height="18" rx="6" fill="#0f172a" />
    <text x="140" y="22" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
      Path · 48.2% share
    </text>
  </svg>
);

const HierarchyPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <rect x="18" y="12" width="244" height="100" rx="8" fill="#ffffff" stroke="#cbd5e1" />
    <rect x="24" y="18" width="128" height="88" rx="5" fill="#eff6ff" stroke="#93c5fd" />
    <rect
      x="30"
      y="30"
      width="68"
      height="70"
      rx="4"
      fill="#2563eb"
      fillOpacity="0.18"
      stroke="#2563eb"
      strokeWidth="1.6"
      className="preview-anim-pulse"
    />
    <rect x="104" y="30" width="42" height="34" rx="4" fill="#60a5fa" fillOpacity="0.35" />
    <rect x="104" y="68" width="42" height="32" rx="4" fill="#93c5fd" fillOpacity="0.45" />

    <rect x="158" y="18" width="98" height="44" rx="5" fill="#ecfeff" stroke="#7dd3fc" />
    <rect x="164" y="24" width="46" height="32" rx="3" fill="#0ea5e9" fillOpacity="0.3" />
    <rect x="214" y="24" width="36" height="32" rx="3" fill="#38bdf8" fillOpacity="0.22" />

    <rect x="158" y="68" width="98" height="38" rx="5" fill="#eef2ff" stroke="#a5b4fc" />
    <rect x="164" y="74" width="54" height="26" rx="3" fill="#6366f1" fillOpacity="0.25" />
    <rect x="222" y="74" width="28" height="26" rx="3" fill="#818cf8" fillOpacity="0.2" />

    <rect x="28" y="10" width="76" height="17" rx="6" fill="#0f172a" />
    <text x="66" y="21.5" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
      Root &gt; Core
    </text>
  </svg>
);

const ScatterPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <g stroke="#e2e8f0" strokeWidth="1" strokeDasharray="3 3">
      <line x1="26" y1="30" x2="258" y2="30" />
      <line x1="26" y1="62" x2="258" y2="62" />
      <line x1="26" y1="94" x2="258" y2="94" />
      <line x1="88" y1="16" x2="88" y2="104" />
      <line x1="168" y1="16" x2="168" y2="104" />
    </g>

    <rect
      x="132"
      y="20"
      width="88"
      height="50"
      rx="9"
      fill="#2563eb"
      fillOpacity="0.08"
      stroke="#2563eb"
      strokeWidth="1.4"
      strokeDasharray="4 3"
      className="preview-anim-pulse"
    />

    <circle cx="54" cy="82" r="4" fill="#6366f1" fillOpacity="0.7" />
    <circle cx="70" cy="74" r="5" fill="#6366f1" fillOpacity="0.8" />
    <circle cx="82" cy="88" r="3.8" fill="#6366f1" fillOpacity="0.7" />
    <circle cx="98" cy="70" r="4.5" fill="#0ea5e9" fillOpacity="0.75" />
    <circle cx="116" cy="58" r="4" fill="#0ea5e9" fillOpacity="0.7" />

    <circle cx="148" cy="46" r="5" fill="#2563eb" />
    <circle cx="164" cy="36" r="6" fill="#2563eb" stroke="#ffffff" strokeWidth="1.5" />
    <circle cx="178" cy="50" r="4.5" fill="#0ea5e9" />
    <circle cx="192" cy="32" r="5.5" fill="#2563eb" />
    <circle cx="206" cy="44" r="4.5" fill="#0ea5e9" />
    <circle cx="232" cy="72" r="4.5" fill="#94a3b8" />

    <rect x="136" y="76" width="82" height="18" rx="6" fill="#0f172a" />
    <text x="177" y="88" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
      Cluster B · n=18
    </text>
  </svg>
);

const RadialPreviewSvg: React.FC = () => (
  <svg className="chart-preview-svg" viewBox="0 0 280 124" fill="none" aria-hidden="true">
    <g stroke="#cbd5e1" strokeWidth="1">
      <circle cx="100" cy="64" r="16" strokeDasharray="2 2" />
      <circle cx="100" cy="64" r="30" strokeDasharray="2 2" />
      <circle cx="100" cy="64" r="44" />
      <line x1="100" y1="20" x2="100" y2="108" />
      <line x1="56" y1="64" x2="144" y2="64" />
      <line x1="69" y1="33" x2="131" y2="95" />
      <line x1="131" y1="33" x2="69" y2="95" />
    </g>

    <polygon
      points="100,26 128,44 134,76 100,96 72,80 68,46"
      fill="#2563eb"
      fillOpacity="0.18"
      stroke="#2563eb"
      strokeWidth="2"
      className="preview-anim-pulse"
    />
    <polygon
      points="100,38 118,52 120,70 100,82 82,70 80,50"
      fill="#0ea5e9"
      fillOpacity="0.16"
      stroke="#0ea5e9"
      strokeWidth="1.6"
    />
    <circle cx="100" cy="26" r="3.5" fill="#2563eb" stroke="#ffffff" strokeWidth="1" />
    <circle cx="128" cy="44" r="3.5" fill="#2563eb" stroke="#ffffff" strokeWidth="1" />
    <circle cx="134" cy="76" r="3.5" fill="#0ea5e9" stroke="#ffffff" strokeWidth="1" />

    <rect x="166" y="28" width="96" height="68" rx="10" fill="#ffffff" stroke="#e2e8f0" />
    <text x="176" y="45" fontSize="8" fontWeight="700" fill="#475569">
      Polar Score
    </text>
    <rect x="176" y="54" width="74" height="5" rx="2.5" fill="#e2e8f0" />
    <rect x="176" y="54" width="58" height="5" rx="2.5" fill="#2563eb" />
    <rect x="176" y="68" width="76" height="19" rx="6" fill="#0f172a" />
    <text x="214" y="80" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
      Index · 84.6
    </text>
  </svg>
);

const BuiltinIconPreview: React.FC<{
  kind: TrendingChartIconKind;
  gradId: string;
}> = ({ kind, gradId }) => {
  switch (kind) {
    case 'network':
      return <NetworkPreviewSvg />;
    case 'bundling':
      return <BundlingPreviewSvg />;
    case 'heatmap':
      return <HeatmapPreviewSvg />;
    case 'flow':
      return <FlowPreviewSvg />;
    case 'hierarchy':
      return <HierarchyPreviewSvg />;
    case 'scatter':
      return <ScatterPreviewSvg />;
    case 'radial':
      return <RadialPreviewSvg />;
    case 'trend':
    default:
      return <TrendPreviewSvg gradId={gradId} />;
  }
};

export const ChartViewPreviewCard: React.FC<ChartViewPreviewCardProps> = ({
  id,
  item,
  isoWeek,
  visible,
}) => {
  const gradId = useId();

  return (
    <div
      id={id}
      role="tooltip"
      className={`chart-preview-popover ${visible ? 'is-open' : ''}`}
    >
      <div className="chart-preview-stage">
        {item.schematic_svg ? (
          <SafeSchematicSvg svg={item.schematic_svg} />
        ) : null}
        {!item.schematic_svg && <BuiltinIconPreview kind={item.icon_kind} gradId={gradId} />}
      </div>

      <div className="chart-preview-body">
        <p className="chart-preview-summary">{item.summary}</p>
        <div className="chart-preview-badges">
          {item.interactions.map((tag) => (
            <span key={tag} className="chart-preview-badge">
              {tag}
            </span>
          ))}
        </div>
        {(isoWeek || item.source_label) && (
          <div className="chart-preview-meta">
            {isoWeek && <span className="chart-preview-week">Trending {isoWeek}</span>}
            {item.source_label && (
              <span className="chart-preview-source" title={item.source_label}>
                {item.source_label}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
