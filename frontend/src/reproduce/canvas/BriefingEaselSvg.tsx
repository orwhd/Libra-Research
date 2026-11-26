import React from 'react';

interface BriefingEaselSvgProps {
  cx: number;
  cy: number;
  topic: string;
  motionDisabled: boolean;
  onPointerEnter?: (e: React.PointerEvent<SVGGElement>) => void;
  onPointerLeave?: () => void;
  onClick?: () => void;
}

/**
 * Hand-drawn PPT whiteboard easel positioned between Main Agent and reporting subagent(s),
 * modeled after the user's sketch: white board, black ink top clamp & splayed tripod/A-frame legs,
 * bottom ledge bar, and central expressive black loopy diagram scribble.
 */
export const BriefingEaselSvg: React.FC<BriefingEaselSvgProps> = ({
  cx,
  cy,
  topic,
  motionDisabled,
  onPointerEnter,
  onPointerLeave,
  onClick,
}) => {
  const boardW = 72;
  const boardH = 52;
  const x0 = cx - boardW / 2;
  const y0 = cy - boardH / 2 - 4;
  const x1 = cx + boardW / 2;
  const y1 = y0 + boardH;

  return (
    <g
      className={`rep-briefing-easel ${motionDisabled ? 'is-motion-disabled' : ''}`}
      data-testid="briefing-easel"
      role="button"
      tabIndex={0}
      aria-label={`Presentation whiteboard: ${topic}`}
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
      {/* Subtle warm ground shadow */}
      <ellipse cx={cx} cy={y1 + 23} rx={26} ry={4.5} className="rep-easel-shadow" />

      {/* Back top T-clamp holding board */}
      <path
        d={`M${cx} ${y0 - 9} L${cx} ${y0 + 1} M${cx - 12} ${y0} Q${cx} ${y0 - 1.5} ${cx + 12} ${y0}`}
        className="rep-easel-ink"
      />

      {/* Splayed A-frame legs */}
      <path
        d={`M${cx - 12} ${y1 + 2} L${cx - 21} ${y1 + 22} M${cx + 12} ${y1 + 2} L${cx + 21} ${y1 + 22}`}
        className="rep-easel-ink"
      />

      {/* White hand-drawn whiteboard surface */}
      <path
        d={`M${x0 + 2} ${y0 + 1} Q${cx} ${y0 - 1.5} ${x1 - 2} ${y0 + 1} Q${x1 + 1.5} ${
          (y0 + y1) / 2
        } ${x1 - 1.5} ${y1} Q${cx} ${y1 + 1.5} ${x0 + 1.5} ${y1} Q${x0 - 1.5} ${
          (y0 + y1) / 2
        } ${x0 + 2} ${y0 + 1} Z`}
        className="rep-easel-board"
      />

      {/* Bottom marker ledge bar */}
      <path
        d={`M${x0 + 12} ${y1 + 1.5} Q${cx} ${y1 + 2.8} ${x1 - 12} ${y1 + 1.5}`}
        className="rep-easel-ledge"
      />

      {/* Central expressive loopy sketch diagram (matching user's easel sketch) */}
      <path
        d={`M${cx - 21} ${cy - 10} C${cx - 11} ${cy - 4}, ${cx - 5} ${cy + 5}, ${cx + 4} ${
          cy + 4
        } C${cx + 14} ${cy + 3}, ${cx + 16} ${cy - 14}, ${cx + 4} ${cy - 15} C${cx - 8} ${
          cy - 16
        }, ${cx - 11} ${cy + 10}, ${cx + 1} ${cy + 10} C${cx + 13} ${cy + 10}, ${cx + 17} ${
          cy - 11
        }, ${cx + 6} ${cy - 11} C${cx - 4} ${cy - 11}, ${cx - 1} ${cy + 7}, ${cx + 15} ${
          cy + 13
        }`}
        pathLength={1}
        className="rep-easel-scribble"
      />
    </g>
  );
};
