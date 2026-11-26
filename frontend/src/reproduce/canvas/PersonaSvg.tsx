import React, { useEffect, useMemo, useRef } from 'react';
import type { AgentRole, Persona, VisualState } from '../types';

interface RoleVisualSpec {
  name: string;
  color: string;
  tint: string;
  hair: string;
  nose: string;
}

export const PERSONA_ROLE_SPECS: Record<string, RoleVisualSpec> = {
  main: {
    name: 'Main',
    color: '#FFD24A',
    tint: '#FFF6D8',
    hair: 'M62 52 C 66 30, 80 30, 83 46 C 86 24, 114 24, 117 46 C 120 30, 134 30, 138 52',
    nose: 'M100 94 C 98 108, 92 120, 94 126 C 98 130, 106 128, 110 126',
  },
  paper: {
    name: 'Paper',
    color: '#FF9FD8',
    tint: '#FFE4F5',
    hair: 'M70 58 C 96 52, 122 46, 124 26 C 126 10, 108 8, 108 24 C 108 42, 134 50, 160 36',
    nose: 'M98 94 C 104 108, 112 122, 110 128 C 106 132, 98 131, 92 131',
  },
  data: {
    name: 'Data',
    color: '#7CC8FF',
    tint: '#E1F2FF',
    hair: 'M80 46 C 80 36, 76 28, 72 20 M100 40 C 100 30, 100 22, 100 12 M120 46 C 120 36, 124 28, 128 20',
    nose: 'M102 96 C 102 108, 100 120, 96 128 C 100 131, 106 131, 110 129',
  },
  analysis: {
    name: 'Analysis',
    color: '#4FF5C8',
    tint: '#D8FFF2',
    hair: 'M40 26 C 52 56, 104 58, 170 20',
    nose: 'M108 96 C 102 108, 94 122, 96 128 C 98 132, 104 131, 110 131',
  },
  validation: {
    name: 'Validation',
    color: '#FF9A6B',
    tint: '#FFE9DE',
    hair: 'M48 62 C 60 34, 104 24, 140 34 C 152 38, 158 44, 162 52 M140 34 C 146 26, 152 22, 160 22',
    nose: 'M104 92 C 98 108, 90 124, 96 128 C 100 130, 106 129, 108 127',
  },
  support: {
    name: 'Support',
    color: '#B9A6FF',
    tint: '#EFEAFF',
    hair: 'M100 40 C 100 32, 100 24, 100 14 M100 24 C 92 14, 82 14, 78 20 C 86 26, 94 26, 100 24 M100 20 C 108 10, 120 10, 124 16 C 116 22, 106 22, 100 20',
    nose: 'M100 98 C 100 110, 98 120, 96 126 C 100 129, 104 129, 108 127',
  },
};

function mixHexWithWhite(hex: string, whiteRatio: number): string {
  const clean = hex.replace('#', '');
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  const mix = (ch: number) => Math.round(ch + (255 - ch) * Math.max(0, Math.min(0.92, whiteRatio)));
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}

export interface AgentBubbleStyle {
  bg: string;
  border: string;
  dot: string;
  isMain: boolean;
  instanceIndex: number;
}

/**
 * Derive group-chat bubble styling directly from the speaker's circle color,
 * progressively mixing more white for instance #2, #3, #4+ of the same role.
 */
export function getAgentBubbleStyle(speakerId: string, speakerLabel?: string): AgentBubbleStyle {
  const cleanId = (speakerId || '').trim();
  const isMain = cleanId === 'persona_main' || /^main\b/i.test(speakerLabel || '');

  let roleKey = 'support';
  if (isMain) {
    roleKey = 'main';
  } else {
    for (const key of Object.keys(PERSONA_ROLE_SPECS)) {
      if (cleanId.startsWith(`persona_${key}`) || new RegExp(`^${key}\\b`, 'i').test(speakerLabel || '')) {
        roleKey = key;
        break;
      }
    }
  }

  let instanceIndex = 1;
  const idMatch = cleanId.match(/_(\d+)$/);
  const labelMatch = (speakerLabel || '').match(/#\s*(\d+)/);
  if (idMatch) instanceIndex = Math.max(1, Number(idMatch[1]));
  else if (labelMatch) instanceIndex = Math.max(1, Number(labelMatch[1]));

  const whiteMix =
    instanceIndex <= 1
      ? 0.14
      : instanceIndex === 2
        ? 0.48
        : instanceIndex === 3
          ? 0.72
          : Math.min(0.86, 0.72 + (instanceIndex - 3) * 0.06);

  const baseHex = PERSONA_ROLE_SPECS[roleKey]?.color || '#B9A6FF';
  return {
    bg: mixHexWithWhite(baseHex, whiteMix),
    border: mixHexWithWhite(baseHex, Math.max(0, whiteMix - 0.12)),
    dot: baseHex,
    isMain,
    instanceIndex,
  };
}

interface ExprPose {
  eL: number[];
  eR: number[];
  bL: number[];
  bR: number[];
  mt: number[];
  mb: number[];
  gaze: [number, number];
  tilt: number;
}

const eyeDot = (x: number, y: number, w = 10) => [x, y, x, y, x + 0.01, y, w];
const eyeArc = (x: number, y: number, c: number, h = 10) => [x - h, y, x, y + c, x + h, y, 4.5];
const eyeNone = (x: number, y: number) => [x - 1, y, x, y, x + 1, y, 0];
const browL = (y: number, tilt = 0, curve = 2, op = 1, len = 24, x = 74) => [
  x - len / 2,
  y - tilt,
  x,
  y - curve,
  x + len / 2,
  y + tilt,
  op,
];
const browR = (y: number, tilt = 0, curve = 2, op = 1, len = 24, x = 126) => [
  x - len / 2,
  y + tilt,
  x,
  y - curve,
  x + len / 2,
  y - tilt,
  op,
];
const mouth = (cx: number, cy: number, w: number, d: number, open = 0, skew = 0) => ({
  mt: [cx - w, cy - skew, cx - w / 3, cy + d, cx + w / 3, cy + d, cx + w, cy + skew],
  mb: [
    cx - w,
    cy - skew,
    cx - w / 3,
    cy + d + open,
    cx + w / 3,
    cy + d + open,
    cx + w,
    cy + skew,
  ],
});
const wavy = (cx: number, cy: number, w: number, a: number) => {
  const p = [cx - w, cy, cx - w / 3, cy - a, cx + w / 3, cy + a, cx + w, cy];
  return { mt: p, mb: p.slice() };
};
const smirk = (cx: number, cy: number) => {
  const p = [cx - 16, cy + 2, cx - 4, cy + 6, cx + 8, cy + 5, cx + 20, cy - 5];
  return { mt: p, mb: p.slice() };
};
const X = (o: Omit<ExprPose, 'gaze' | 'tilt'> & Partial<Pick<ExprPose, 'gaze' | 'tilt'>>): ExprPose => ({
  gaze: [0, 0],
  tilt: 0,
  ...o,
});

const EXPR: Record<string, ExprPose> = {
  calm: X({ eL: eyeArc(74, 104, 8), eR: eyeArc(126, 104, 8), bL: browL(90, 0, 1, 0), bR: browR(90, 0, 1, 0), ...smirk(100, 148) }),
  alert: X({ eL: eyeDot(74, 104, 11.5), eR: eyeDot(126, 104, 11.5), bL: browL(81, -1, 4, 1), bR: browR(81, -1, 4, 1), ...mouth(100, 146, 6, -1, 10) }),
  focused: X({ eL: eyeDot(74, 106), eR: eyeDot(126, 106), bL: browL(92, 2.5, 0, 1, 22), bR: browR(92, 2.5, 0, 1, 22), ...mouth(102, 149, 9, 0), gaze: [3, 4] }),
  waiting: X({ eL: eyeDot(74, 104), eR: eyeDot(126, 104), bL: browL(89, 0, 1, 0), bR: browR(82, -2, 4, 1), ...wavy(100, 149, 12, 2), gaze: [-4, -6], tilt: -4 }),
  speaking: X({ eL: eyeDot(74, 104), eR: eyeDot(126, 104), bL: browL(88, 3, 1, 1), bR: browR(88, 3, 1, 1), ...mouth(100, 145, 15, 2, 12) }),
  listening: X({ eL: eyeDot(74, 104), eR: eyeDot(126, 104), bL: browL(86, -1, 3, 0.9), bR: browR(86, -1, 3, 0.9), ...mouth(100, 148, 11, 1), tilt: 3 }),
  reporting: X({ eL: eyeDot(74, 103), eR: eyeDot(126, 103), bL: browL(85, -1, 3, 0.8), bR: browR(85, -1, 3, 0.8), ...mouth(100, 143, 20, 8, 9) }),
  handoff: X({ eL: eyeDot(74, 104), eR: eyeDot(126, 104), bL: browL(87, 0, 2, 0.6), bR: browR(87, 0, 2, 0.6), ...mouth(100, 146, 15, 5), gaze: [5, 1] }),
  paused: X({ eL: eyeArc(74, 106, 0, 9), eR: eyeArc(126, 106, 0, 9), bL: browL(92, 0, 0, 0), bR: browR(92, 0, 0, 0), ...mouth(100, 149, 10, 0) }),
  failed: X({ eL: eyeNone(74, 104), eR: eyeNone(126, 104), bL: browL(86, -4, 2, 1), bR: browR(86, -4, 2, 1), ...wavy(100, 151, 16, 4) }),
  happy: X({ eL: eyeArc(74, 106, -13), eR: eyeArc(126, 106, -13), bL: browL(86, 0, 2, 0), bR: browR(86, 0, 2, 0), ...mouth(100, 140, 32, 17) }),
};

const STATE_TO_EXPR: Record<VisualState, string> = {
  idle: 'calm',
  assigned: 'alert',
  working: 'focused',
  waiting: 'waiting',
  debating: 'listening',
  reporting: 'reporting',
  handoff: 'handoff',
  paused: 'paused',
  failed: 'failed',
  completed: 'happy',
};

const f1 = (n: number) => Math.round(n * 10) / 10;
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function clonePose(e: ExprPose): ExprPose {
  return {
    eL: e.eL.slice(),
    eR: e.eR.slice(),
    bL: e.bL.slice(),
    bR: e.bR.slice(),
    mt: e.mt.slice(),
    mb: e.mb.slice(),
    gaze: [e.gaze[0], e.gaze[1]],
    tilt: e.tilt,
  };
}

function mixPose(a: ExprPose, b: ExprPose, k: number): ExprPose {
  const mixArr = (x: number[], y: number[]) => x.map((v, i) => v + (y[i] - v) * k);
  return {
    eL: mixArr(a.eL, b.eL),
    eR: mixArr(a.eR, b.eR),
    bL: mixArr(a.bL, b.bL),
    bR: mixArr(a.bR, b.bR),
    mt: mixArr(a.mt, b.mt),
    mb: mixArr(a.mb, b.mb),
    gaze: [a.gaze[0] + (b.gaze[0] - a.gaze[0]) * k, a.gaze[1] + (b.gaze[1] - a.gaze[1]) * k],
    tilt: a.tilt + (b.tilt - a.tilt) * k,
  };
}

function hashSeed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 100;
}

interface PersonaSvgProps {
  persona: Persona;
  worldX: number;
  worldY: number;
  isSpeakingPulse?: boolean;
  motionDisabled: boolean;
  onSilentNavigate?: (persona: Persona) => void;
}

/**
 * Pure React/SVG port of `reference/agent_motion/agent.js` adhering strictly to Frozen L1.5:
 * - Preserves exact role colors, pen strokes, hair/nose paths, Main orbit ring (~1.2x scale), and facial morphing.
 * - Zero reaction on click, hover, or focus: non-interactive status actor with zero write API calls.
 */
export const PersonaSvg: React.FC<PersonaSvgProps> = ({
  persona,
  worldX,
  worldY,
  isSpeakingPulse = false,
  motionDisabled,
}) => {
  const roleKey: AgentRole = persona.role in PERSONA_ROLE_SPECS ? persona.role : 'support';
  const spec = PERSONA_ROLE_SPECS[roleKey];
  const seed = useMemo(() => hashSeed(persona.persona_id), [persona.persona_id]);
  const breathDelay = useMemo(() => `${-(seed * 0.45).toFixed(2)}s`, [seed]);
  const breathDuration = useMemo(() => `${(4.4 + (seed % 1.4)).toFixed(2)}s`, [seed]);

  const eLRef = useRef<SVGPathElement>(null);
  const eRRef = useRef<SVGPathElement>(null);
  const bLRef = useRef<SVGPathElement>(null);
  const bRRef = useRef<SVGPathElement>(null);
  const mouthRef = useRef<SVGPathElement>(null);
  const noseRef = useRef<SVGPathElement>(null);
  const faceRef = useRef<SVGGElement>(null);

  const curPoseRef = useRef<ExprPose>(clonePose(EXPR[STATE_TO_EXPR[persona.state] || 'calm']));
  const tweenRef = useRef<{ from: ExprPose; to: ExprPose; t0: number; dur: number } | null>(null);
  const blinkRef = useRef<{ nextAt: number; startAt: number }>({
    nextAt: 1600 + (seed % 3) * 1100,
    startAt: -1e9,
  });

  const targetExprKey =
    persona.state === 'debating' && isSpeakingPulse
      ? 'speaking'
      : STATE_TO_EXPR[persona.state] || 'calm';

  useEffect(() => {
    const targetPose = EXPR[targetExprKey] || EXPR.calm;
    if (motionDisabled) {
      curPoseRef.current = clonePose(targetPose);
      tweenRef.current = null;
    } else {
      tweenRef.current = {
        from: clonePose(curPoseRef.current),
        to: clonePose(targetPose),
        t0: performance.now(),
        dur: 340,
      };
    }
  }, [targetExprKey, motionDisabled]);

  useEffect(() => {
    const applyPoseToDom = (now: number) => {
      if (tweenRef.current) {
        const k = Math.min(1, (now - tweenRef.current.t0) / tweenRef.current.dur);
        curPoseRef.current = mixPose(tweenRef.current.from, tweenRef.current.to, ease(k));
        if (k >= 1) tweenRef.current = null;
      }
      const f = clonePose(curPoseRef.current);
      let gx = f.gaze[0];
      const gy = f.gaze[1];

      // Subtle state-driven gaze only (zero interaction reactivity): reporting specialists glance left toward Main & easel
      if ((persona.state === 'reporting' || persona.state === 'handoff') && !persona.is_main) {
        gx -= 3.5;
      }

      if (!motionDisabled) {
        const tSec = now / 1000;
        if (persona.state === 'working') {
          gx += Math.sin(tSec * 1.4 + seed) * 1.8;
        }
        if (now > blinkRef.current.nextAt) {
          blinkRef.current.startAt = now;
          blinkRef.current.nextAt = now + 3200 + ((seed * 430) % 2800);
        }
        const bk = (now - blinkRef.current.startAt) / 150;
        if (bk >= 0 && bk < 1) {
          const b = Math.sin(bk * Math.PI);
          for (const eye of [f.eL, f.eR]) {
            if (eye[6] >= 6) {
              const cx = (eye[0] + eye[4]) / 2;
              const cy = (eye[1] + eye[5]) / 2;
              const closed = [cx - 7, cy, cx, cy, cx + 7, cy, 4.5];
              for (let i = 0; i < 7; i += 1) {
                eye[i] += (closed[i] - eye[i]) * b;
              }
            }
          }
        }
      }

      const writeQuad = (el: SVGPathElement | null, arr: number[], dx: number, dy: number, isEye: boolean) => {
        if (!el) return;
        el.setAttribute(
          'd',
          `M${f1(arr[0] + dx)} ${f1(arr[1] + dy)}Q${f1(arr[2] + dx)} ${f1(arr[3] + dy)} ${f1(arr[4] + dx)} ${f1(arr[5] + dy)}`
        );
        if (isEye) {
          el.style.strokeWidth = String(f1(arr[6]));
          el.style.opacity = arr[6] < 0.6 ? '0' : '1';
        } else {
          el.style.opacity = String(f1(arr[6]));
        }
      };

      writeQuad(eLRef.current, f.eL, gx, gy, true);
      writeQuad(eRRef.current, f.eR, gx, gy, true);
      writeQuad(bLRef.current, f.bL, gx * 0.6, gy * 0.6, false);
      writeQuad(bRRef.current, f.bR, gx * 0.6, gy * 0.6, false);

      if (mouthRef.current) {
        const m = f.mt;
        const b = f.mb;
        const dx = gx * 0.5;
        const dy = gy * 0.4;
        mouthRef.current.setAttribute(
          'd',
          `M${f1(m[0] + dx)} ${f1(m[1] + dy)}C${f1(m[2] + dx)} ${f1(m[3] + dy)} ${f1(m[4] + dx)} ${f1(m[5] + dy)} ${f1(m[6] + dx)} ${f1(m[7] + dy)}C${f1(b[4] + dx)} ${f1(b[5] + dy)} ${f1(b[2] + dx)} ${f1(b[3] + dy)} ${f1(b[0] + dx)} ${f1(b[1] + dy)}Z`
        );
      }
      noseRef.current?.setAttribute('transform', `translate(${f1(gx * 0.6)} ${f1(gy * 0.5)})`);
      faceRef.current?.setAttribute('transform', `rotate(${f1(f.tilt)} 100 112)`);
    };

    applyPoseToDom(performance.now());
    if (motionDisabled) {
      return undefined;
    }

    let raf = 0;
    const tick = (now: number) => {
      applyPoseToDom(now);
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [persona.state, targetExprKey, motionDisabled, seed]);

  // Main is ~20% larger than research agents (L1.5 §1 & §25)
  const widthPct = persona.is_main ? 10.6 : 8.8;
  const leftPct = (worldX / 1000) * 100;
  const topPct = (worldY / 600) * 100;

  return (
    <div
      role="img"
      className={`rep-persona-node role-${roleKey} state-${persona.state} ${persona.is_main ? 'is-main' : ''}`}
      data-testid={`persona-${persona.persona_id}`}
      data-persona-id={persona.persona_id}
      data-persona-state={persona.state}
      data-world-x={Math.round(worldX)}
      data-world-y={Math.round(worldY)}
      aria-label={`${persona.label} (${persona.state})`}
      style={
        {
          left: `${leftPct.toFixed(3)}%`,
          top: `${topPct.toFixed(3)}%`,
          width: `${widthPct}%`,
          '--persona-fill': spec.color,
          '--persona-tint': spec.tint,
          '--breath-delay': breathDelay,
          '--breath-dur': breathDuration,
        } as React.CSSProperties
      }
    >
      <svg viewBox="-40 -30 280 260" aria-hidden="true" className="rep-persona-svg">
        <g className="rep-persona-body">
          {persona.is_main && (
            <path
              className="rep-ink rep-orbit"
              d="M18 128 C 8 60, 70 14, 128 24 C 186 34, 206 100, 186 152 C 166 200, 92 214, 48 186"
            />
          )}
          <circle className="rep-fill" cx="100" cy="112" r="76" />
          <g ref={faceRef} className="rep-face">
            <path className="rep-ink rep-hair" d={spec.hair} />
            <path ref={bLRef} className="rep-ink rep-brow" />
            <path ref={bRRef} className="rep-ink rep-brow" />
            <path ref={eLRef} className="rep-ink rep-eye" />
            <path ref={eRRef} className="rep-ink rep-eye" />
            <path ref={noseRef} className="rep-ink rep-nose" d={spec.nose} />
            <path ref={mouthRef} className="rep-mouth" />
          </g>
          {persona.state === 'working' && (
            <g className="rep-ov-working">
              <path
                className="rep-ink rep-thin"
                d="M166 190 C 174 180, 180 196, 188 186 S 202 194, 212 184"
              />
            </g>
          )}
          {persona.state === 'waiting' && (
            <g className="rep-ov-waiting">
              <circle cx="186" cy="42" r="4.5" fill="#1b1b1f" />
              <circle cx="202" cy="32" r="4.5" fill="#1b1b1f" opacity="0.6" />
              <circle cx="218" cy="22" r="4.5" fill="#1b1b1f" opacity="0.35" />
            </g>
          )}
          {persona.state === 'paused' && (
            <g className="rep-ov-paused">
              <path className="rep-ink" d="M190 28 L190 56 M206 28 L206 56" />
            </g>
          )}
          {persona.state === 'failed' && (
            <g className="rep-ov-failed">
              <path
                className="rep-ink"
                d="M66 96 L82 112 M82 96 L66 112 M118 96 L134 112 M134 96 L118 112"
              />
            </g>
          )}
          {persona.state === 'completed' && (
            <g className="rep-ov-completed">
              <path className="rep-ink" d="M180 46 L193 60 L218 26" />
            </g>
          )}
        </g>
      </svg>
      <span className="rep-persona-tag">
        <span className="rep-persona-name">{persona.label}</span>
        <span className="rep-persona-status">{persona.state}</span>
      </span>
    </div>
  );
};
