import React from 'react';

interface InterventionMailSvgProps {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  isResolved: boolean;
  motionDisabled: boolean;
  onPointerEnter?: (e: React.PointerEvent<SVGGElement>) => void;
  onPointerLeave?: () => void;
  onClick?: () => void;
}

/**
 * Hand-drawn Email / Envelope icon sent along a curved trajectory from a dissenting
 * debating subagent table toward Main Agent to explicitly request human intervention.
 */
export const InterventionMailSvg: React.FC<InterventionMailSvgProps> = ({
  fromX,
  fromY,
  toX,
  toY,
  isResolved,
  motionDisabled,
  onPointerEnter,
  onPointerLeave,
  onClick,
}) => {
  const midX = (fromX + toX) / 2;
  const ctrlY = Math.min(fromY, toY) - 66;
  const t = 0.56;
  const omt = 1 - t;
  const mailX = omt * omt * fromX + 2 * omt * t * midX + t * t * toX;
  const mailY = omt * omt * fromY + 2 * omt * t * ctrlY + t * t * toY;

  const w = 38;
  const h = 26;
  const x0 = mailX - w / 2;
  const y0 = mailY - h / 2;

  return (
    <g
      className={`rep-intervention-mail ${isResolved ? 'is-resolved' : 'is-pending'} ${
        motionDisabled ? 'is-motion-disabled' : ''
      }`}
    >
      {/* Curved dashed hand-drawn trajectory arc from debate table to Main */}
      <path
        d={`M${fromX.toFixed(1)} ${fromY.toFixed(1)} Q${midX.toFixed(1)} ${ctrlY.toFixed(1)} ${toX.toFixed(1)} ${toY.toFixed(1)}`}
        className="rep-mail-trajectory"
        pointerEvents="none"
      />

      {/* Interactive Envelope / Email icon hitbox */}
      <g
        className="rep-mail-envelope"
        data-testid="intervention-mail-icon"
        role="button"
        tabIndex={0}
        pointerEvents="all"
        aria-label="Human intervention request email sent to Main Agent — click to answer in Chat"
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
        onClick={(e) => {
          e.stopPropagation();
          onClick?.();
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            onClick?.();
          }
        }}
      >
        <rect
          x={x0 - 4}
          y={y0 - 4}
          width={w + 8}
          height={h + 8}
          rx={6}
          fill="rgba(255,255,255,0.01)"
        />
        <rect
          x={x0}
          y={y0}
          width={w}
          height={h}
          rx={4}
          className="rep-mail-body"
        />
        <path
          d={`M${x0 + 2} ${y0 + 2} L${mailX} ${y0 + h * 0.56} L${x0 + w - 2} ${y0 + 2}`}
          className="rep-mail-flap"
        />
        <circle
          cx={x0 + w - 2}
          cy={y0 + 2}
          r={6}
          className="rep-mail-badge"
        />
        <text
          x={x0 + w - 2}
          y={y0 + 5.2}
          textAnchor="middle"
          className="rep-mail-badge-text"
        >
          {isResolved ? '✓' : '!'}
        </text>
      </g>
    </g>
  );
};
