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
    name: 'Boss',
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
  const isMain = cleanId === 'persona_main' || /^(boss|main)\b/i.test(speakerLabel || '');

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

export type PersonaInteractionRole = 'speaker' | 'listener' | 'presenter' | 'audience' | 'seated' | null;

export interface WorldPoint {
  x: number;
  y: number;
}

interface PersonaSvgProps {
  persona: Persona;
  worldX: number;
  worldY: number;
  isSpeakingPulse?: boolean;
  motionDisabled: boolean;
  /** Ordered world-space attention targets (speaker, table center, Main, other members). */
  lookTargets?: WorldPoint[];
  /** Role of this persona in the current visible interaction (drives lean, nods, bob). */
  interaction?: PersonaInteractionRole;
  /** Responsive size multiplier derived from the canvas width. */
  sizeScale?: number;
  /** Parked on the edge bench after completion: quieter, smaller status glyph. */
  benched?: boolean;
  /**
   * Pointer interaction mode:
   * - 'none': solo Live actor (`pointer-events: none`, UI-03 invariant)
   * - 'group_click': Live actor inside a table/briefing composite entity (hover/click delegates to group)
   * - 'replay_drag': Timeline replay actor (draggable individually or as part of its composite group)
   */
  interactiveMode?: 'none' | 'group_click' | 'replay_drag';
  inCompositeGroup?: boolean;
  isDragging?: boolean;
  onDragPointerDown?: (persona: Persona, e: React.PointerEvent<HTMLDivElement>) => void;
  onPersonaPointerEnter?: (persona: Persona, e: React.PointerEvent<HTMLDivElement>) => void;
  onPersonaPointerLeave?: (persona: Persona) => void;
  onPersonaClick?: (persona: Persona, e: React.MouseEvent<HTMLDivElement>) => void;
  onSilentNavigate?: (persona: Persona) => void;
}

/** Deterministic pseudo-random in [0, 1) from an integer key + seed. */
function rand01(key: number, seed: number): number {
  const v = Math.sin(key * 127.1 + seed * 311.7) * 43758.5453;
  return v - Math.floor(v);
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const WALK_DURATION_MS = 880;
const POP_DURATION_MS = 520;

/**
 * Pure React/SVG port of `reference/agent_motion/agent.js` adhering strictly to Frozen L1.5:
 * - Preserves exact role colors, pen strokes, hair/nose paths, Main orbit ring (~1.2x scale), and facial morphing.
 * - Zero reaction on click, hover, or focus: non-interactive status actor with zero write API calls.
 * - Ambient motion layer (all rAF-driven, disabled under reduced motion):
 *   attention gaze toward speaker / table / Main, idle saccades, walking hops with lean + squash,
 *   state-change anticipation pop, speaker lip-flap + bob, listener nods, waiting sway, failure droop.
 */
export const PersonaSvg: React.FC<PersonaSvgProps> = ({
  persona,
  worldX,
  worldY,
  isSpeakingPulse = false,
  motionDisabled,
  lookTargets,
  interaction = null,
  sizeScale = 1,
  benched = false,
  interactiveMode = 'none',
  inCompositeGroup = false,
  isDragging = false,
  onDragPointerDown,
  onPersonaPointerEnter,
  onPersonaPointerLeave,
  onPersonaClick,
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
  const headRef = useRef<SVGGElement>(null);
  const motionRef = useRef<SVGGElement>(null);
  const shadowRef = useRef<SVGEllipseElement>(null);
  const speechRef = useRef<SVGGElement>(null);

  const curPoseRef = useRef<ExprPose>(clonePose(EXPR[STATE_TO_EXPR[persona.state] || 'calm']));
  const tweenRef = useRef<{ from: ExprPose; to: ExprPose; t0: number; dur: number } | null>(null);
  const blinkRef = useRef<{ nextAt: number; startAt: number; double: boolean }>({
    nextAt: 1600 + (seed % 3) * 1100,
    startAt: -1e9,
    double: false,
  });

  // Live, frame-loop-readable copies of props (avoid restarting the rAF loop on every render)
  const worldRef = useRef<WorldPoint>({ x: worldX, y: worldY });
  const targetsRef = useRef<WorldPoint[]>(lookTargets || []);
  const interactionRef = useRef<PersonaInteractionRole>(interaction);
  const stateRef = useRef<VisualState>(persona.state);
  worldRef.current = { x: worldX, y: worldY };
  targetsRef.current = lookTargets || [];
  interactionRef.current = interaction;
  stateRef.current = persona.state;

  const walkRef = useRef<{ t0: number; dx: number; dy: number } | null>(null);
  const prevWorldRef = useRef<WorldPoint | null>(null);
  const popRef = useRef<{ t0: number; state: VisualState } | null>(null);
  const prevStateRef = useRef<VisualState>(persona.state);
  const dyn = useRef({ gx: 0, gy: 0, lean: 0, leanY: 0, droop: 0, facing: 1, lastNow: 0 });

  const isSpeaking = persona.state === 'debating' && isSpeakingPulse;
  const targetExprKey = isSpeaking ? 'speaking' : STATE_TO_EXPR[persona.state] || 'calm';

  // Walking detection: world anchor moved -> hop + lean along the travel vector (matches CSS left/top transition)
  useEffect(() => {
    const prev = prevWorldRef.current;
    prevWorldRef.current = { x: worldX, y: worldY };
    if (isDragging) {
      walkRef.current = null;
      return;
    }
    if (!prev || motionDisabled) return;
    const dx = worldX - prev.x;
    const dy = worldY - prev.y;
    if (Math.hypot(dx, dy) > 4) {
      walkRef.current = { t0: performance.now(), dx, dy };
    }
  }, [worldX, worldY, motionDisabled, isDragging]);

  // State-change anticipation pop (squash -> overshoot -> settle)
  useEffect(() => {
    if (prevStateRef.current !== persona.state) {
      prevStateRef.current = persona.state;
      if (!motionDisabled) popRef.current = { t0: performance.now(), state: persona.state };
    }
  }, [persona.state, motionDisabled]);

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
      const d = dyn.current;
      const dt = d.lastNow ? clamp((now - d.lastNow) / 1000, 0, 0.1) : 1 / 60;
      d.lastNow = now;
      const tSec = now / 1000;
      const state = stateRef.current;
      const role = interactionRef.current;
      const me = worldRef.current;

      if (tweenRef.current) {
        const k = Math.min(1, (now - tweenRef.current.t0) / tweenRef.current.dur);
        curPoseRef.current = mixPose(tweenRef.current.from, tweenRef.current.to, ease(k));
        if (k >= 1) tweenRef.current = null;
      }
      const f = clonePose(curPoseRef.current);

      // ---------- Attention / gaze ----------
      let wantGx = 0;
      let wantGy = 0;
      let facingDir = 0;
      const walk = walkRef.current;
      const walkK = walk ? (now - walk.t0) / WALK_DURATION_MS : 1;
      if (walk && walkK >= 1) walkRef.current = null;

      if (!motionDisabled) {
        if (walk && walkK < 1) {
          const len = Math.hypot(walk.dx, walk.dy) || 1;
          wantGx = (walk.dx / len) * 6;
          wantGy = (walk.dy / len) * 3.5;
          facingDir = Math.sign(walk.dx);
        } else {
          const targets = targetsRef.current;
          if (targets.length > 0) {
            // Cycle attention between targets; primary target holds longer
            const slot = Math.floor((tSec + seed) / 2.4);
            const pick = targets.length === 1 || rand01(slot, seed) < 0.62 ? 0 : 1 + Math.floor(rand01(slot + 7, seed) * (targets.length - 1));
            const tgt = targets[Math.min(pick, targets.length - 1)];
            const vx = tgt.x - me.x;
            const vy = tgt.y - me.y;
            const len = Math.hypot(vx, vy);
            if (len > 1) {
              wantGx = (vx / len) * 5.5;
              wantGy = (vy / len) * 3.8;
              facingDir = Math.abs(vx) > 6 ? Math.sign(vx) : 0;
            }
          } else if (state === 'working') {
            // Reading / scanning a document: left-right sweep, eyes slightly lowered, line-return snap
            const sweep = ((tSec * 0.55 + seed) % 1 + 1) % 1;
            wantGx = sweep < 0.82 ? -4 + (sweep / 0.82) * 8 : 4 - ((sweep - 0.82) / 0.18) * 8;
            wantGy = 3.2;
          } else if (state === 'idle' || state === 'completed' || state === 'waiting') {
            // Idle saccades: hold a random glance, then snap to the next one
            const slot = Math.floor((tSec + seed * 0.7) / (2.2 + (seed % 1.3)));
            wantGx = (rand01(slot, seed) - 0.5) * 6;
            wantGy = (rand01(slot + 13, seed) - 0.5) * 4;
            if (state === 'waiting') wantGy -= 3;
          }
        }
        const gk = 1 - Math.exp(-dt * (walk ? 9 : 14));
        d.gx += (wantGx - d.gx) * gk;
        d.gy += (wantGy - d.gy) * gk;
      } else {
        d.gx = 0;
        d.gy = 0;
      }
      if (facingDir !== 0) d.facing = facingDir;

      const gx = f.gaze[0] + d.gx;
      const gy = f.gaze[1] + d.gy;

      // ---------- Blink (with occasional double-blink) ----------
      if (!motionDisabled) {
        if (now > blinkRef.current.nextAt) {
          blinkRef.current.startAt = now;
          blinkRef.current.double = rand01(Math.floor(now / 997), seed) < 0.22;
          blinkRef.current.nextAt = now + 2800 + ((seed * 430 + now * 0.37) % 3200);
        }
        const since = now - blinkRef.current.startAt;
        let bk = since / 150;
        if (blinkRef.current.double && bk >= 1.4 && bk < 2.4) bk -= 1.4;
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

      // ---------- Speaking lip-flap & expressive brows ----------
      let flap = 0;
      if (!motionDisabled && isSpeaking) {
        flap = Math.abs(Math.sin(tSec * 9.2 + Math.sin(tSec * 2.3) * 1.6)) * (0.55 + 0.45 * Math.abs(Math.sin(tSec * 1.7 + seed)));
        const k = 0.22 + 0.78 * flap;
        f.mb[3] = f.mt[3] + (f.mb[3] - f.mt[3]) * k;
        f.mb[5] = f.mt[5] + (f.mb[5] - f.mt[5]) * k;
        const lift = flap * 2.4;
        for (const br of [f.bL, f.bR]) {
          br[1] -= lift;
          br[3] -= lift;
          br[5] -= lift;
        }
      } else if (!motionDisabled && role === 'listener') {
        // Attentive listener: brows gently raise in sync with nods
        const lift = Math.max(0, Math.sin(tSec * 2.6 + seed)) * 1.2;
        for (const br of [f.bL, f.bR]) {
          br[1] -= lift;
          br[3] -= lift;
          br[5] -= lift;
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
      // State tilt only (kept static per state so the persona never "reacts" to pointer input)
      faceRef.current?.setAttribute('transform', `rotate(${f1(f.tilt)} 100 112)`);

      // ---------- Head & body motion ----------
      let headRot = 0;
      let headY = 0;
      let tx = 0;
      let ty = 0;
      let rot = 0;
      let sx = 1;
      let sy = 1;
      let shadowScale = 1;

      if (!motionDisabled) {
        // Lean toward the attention target while engaged in an interaction
        const leanAmt =
          role === 'speaker' ? 8 : role === 'listener' ? 5 : role === 'presenter' ? 6 : role === 'audience' ? 3 : role === 'seated' ? 3.5 : 0;
        const wantLean = d.facing * leanAmt * (Math.abs(d.gx) > 1 ? 1 : 0.4);
        const wantDroop = state === 'failed' ? 7 : state === 'paused' ? 3 : 0;
        const lk = 1 - Math.exp(-dt * 5);
        d.lean += (wantLean - d.lean) * lk;
        d.droop += (wantDroop - d.droop) * lk;
        tx += d.lean;
        rot += d.lean * 0.55;
        ty += d.droop;
        rot += state === 'failed' ? -d.droop * 0.5 : 0;

        if (role === 'speaker') {
          // Emphatic speaking bob + slight sway, synced loosely with lip flap
          ty -= Math.abs(Math.sin(tSec * 4.6 + seed)) * 2.6;
          rot += Math.sin(tSec * 2.3 + seed) * 2.2;
          headRot += Math.sin(tSec * 3.4 + seed) * 2.4;
          headY -= flap * 1.6;
        } else if (role === 'listener' || role === 'audience') {
          // Periodic double-nod
          const period = 2.6 + (seed % 1.1);
          const ph = ((tSec + seed * 0.9) % period) / period;
          if (ph < 0.26) {
            const n = Math.sin((ph / 0.26) * Math.PI * 2);
            headY += Math.max(0, n) * 4.2;
            headRot += Math.max(0, n) * 1.6 * d.facing;
          }
        } else if (role === 'presenter') {
          // Presenting: open posture, gentle gesture rhythm
          ty -= Math.abs(Math.sin(tSec * 3.1 + seed)) * 1.8;
          headRot += Math.sin(tSec * 1.9 + seed) * 2.8;
        } else if (state === 'working') {
          // Typing bob with occasional "aha" lift
          headY += Math.sin(tSec * 7.4 + seed) * 0.9;
          const aha = ((tSec + seed) % 6.5) / 6.5;
          if (aha < 0.08) ty -= Math.sin((aha / 0.08) * Math.PI) * 4;
        } else if (state === 'waiting') {
          rot += Math.sin(tSec * 1.3 + seed) * 3.2;
          headRot += Math.sin(tSec * 0.9 + seed) * 2;
        } else if (state === 'assigned') {
          // Ready stance: small eager bounce
          ty -= Math.abs(Math.sin(tSec * 5.2 + seed)) * 1.6;
        } else if (state === 'idle') {
          headRot += Math.sin(tSec * 0.6 + seed) * 1.4;
        }

        // Walking: three hops with lean into travel direction + landing squash
        if (walk && walkK < 1) {
          const len = Math.hypot(walk.dx, walk.dy) || 1;
          const dirX = walk.dx / len;
          const hopsPhase = walkK * Math.PI * 3;
          const hop = Math.abs(Math.sin(hopsPhase)) * 10 * (1 - walkK * 0.35);
          ty -= hop;
          rot += dirX * 9 * Math.sin(walkK * Math.PI);
          const contact = 1 - Math.abs(Math.sin(hopsPhase));
          const squash = contact * 0.07 * (1 - walkK * 0.5);
          sx *= 1 + squash;
          sy *= 1 - squash;
          shadowScale *= 1 - (hop / 10) * 0.35;
        }

        // State-change pop: anticipation dip then overshoot
        const pop = popRef.current;
        if (pop) {
          const pk = (now - pop.t0) / POP_DURATION_MS;
          if (pk >= 1) {
            popRef.current = null;
          } else {
            const s =
              pk < 0.22
                ? 1 - 0.06 * Math.sin((pk / 0.22) * Math.PI * 0.5)
                : 0.94 + 0.06 + 0.07 * Math.sin(((pk - 0.22) / 0.78) * Math.PI) * (1 - (pk - 0.22) / 0.78);
            sx *= pk < 0.22 ? 2 - s : s;
            sy *= s;
            if (pop.state === 'completed' && pk >= 0.22) {
              const jump = Math.sin(((pk - 0.22) / 0.78) * Math.PI) * 14;
              ty -= jump;
              shadowScale *= 1 - (jump / 14) * 0.4;
            }
            if (pop.state === 'failed') {
              tx += Math.sin(pk * Math.PI * 8) * 4 * (1 - pk);
            }
          }
        }
      }

      motionRef.current?.setAttribute(
        'transform',
        `translate(${f1(tx)} ${f1(ty)}) rotate(${f1(rot)} 100 188) translate(100 188) scale(${(Math.round(sx * 1000) / 1000).toString()} ${(Math.round(sy * 1000) / 1000).toString()}) translate(-100 -188)`
      );
      headRef.current?.setAttribute('transform', `translate(0 ${f1(headY)}) rotate(${f1(headRot)} 100 150)`);
      if (shadowRef.current) {
        shadowRef.current.setAttribute('transform', `translate(${f1(100 + tx * 0.6)} 194) scale(${f1(shadowScale * 100) / 100} 1)`);
        shadowRef.current.style.opacity = String(f1(0.14 + 0.1 * shadowScale) );
      }
      if (speechRef.current) {
        speechRef.current.setAttribute('transform', d.facing < 0 ? 'translate(200 0) scale(-1 1)' : '');
        speechRef.current.style.opacity = String(f1(0.45 + 0.55 * flap));
      }
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
  }, [targetExprKey, motionDisabled, seed, isSpeaking]);

  // Main is ~20% larger than research agents (L1.5 §1 & §25)
  const widthPct = (persona.is_main ? 12.5 : 10.5) * sizeScale;
  const leftPct = (worldX / 1000) * 100;
  const topPct = (worldY / 600) * 100;

  const isInteractive = interactiveMode !== 'none';
  const isReplayDraggable = interactiveMode === 'replay_drag';

  return (
    <div
      role="img"
      className={`rep-persona-node role-${roleKey} state-${persona.state} ${persona.is_main ? 'is-main' : ''} ${benched ? 'is-benched' : ''} ${
        interaction ? `interaction-${interaction}` : ''
      } ${inCompositeGroup ? 'in-composite-group' : ''} ${
        isInteractive ? 'is-group-interactive' : ''
      } ${isReplayDraggable ? 'is-replay-draggable' : ''} ${isDragging ? 'is-dragging' : ''}`}
      data-testid={`persona-${persona.persona_id}`}
      data-persona-id={persona.persona_id}
      data-persona-state={persona.state}
      data-interaction={interaction || 'none'}
      data-world-x={Math.round(worldX)}
      data-world-y={Math.round(worldY)}
      aria-label={`${persona.label} (${persona.state})`}
      title={`${persona.label} · ${persona.state}`}
      onPointerDown={
        isReplayDraggable
          ? (e) => onDragPointerDown?.(persona, e)
          : undefined
      }
      onPointerEnter={
        isInteractive
          ? (e) => onPersonaPointerEnter?.(persona, e)
          : undefined
      }
      onPointerLeave={
        isInteractive
          ? () => onPersonaPointerLeave?.(persona)
          : undefined
      }
      onClick={
        inCompositeGroup
          ? (e) => onPersonaClick?.(persona, e)
          : undefined
      }
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
      <svg viewBox="-16 -14 232 232" aria-hidden="true" className="rep-persona-svg">
        <ellipse ref={shadowRef} className="rep-persona-shadow" cx="0" cy="0" rx="58" ry="7" transform="translate(100 194)" />
        <g className="rep-persona-body">
          <g ref={motionRef} className="rep-persona-motion">
            {persona.is_main && (
              <path
                className="rep-ink rep-orbit"
                d="M18 128 C 8 60, 70 14, 128 24 C 186 34, 206 100, 186 152 C 166 200, 92 214, 48 186"
              />
            )}
            <g ref={headRef} className="rep-head-motion">
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
            </g>
            {isSpeaking && (
              <g ref={speechRef} className="rep-ov-speaking">
                <path className="rep-ink rep-thin rep-speech-arc arc-1" pathLength={1} d="M186 128 C 194 136, 194 152, 186 160" />
                <path className="rep-ink rep-thin rep-speech-arc arc-2" pathLength={1} d="M200 118 C 214 134, 214 156, 200 172" />
              </g>
            )}
            <g className="rep-status-mark-scale" transform={benched ? "translate(186 40) scale(0.72) translate(-186 -40)" : "translate(200 30) scale(1.1) translate(-200 -30)"}>
              <StatusMark state={persona.state} />
            </g>
          </g>
        </g>
      </svg>
      <span className="rep-persona-tag">
        <span className="rep-persona-name">{persona.label}</span>
      </span>
    </div>
  );
};

/**
 * Exaggerated pen-drawn status glyphs (replace the textual state label).
 * Drawn top-right of the head in persona SVG space (head center 100,112, r 76).
 * idle intentionally has no glyph: a calm face without marks reads as "idle".
 */
const StatusMark: React.FC<{ state: VisualState }> = ({ state }) => {
  switch (state) {
    case 'assigned':
      return (
        <g className="rep-status-mark mark-assigned" data-status-mark="assigned">
          <path className="rep-mark-ink rep-ov-draw" pathLength={1} d="M210 -18 C 208 0, 206 16, 203 34" />
          <circle className="rep-mark-dot" cx="201" cy="54" r="3.6" />
        </g>
      );
    case 'working':
      return (
        <g className="rep-status-mark mark-working" data-status-mark="working">
          <g className="rep-mark-pencil">
            <path
              className="rep-mark-ink rep-ov-draw"
              pathLength={1}
              d="M184 44 L224 2 L240 18 L200 60 Z M184 44 L176 68 L200 60 M216 10 L232 26"
            />
          </g>
          <path
            className="rep-mark-ink rep-mark-thin rep-mark-scribble"
            pathLength={1}
            d="M150 214 C 160 198, 170 222, 182 204 S 202 220, 214 202 S 232 214, 244 200"
          />
        </g>
      );
    case 'waiting':
      return (
        <g className="rep-status-mark mark-waiting" data-status-mark="waiting">
          <g className="rep-mark-hourglass">
            <path
              className="rep-mark-ink rep-ov-draw"
              pathLength={1}
              d="M182 -16 L234 -16 M182 60 L234 60 M188 -16 C 188 12, 226 22, 226 60 M228 -16 C 228 12, 190 22, 190 60"
            />
            <path className="rep-mark-sand" d="M196 52 L220 52 L208 40 Z" />
          </g>
        </g>
      );
    case 'debating':
      return (
        <g className="rep-status-mark mark-debating" data-status-mark="debating">
          <g className="rep-mark-bubble b1">
            <path
              className="rep-mark-ink rep-ov-draw"
              pathLength={1}
              d="M166 6 C 166 -18, 214 -20, 216 4 C 218 26, 190 30, 180 26 L166 40 L170 22 C 166 18, 166 12, 166 6 Z"
            />
          </g>
          <g className="rep-mark-bubble b2">
            <path
              className="rep-mark-ink rep-ov-draw"
              pathLength={1}
              d="M246 34 C 246 14, 206 12, 204 32 C 202 50, 226 54, 234 50 L246 62 L242 46 C 246 44, 246 40, 246 34 Z"
            />
          </g>
        </g>
      );
    case 'reporting':
      return (
        <g className="rep-status-mark mark-reporting" data-status-mark="reporting">
          <path
            className="rep-mark-ink rep-ov-draw"
            pathLength={1}
            d="M180 32 L180 6 L222 -14 L222 52 Z M180 10 L166 10 L166 28 L180 28 M184 32 L190 50"
          />
          <g className="rep-mark-sound">
            <path className="rep-mark-ink rep-mark-thin" d="M234 2 L248 -6 M236 19 L252 19 M234 36 L248 44" />
          </g>
        </g>
      );
    case 'handoff':
      return (
        <g className="rep-status-mark mark-handoff" data-status-mark="handoff">
          <g className="rep-mark-arrow">
            <path
              className="rep-mark-ink rep-ov-draw"
              pathLength={1}
              d="M170 46 C 178 4, 214 -10, 240 14 M224 16 L241 15 L238 -2"
            />
          </g>
        </g>
      );
    case 'paused':
      return (
        <g className="rep-status-mark mark-paused" data-status-mark="paused">
          <path className="rep-mark-ink rep-mark-heavy rep-ov-draw" pathLength={1} d="M196 -12 L196 50 M224 -12 L224 50" />
        </g>
      );
    case 'failed':
      return (
        <g className="rep-status-mark mark-failed" data-status-mark="failed">
          <path className="rep-mark-ink rep-mark-heavy rep-ov-draw" pathLength={1} d="M186 -12 L236 38 M236 -12 L186 38" />
          <path className="rep-mark-ink rep-mark-thin" d="M66 96 L82 112 M82 96 L66 112 M118 96 L134 112 M134 96 L118 112" />
        </g>
      );
    case 'completed':
      return (
        <g className="rep-status-mark mark-completed" data-status-mark="completed">
          <path className="rep-mark-ink rep-mark-heavy rep-ov-draw" pathLength={1} d="M170 16 L198 46 L244 -16" />
        </g>
      );
    default:
      return null;
  }
};
