import React, { useId } from 'react';

export type ChartViewId = 'trend' | 'network' | 'bundling' | 'heatmap';

export interface ChartViewItem {
  id: ChartViewId;
  title: string;
  summary: string;
  interactions: string[];
  prompt: string;
}

export const CHART_VIEW_ITEMS: ChartViewItem[] = [
  {
    id: 'trend',
    title: 'Trend & Growth Chart',
    summary: 'Multi-metric time series with hover crosshair readouts and brush range zoom.',
    interactions: ['Metric Switch', 'Hover Crosshair', 'Brush Zoom'],
    prompt:
      'Visualize temporal trends, growth trajectories, and key inflection points from the data with interactive metric switching and range zoom.',
  },
  {
    id: 'network',
    title: 'Network & Cluster Graph',
    summary: 'Force-directed community clusters with ego-neighborhood highlighting and tie filtering.',
    interactions: ['Ego-Node Hover', 'Drag & Zoom', 'Tie Weight Filter'],
    prompt:
      'Construct an interactive force-directed relationship network from the data, highlighting key hubs, community clusters, and connection weights.',
  },
  {
    id: 'bundling',
    title: 'Hierarchical Bundling',
    summary: 'Radial taxonomy ring with bundled spline curves revealing cross-group co-occurrence.',
    interactions: ['Path Highlight', 'Bundle Tension', 'Sector Filter'],
    prompt:
      'Build a radial hierarchical taxonomy and edge-bundling view to reveal cross-category structure and co-occurrence links in the data.',
  },
  {
    id: 'heatmap',
    title: 'Heatmap & Distribution',
    summary: 'Color-encoded category × time matrix paired with sortable marginal distribution bars.',
    interactions: ['Sort Matrix', 'Cell Inspector', 'Marginal Bars'],
    prompt:
      'Create an interactive category-by-time matrix heatmap and distribution breakdown with sorting, filtering, and detailed hover tooltips.',
  },
];

interface ChartViewPreviewCardProps {
  id: string;
  item: ChartViewItem;
  visible: boolean;
}

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
  <svg
    className="chart-preview-svg"
    viewBox="0 0 280 124"
    fill="none"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="#2563eb" stopOpacity="0.24" />
        <stop offset="100%" stopColor="#2563eb" stopOpacity="0.02" />
      </linearGradient>
    </defs>

    {/* Gridlines */}
    <g stroke="#e2e8f0" strokeDasharray="3 3" strokeWidth="1">
      <line x1="18" y1="32" x2="262" y2="32" />
      <line x1="18" y1="54" x2="262" y2="54" />
      <line x1="18" y1="76" x2="262" y2="76" />
      <line x1="18" y1="98" x2="262" y2="98" />
    </g>

    {/* Mini metric switcher */}
    <rect x="18" y="6" width="94" height="16" rx="8" fill="#ffffff" stroke="#cbd5e1" />
    <rect x="20" y="8" width="44" height="12" rx="6" fill="#eff6ff" />
    <text x="42" y="16.5" textAnchor="middle" fontSize="7.5" fontWeight="700" fill="#2563eb">
      Volume
    </text>
    <text x="87" y="16.5" textAnchor="middle" fontSize="7.5" fontWeight="600" fill="#64748b">
      Growth %
    </text>

    {/* Area & Curves */}
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

    {/* Bottom brush range window */}
    <rect x="18" y="108" width="244" height="7" rx="3.5" fill="#e2e8f0" />
    <rect x="82" y="108" width="144" height="7" rx="3.5" fill="#93c5fd" />
    <rect x="80" y="106" width="5" height="11" rx="2" fill="#2563eb" />
    <rect x="223" y="106" width="5" height="11" rx="2" fill="#2563eb" />

    {/* Animated hover crosshair steps */}
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
  <svg
    className="chart-preview-svg"
    viewBox="0 0 280 124"
    fill="none"
    aria-hidden="true"
  >
    {/* Filter badge */}
    <rect x="12" y="8" width="54" height="16" rx="8" fill="#ffffff" stroke="#cbd5e1" />
    <text x="39" y="18.5" textAnchor="middle" fontSize="8" fontWeight="600" fill="#475569">
      Ties ≥ 2
    </text>

    {/* Peripheral non-ego edges & nodes */}
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

    {/* Ego neighborhood edges (animated pulse brightness) */}
    <g className="preview-anim-ego-edges" stroke="#2563eb" strokeLinecap="round">
      <line x1="136" y1="64" x2="88" y2="30" strokeWidth="2.1" />
      <line x1="136" y1="64" x2="186" y2="34" strokeWidth="2.4" />
      <line x1="136" y1="64" x2="222" y2="88" strokeWidth="1.8" />
      <line x1="136" y1="64" x2="168" y2="96" strokeWidth="2.2" />
      <line x1="136" y1="64" x2="48" y2="86" strokeWidth="1.7" />
    </g>

    {/* Direct neighbors + hub */}
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

    {/* Floating inspector chip */}
    <g className="preview-anim-ego-badge">
      <rect x="148" y="42" width="70" height="18" rx="6" fill="#0f172a" />
      <text x="183" y="54" textAnchor="middle" fontSize="8" fontWeight="600" fill="#ffffff">
        Hub · 14 ties
      </text>
    </g>
  </svg>
);

const BundlingPreviewSvg: React.FC = () => (
  <svg
    className="chart-preview-svg"
    viewBox="0 0 280 124"
    fill="none"
    aria-hidden="true"
  >
    {/* Subtle base bundled chords */}
    <g stroke="#cbd5e1" strokeWidth="1" opacity="0.75">
      <path d="M 58 49 Q 96 62 124 34" />
      <path d="M 58 49 Q 96 62 88 100" />
      <path d="M 102 23 Q 96 62 134 68" />
      <path d="M 102 23 Q 96 62 69 93" />
      <path d="M 133 50 Q 96 62 106 99" />
      <path d="M 122 89 Q 96 62 59 77" />
    </g>

    {/* Highlighted interactive cross-sector bundle */}
    <g className="preview-anim-bundle-links" strokeWidth="1.9" strokeLinecap="round">
      <path d="M 65 36 Q 96 62 133 50" stroke="#2563eb" />
      <path d="M 65 36 Q 96 62 122 89" stroke="#0ea5e9" />
      <path d="M 80 26 Q 96 62 134 68" stroke="#2563eb" />
      <path d="M 80 26 Q 96 62 106 99" stroke="#6366f1" />
    </g>

    {/* 3 outer hierarchical sectors */}
    <path d="M 52 50 A 45 45 0 0 1 118 23" stroke="#2563eb" strokeWidth="4.5" strokeLinecap="round" />
    <path d="M 128 30 A 45 45 0 0 1 119 101" stroke="#0ea5e9" strokeWidth="4.5" strokeLinecap="round" />
    <path d="M 108 105 A 45 45 0 0 1 51 62" stroke="#6366f1" strokeWidth="4.5" strokeLinecap="round" />

    {/* Leaf nodes */}
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

    {/* Right-side tension & active bundle panel */}
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
  <svg
    className="chart-preview-svg"
    viewBox="0 0 280 124"
    fill="none"
    aria-hidden="true"
  >
    {/* Sort chip */}
    <rect x="34" y="6" width="54" height="15" rx="7.5" fill="#ffffff" stroke="#cbd5e1" />
    <text x="61" y="16" textAnchor="middle" fontSize="7.5" fontWeight="600" fill="#475569">
      Sort: Peak
    </text>

    {/* Marginal distribution header & baseline bars */}
    <text x="194" y="16" fontSize="7.5" fontWeight="700" fill="#64748b">
      Row Total
    </text>
    <rect x="194" y="26" width="38" height="13" rx="3" fill="#cbd5e1" />
    <rect x="194" y="46" width="64" height="13" rx="3" fill="#93c5fd" className="preview-anim-bar-r1" />
    <rect x="194" y="66" width="54" height="13" rx="3" fill="#93c5fd" className="preview-anim-bar-r2" />
    <rect x="194" y="86" width="46" height="13" rx="3" fill="#cbd5e1" />

    {/* Row & Col Labels */}
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

    {/* Matrix Cells */}
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

    {/* Animated cell inspector phase 1: C2 x Q4 */}
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

    {/* Animated cell inspector phase 2: C3 x Q2 */}
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

export const ChartViewPreviewCard: React.FC<ChartViewPreviewCardProps> = ({
  id,
  item,
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
        {item.id === 'trend' && <TrendPreviewSvg gradId={gradId} />}
        {item.id === 'network' && <NetworkPreviewSvg />}
        {item.id === 'bundling' && <BundlingPreviewSvg />}
        {item.id === 'heatmap' && <HeatmapPreviewSvg />}
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
      </div>
    </div>
  );
};
