import React from 'react';

export interface SpeechFlow {
  id: string;
  from: { x: number; y: number };
  center: { x: number; y: number };
  rx: number;
  ry: number;
}

export interface HandoffFlow {
  id: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
}

interface AgentInteractionFlowsProps {
  speechFlows: SpeechFlow[];
  handoffFlows: HandoffFlow[];
  motionDisabled: boolean;
}

const r1 = (n: number) => Math.round(n * 10) / 10;

function arcAround(cx: number, cy: number, r: number, angle: number, span: number): string {
  const a0 = angle - span;
  const a1 = angle + span;
  const x0 = cx + Math.cos(a0) * r;
  const y0 = cy + Math.sin(a0) * r;
  const x1 = cx + Math.cos(a1) * r;
  const y1 = cy + Math.sin(a1) * r;
  return `M${r1(x0)} ${r1(y0)} A${r} ${r} 0 0 1 ${r1(x1)} ${r1(y1)}`;
}

/**
 * Transient, pen-drawn interaction cues between agents (rendered in 1000x600 world space, under personas):
 * - Speech: ripple arcs radiate from the current speaker toward the table, and ink dots travel
 *   from the speaker into the table's shared context (where the pen motif is writing).
 * - Handoff / presenting: a small paper sheet hops along a dotted arc from presenter to its recipient.
 * Nothing persists: cues exist only while the underlying state is active (no permanent graph edges).
 */
export const AgentInteractionFlows: React.FC<AgentInteractionFlowsProps> = ({
  speechFlows,
  handoffFlows,
  motionDisabled,
}) => {
  if (motionDisabled || (speechFlows.length === 0 && handoffFlows.length === 0)) return null;

  return (
    <g className="rep-interaction-flows" data-testid="agent-interaction-flows" pointerEvents="none">
      {speechFlows.map((sf) => {
        const vx = sf.center.x - sf.from.x;
        const vy = sf.center.y - sf.from.y;
        const len = Math.hypot(vx, vy) || 1;
        const ux = vx / len;
        const uy = vy / len;
        const angle = Math.atan2(vy, vx);
        // Rim distance from table center along the speaker direction
        const rimFromCenter = 1 / Math.sqrt((ux / sf.rx) ** 2 + (uy / sf.ry) ** 2);
        const start = { x: sf.from.x + ux * 34, y: sf.from.y + uy * 34 };
        const endDist = Math.max(40, len - rimFromCenter * 0.45);
        const end = { x: sf.from.x + ux * endDist, y: sf.from.y + uy * endDist };
        // Slight curvature so the ink stream feels hand-drawn
        const mid = { x: (start.x + end.x) / 2 - uy * 10, y: (start.y + end.y) / 2 + ux * 10 };
        const streamPath = `M${r1(start.x)} ${r1(start.y)} Q${r1(mid.x)} ${r1(mid.y)} ${r1(end.x)} ${r1(end.y)}`;
        return (
          <g key={`speech-${sf.id}`} className="rep-speech-flow" data-testid={`speech-flow-${sf.id}`}>
            {[0, 1, 2].map((i) => (
              <path
                key={`ripple-${i}`}
                className={`rep-speech-ripple ripple-${i}`}
                d={arcAround(sf.from.x, sf.from.y, 38 + i * 11, angle, 0.5 - i * 0.06)}
              />
            ))}
            {[0, 1, 2].map((i) => (
              <circle key={`ink-${i}`} className="rep-speech-ink" r={2.6 - i * 0.4} opacity={0}>
                <animateMotion
                  dur="1.5s"
                  begin={`${(i * 0.5).toFixed(2)}s`}
                  repeatCount="indefinite"
                  path={streamPath}
                  calcMode="spline"
                  keyPoints="0;1"
                  keyTimes="0;1"
                  keySplines="0.45 0 0.25 1"
                />
                <animate
                  attributeName="opacity"
                  dur="1.5s"
                  begin={`${(i * 0.5).toFixed(2)}s`}
                  repeatCount="indefinite"
                  values="0;0.85;0.85;0"
                  keyTimes="0;0.15;0.75;1"
                />
              </circle>
            ))}
          </g>
        );
      })}
      {handoffFlows.map((hf) => {
        const dx = hf.to.x - hf.from.x;
        const dy = hf.to.y - hf.from.y;
        const len = Math.hypot(dx, dy) || 1;
        const ux = dx / len;
        const uy = dy / len;
        const start = { x: hf.from.x + ux * 30, y: hf.from.y + uy * 30 - 8 };
        const end = { x: hf.to.x - ux * 30, y: hf.to.y - uy * 30 - 8 };
        const lift = Math.min(60, 22 + len * 0.18);
        const ctrl = { x: (start.x + end.x) / 2, y: Math.min(start.y, end.y) - lift };
        const arcPath = `M${r1(start.x)} ${r1(start.y)} Q${r1(ctrl.x)} ${r1(ctrl.y)} ${r1(end.x)} ${r1(end.y)}`;
        return (
          <g key={`handoff-${hf.id}`} className="rep-handoff-flow" data-testid={`handoff-flow-${hf.id}`}>
            <path className="rep-handoff-trail" d={arcPath} />
            <g className="rep-handoff-sheet" opacity={0}>
              <path className="rep-handoff-paper" d="M-6 -8 L3 -8 L7 -4 L7 8 L-6 8 Z M3 -8 L3 -4 L7 -4" />
              <path className="rep-handoff-lines" d="M-3 -1 L4 -1 M-3 3 L3 3" />
              <animateMotion
                dur="2.2s"
                repeatCount="indefinite"
                path={arcPath}
                calcMode="spline"
                keyPoints="0;1"
                keyTimes="0;1"
                keySplines="0.5 0 0.3 1"
              />
              <animate
                attributeName="opacity"
                dur="2.2s"
                repeatCount="indefinite"
                values="0;1;1;0"
                keyTimes="0;0.12;0.82;1"
              />
            </g>
          </g>
        );
      })}
    </g>
  );
};
