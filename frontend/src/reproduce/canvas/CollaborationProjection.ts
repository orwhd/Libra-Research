import type {
  CanvasProjection,
  Collaboration,
  HarnessEvent,
  Persona,
  ReproduceRunMode,
  VisualState,
} from '../types';

export const STATIC_PERSONA_ROSTER: Persona[] = [
  { persona_id: 'persona_main', role: 'main', label: 'Main', runtime_agent_id: null, state: 'idle', is_main: true, task_ids: [] },
  { persona_id: 'persona_paper', role: 'paper', label: 'Paper', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
  { persona_id: 'persona_data', role: 'data', label: 'Data', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
  { persona_id: 'persona_analysis', role: 'analysis', label: 'Analysis', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
  { persona_id: 'persona_validation', role: 'validation', label: 'Validation', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
  { persona_id: 'persona_support', role: 'support', label: 'Support', runtime_agent_id: null, state: 'idle', is_main: false, task_ids: [] },
];

/**
 * Fixed world-space home anchors inside 1000x600 LivingCanvas (L1.5 §2 & §20.1).
 * Main stays in the left Boss safety zone x ∈ [0, 260], close to Left Chat.
 */
export const ROLE_BASE_ANCHORS_WORLD: Record<string, { x: number; y: number }> = {
  main: { x: 165, y: 294 },
  paper: { x: 455, y: 136 },
  data: { x: 775, y: 170 },
  analysis: { x: 560, y: 342 },
  validation: { x: 840, y: 438 },
  support: { x: 355, y: 492 },
};

export const HOME_ANCHORS_WORLD: Record<string, { x: number; y: number }> = {
  persona_main: ROLE_BASE_ANCHORS_WORLD.main,
  persona_paper: ROLE_BASE_ANCHORS_WORLD.paper,
  persona_data: ROLE_BASE_ANCHORS_WORLD.data,
  persona_analysis: ROLE_BASE_ANCHORS_WORLD.analysis,
  persona_validation: ROLE_BASE_ANCHORS_WORLD.validation,
  persona_support: ROLE_BASE_ANCHORS_WORLD.support,
};

export const MAIN_COLLISION_RADIUS = { rx: 64, ry: 72 };
export const SPECIALIST_COLLISION_RADIUS = { rx: 52, ry: 62 };
export const AGENT_COLLISION_GAP = 14;

const DYNAMIC_RING_OFFSETS: [number, number][] = [
  [126, -54],
  [-122, 58],
  [128, 66],
  [-124, -58],
  [0, 124],
  [0, -124],
  [176, 0],
  [-162, 0],
];

/**
 * Resolve conflict-free home anchors for all baseline + dynamically spawned personas.
 * - Baseline instances stay near `ROLE_BASE_ANCHORS_WORLD` coordinates unless separated for collision volumes.
 * - Extra instances within any specialist role fan out cleanly around that role's anchor
 *   and are repelled pairwise (`minDist = 126`) while keeping Main's left Boss zone clear (`x >= 306`).
 */
export function computePersonaHomeAnchors(personas: Persona[]): Record<string, { x: number; y: number }> {
  const anchors: Record<string, { x: number; y: number }> = {};
  const roleSeenCount: Record<string, number> = {};

  for (const p of personas) {
    if (p.is_main || p.persona_id === 'persona_main') {
      anchors[p.persona_id] = { ...ROLE_BASE_ANCHORS_WORLD.main };
      continue;
    }
    const base = HOME_ANCHORS_WORLD[p.persona_id] || ROLE_BASE_ANCHORS_WORLD[p.role] || { x: 580, y: 300 };
    const idxInRole = roleSeenCount[p.role] || 0;
    roleSeenCount[p.role] = idxInRole + 1;

    if (idxInRole === 0 && HOME_ANCHORS_WORLD[p.persona_id]) {
      anchors[p.persona_id] = { ...base };
    } else {
      const slot = idxInRole > 0 ? idxInRole - 1 : 0;
      const offset = DYNAMIC_RING_OFFSETS[slot % DYNAMIC_RING_OFFSETS.length];
      const ringScale = 1 + Math.floor(slot / DYNAMIC_RING_OFFSETS.length) * 0.65;
      anchors[p.persona_id] = {
        x: Math.max(306, Math.min(942, base.x + offset[0] * ringScale)),
        y: Math.max(66, Math.min(534, base.y + offset[1] * ringScale)),
      };
    }
  }

  // Pairwise separation among specialist personas using collision-volume clearance
  const specialistIds = personas
    .filter((p) => !p.is_main && p.persona_id !== 'persona_main')
    .map((p) => p.persona_id);

  if (specialistIds.length > 1) {
    const minDist = 126;
    for (let iter = 0; iter < 18; iter += 1) {
      for (let i = 0; i < specialistIds.length; i += 1) {
        for (let j = i + 1; j < specialistIds.length; j += 1) {
          const a = anchors[specialistIds[i]];
          const b = anchors[specialistIds[j]];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.hypot(dx, dy) || 0.01;
          if (dist < minDist) {
            const push = (minDist - dist) * 0.52;
            const ux = dx / dist;
            const uy = dy / dist;
            a.x = Math.max(306, Math.min(942, a.x - ux * push));
            a.y = Math.max(66, Math.min(534, a.y - uy * push));
            b.x = Math.max(306, Math.min(942, b.x + ux * push));
            b.y = Math.max(66, Math.min(534, b.y + uy * push));
          }
        }
      }
    }
  }

  return anchors;
}

export interface TableObstacle {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  memberIds: string[];
}

export interface EaselObstacle {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
}

export interface BubbleObstacle {
  id: string;
  cx: number;
  cy: number;
  rx: number;
  ry: number;
}

export interface LiveBubblePlacement extends BubbleObstacle {
  collabId: string;
  text: string;
  dotMidX: number;
  dotMidY: number;
  dotRimX: number;
  dotRimY: number;
}

function capsuleEllipseNorm(
  bub: BubbleObstacle,
  px: number,
  py: number,
  radRx: number,
  radRy: number,
  gap: number
): { norm: number; dx: number; dy: number } {
  const spineHalf = Math.max(0, bub.rx - bub.ry);
  const clampedX = Math.max(bub.cx - spineHalf, Math.min(bub.cx + spineHalf, px));
  const dx = px - clampedX;
  const dy = py - bub.cy;
  const minDx = bub.ry + radRx + gap;
  const minDy = bub.ry + radRy + gap;
  const norm = Math.hypot(dx / minDx, dy / minDy);
  return { norm, dx, dy };
}

/**
 * Compute rim-anchored Thinking Bubble placements around active tables:
 * - Trailing dot lands on the outer rim of the table (never over center pen strokes).
 * - Pill sits outside the rim and avoids seated table participants, other tables, and stage edges.
 */
export function computeLiveBubblePlacements(options: {
  tables: {
    collaborationId: string;
    cx: number;
    cy: number;
    rx: number;
    ry: number;
    seatsByPersona: Record<string, { x: number; y: number }>;
    text: string;
  }[];
  homeAnchorsById: Record<string, { x: number; y: number }>;
  personas: Persona[];
}): LiveBubblePlacement[] {
  const { tables, homeAnchorsById, personas } = options;
  if (tables.length === 0) return [];

  const allSeatedCoords: { pid: string; x: number; y: number }[] = [];
  for (const t of tables) {
    for (const [pid, pos] of Object.entries(t.seatsByPersona)) {
      allSeatedCoords.push({ pid, x: pos.x, y: pos.y });
    }
  }
  const seatedIdSet = new Set(allSeatedCoords.map((s) => s.pid));
  const bystanderAnchors = personas
    .filter((p) => !p.is_main && p.persona_id !== 'persona_main' && !seatedIdSet.has(p.persona_id))
    .map((p) => homeAnchorsById[p.persona_id] || HOME_ANCHORS_WORLD[p.persona_id] || { x: 580, y: 300 });

  const placed: LiveBubblePlacement[] = [];

  for (const tbl of tables) {
    const pillRx = Math.min(114, Math.max(74, Math.round(tbl.text.length * 3.3 + 16)));
    const pillRy = 18;
    const minCx = 292 + pillRx;
    const maxCx = 972 - pillRx;
    const minCy = 58 + pillRy;
    const maxCy = 514 - pillRy;

    const angles: { angle: number; baseCost: number; radialExtra: number }[] = [
      { angle: -Math.PI / 2, baseCost: 0, radialExtra: 40 },
      { angle: -Math.PI * 0.3, baseCost: 18, radialExtra: 42 },
      { angle: -Math.PI * 0.7, baseCost: 18, radialExtra: 42 },
      { angle: Math.PI / 2, baseCost: 26, radialExtra: 40 },
      { angle: Math.PI * 0.3, baseCost: 38, radialExtra: 42 },
      { angle: Math.PI * 0.7, baseCost: 38, radialExtra: 42 },
    ];

    let best: LiveBubblePlacement | null = null;
    let bestCost = Number.POSITIVE_INFINITY;

    for (const cand of angles) {
      const cos = Math.cos(cand.angle);
      const sin = Math.sin(cand.angle);
      const rimX = tbl.cx + cos * tbl.rx * 0.88;
      const rimY = tbl.cy + sin * (tbl.ry - 4);
      const rawPillCx = tbl.cx + cos * (tbl.rx + cand.radialExtra);
      const rawPillCy = tbl.cy + sin * (tbl.ry + cand.radialExtra);
      const pillCx = Math.max(minCx, Math.min(maxCx, rawPillCx));
      const pillCy = Math.max(minCy, Math.min(maxCy, rawPillCy));
      const clampDist = Math.hypot(pillCx - rawPillCx, pillCy - rawPillCy);

      let cost = cand.baseCost + (clampDist > 4 ? 500 + clampDist * 10 : 0);
      const bubRect: BubbleObstacle = {
        id: `bubble_${tbl.collaborationId}`,
        cx: pillCx,
        cy: pillCy,
        rx: pillRx,
        ry: pillRy,
      };

      for (const s of allSeatedCoords) {
        const { norm } = capsuleEllipseNorm(
          bubRect,
          s.x,
          s.y,
          SPECIALIST_COLLISION_RADIUS.rx,
          SPECIALIST_COLLISION_RADIUS.ry,
          8
        );
        if (norm < 1.05) {
          cost += 4500 * (1.05 - norm);
        }
      }

      for (const other of tables) {
        if (other.collaborationId === tbl.collaborationId) continue;
        const { norm } = capsuleEllipseNorm(bubRect, other.cx, other.cy, other.rx, other.ry, 10);
        if (norm < 1) {
          cost += 3000 * (1 - norm);
        }
      }

      for (const prev of placed) {
        const { norm } = capsuleEllipseNorm(bubRect, prev.cx, prev.cy, prev.rx, prev.ry, 10);
        if (norm < 1) {
          cost += 3000 * (1 - norm);
        }
      }

      for (const ba of bystanderAnchors) {
        const { norm } = capsuleEllipseNorm(
          bubRect,
          ba.x,
          ba.y,
          SPECIALIST_COLLISION_RADIUS.rx,
          SPECIALIST_COLLISION_RADIUS.ry,
          10
        );
        if (norm < 1) {
          cost += 14 * (1 - norm);
        }
      }

      const edgeX = Math.max(pillCx - (pillRx - 12), Math.min(pillCx + (pillRx - 12), rimX));
      const edgeY = rimY >= pillCy ? pillCy + pillRy : pillCy - pillRy;
      const dotMidX = (rimX + edgeX) * 0.5;
      const dotMidY = (rimY + edgeY) * 0.5;

      if (cost < bestCost) {
        bestCost = cost;
        best = {
          ...bubRect,
          collabId: tbl.collaborationId,
          text: tbl.text,
          dotMidX,
          dotMidY,
          dotRimX: rimX,
          dotRimY: rimY,
        };
      }
    }

    if (best) {
      placed.push(best);
    }
  }

  return placed;
}

/**
 * Resolve elliptical collision volumes across all agents, active tables, thinking bubbles, and the PPT easel:
 * - Main stays fixed in the left Boss safety zone.
 * - Pairwise elliptical collision separation prevents body/label overlaps in every state.
 * - Bystander agents are pushed smoothly outside active table perimeters, thinking bubbles, and the PPT easel.
 */
export function resolvePersonaCollisions(options: {
  personas: Persona[];
  rawCoords: Record<string, { x: number; y: number }>;
  tableObstacles?: TableObstacle[];
  easelObstacle?: EaselObstacle | null;
  bubbleObstacles?: BubbleObstacle[];
  pinnedIds?: Set<string>;
}): Record<string, { x: number; y: number }> {
  const {
    personas,
    rawCoords,
    tableObstacles = [],
    easelObstacle = null,
    bubbleObstacles = [],
    pinnedIds,
  } = options;
  const coords: Record<string, { x: number; y: number }> = {};
  for (const p of personas) {
    const c = rawCoords[p.persona_id] || HOME_ANCHORS_WORLD[p.persona_id] || { x: 580, y: 300 };
    coords[p.persona_id] = { x: c.x, y: c.y };
  }

  const getRadius = (p: Persona) =>
    p.is_main || p.persona_id === 'persona_main' ? MAIN_COLLISION_RADIUS : SPECIALIST_COLLISION_RADIUS;

  const getWeight = (p: Persona) => {
    if (p.is_main || p.persona_id === 'persona_main') return 0;
    if (pinnedIds?.has(p.persona_id)) return 0.2;
    return 0.8;
  };

  for (let iter = 0; iter < 38; iter += 1) {
    // 1. Pairwise elliptical collision separation
    for (let i = 0; i < personas.length; i += 1) {
      const pa = personas[i];
      const a = coords[pa.persona_id];
      const radA = getRadius(pa);
      const wA = getWeight(pa);

      for (let j = i + 1; j < personas.length; j += 1) {
        const pb = personas[j];
        const b = coords[pb.persona_id];
        const radB = getRadius(pb);
        const wB = getWeight(pb);

        const totalW = wA + wB;
        if (totalW <= 0) continue;

        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const minDx = radA.rx + radB.rx + AGENT_COLLISION_GAP;
        const minDy = radA.ry + radB.ry + AGENT_COLLISION_GAP;

        if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
          const angle = ((i * 2.39996 + j * 1.1) % (Math.PI * 2));
          dx = Math.cos(angle) * 2;
          dy = Math.sin(angle) * 2;
        }

        const norm = Math.hypot(dx / minDx, dy / minDy);
        if (norm < 1) {
          const scale = 1 / Math.max(0.05, norm) - 1;
          const pushX = dx * scale;
          const pushY = dy * scale;
          a.x -= pushX * (wA / totalW);
          a.y -= pushY * (wA / totalW);
          b.x += pushX * (wB / totalW);
          b.y += pushY * (wB / totalW);
        }
      }
    }

    // 2. Active table perimeter & PPT whiteboard easel exclusion
    for (const p of personas) {
      if (p.is_main || p.persona_id === 'persona_main') continue;
      const c = coords[p.persona_id];
      const rad = getRadius(p);

      for (const tbl of tableObstacles) {
        const isMember = tbl.memberIds.includes(p.persona_id);
        const agentRx = rad.rx + 6;
        const agentRy = 52;
        const gap = isMember ? 8 : 16;
        const exRx = tbl.rx + agentRx + gap;
        const exRy = tbl.ry + agentRy + gap;
        let dx = c.x - tbl.cx;
        let dy = (c.y + 5) - tbl.cy;
        if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
          dx = 1;
          dy = -1;
        }
        const norm = Math.hypot(dx / exRx, dy / exRy);
        if (norm < 1) {
          const push = 1 / Math.max(0.05, norm);
          c.x = tbl.cx + dx * push;
          c.y = tbl.cy + dy * push - 5;
        }
      }

      if (easelObstacle) {
        const exRx = easelObstacle.rx + rad.rx + 8;
        const exRy = easelObstacle.ry + rad.ry + 8;
        const dx = c.x - easelObstacle.cx;
        const dy = c.y - easelObstacle.cy;
        const norm = Math.hypot(dx / exRx, dy / exRy);
        if (norm < 1) {
          if (dx >= 0) {
            const safeX = easelObstacle.cx + exRx * Math.sqrt(Math.max(0.15, 1 - Math.min(0.85, (dy / exRy) ** 2)));
            c.x = Math.max(c.x, safeX);
          } else {
            const push = 1 / Math.max(0.05, norm);
            c.x = easelObstacle.cx + dx * push;
            c.y = easelObstacle.cy + dy * push;
          }
        }
      }

      for (const bub of bubbleObstacles) {
        const res = capsuleEllipseNorm(bub, c.x, c.y, rad.rx, rad.ry, 12);
        if (res.norm < 1) {
          let dx = res.dx;
          let dy = res.dy;
          if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
            dy = c.y <= bub.cy ? -1 : 1;
          }
          const pushRatio = (1 / Math.max(0.08, res.norm) - 1) * (pinnedIds?.has(p.persona_id) ? 0.45 : 0.95);
          c.x += dx * pushRatio;
          c.y += dy * pushRatio;
        }
      }

      // 3. Stage bounds clamping (preserve Main's left Boss zone clear)
      c.x = Math.max(306, Math.min(944, c.x));
      c.y = Math.max(66, Math.min(534, c.y));
    }
  }

  return coords;
}

export interface ActiveTableVisual extends Collaboration {
  revision: number;
  speakingPersonaId: string | null;
  prominent: boolean;
  fromSnapshot: boolean;
}

export interface ProjectedCanvasState {
  runId: string;
  atSeq: number;
  mode: ReproduceRunMode;
  personas: Persona[];
  tables: ActiveTableVisual[];
  overflowCollaborations: Collaboration[];
}

export function createInitialProjectedState(
  runId = 'pending',
  mode: ReproduceRunMode = 'live'
): ProjectedCanvasState {
  return {
    runId,
    atSeq: 0,
    mode,
    personas: STATIC_PERSONA_ROSTER.map((p) => ({ ...p, task_ids: [] })),
    tables: [],
    overflowCollaborations: [],
  };
}

function allocateVisibleTables(
  rawList: ActiveTableVisual[]
): { prominent: ActiveTableVisual[]; overflow: Collaboration[] } {
  const prominent: ActiveTableVisual[] = [];
  const overflow: Collaboration[] = [];
  const claimedPersonas = new Set<string>();

  for (const tbl of rawList) {
    if (tbl.status === 'ended') continue;
    const cleanMembers = tbl.participant_persona_ids.filter(
      (id) => id !== 'persona_main' && !claimedPersonas.has(id)
    );
    // Max 2 prominent tables simultaneously and a single persona cannot sit at two tables at once (§20.4)
    if (prominent.length < 2 && cleanMembers.length >= 2) {
      for (const m of cleanMembers) claimedPersonas.add(m);
      prominent.push({
        ...tbl,
        participant_persona_ids: cleanMembers,
        prominent: true,
      });
    } else {
      overflow.push(tbl);
    }
  }
  return { prominent, overflow };
}

export function hydrateFromCanvasProjection(
  projection: CanvasProjection | null | undefined,
  fallbackRunId = 'pending',
  fallbackMode: ReproduceRunMode = 'live'
): ProjectedCanvasState {
  if (!projection) {
    return createInitialProjectedState(fallbackRunId, fallbackMode);
  }
  const byId = new Map<string, Persona>();
  for (const base of STATIC_PERSONA_ROSTER) {
    byId.set(base.persona_id, { ...base, task_ids: [] });
  }
  for (const p of projection.personas) {
    byId.set(p.persona_id, { ...p, task_ids: [...(p.task_ids || [])] });
  }

  const rawTables: ActiveTableVisual[] = (projection.collaborations || [])
    .filter((c) => c.status === 'active' || c.status === 'ending')
    .map((c) => ({
      ...c,
      revision: c.revision ?? 1,
      speakingPersonaId: null,
      prominent: true,
      fromSnapshot: true,
    }));

  const { prominent, overflow } = allocateVisibleTables(rawTables);
  return {
    runId: projection.run_id,
    atSeq: projection.at_seq,
    mode: projection.mode,
    personas: Array.from(byId.values()),
    tables: prominent,
    overflowCollaborations: overflow,
  };
}

export function applyHarnessEventToProjection(
  prev: ProjectedCanvasState,
  ev: HarnessEvent
): ProjectedCanvasState {
  if (ev.seq <= prev.atSeq) {
    return prev;
  }

  const personas = prev.personas.map((p) => ({ ...p, task_ids: [...p.task_ids] }));
  const findPersona = (id?: string | null) => personas.find((p) => p.persona_id === id);

  const rawTables: ActiveTableVisual[] = [
    ...prev.tables.map((t) => ({ ...t, fromSnapshot: false })),
    ...prev.overflowCollaborations.map((c) => ({
      ...c,
      revision: c.revision ?? 1,
      speakingPersonaId: null,
      prominent: false,
      fromSnapshot: false,
    })),
  ];

  const pl = ev.payload || {};

  if (ev.type === 'agent.spawned') {
    const pid = String(pl.persona_id || '');
    const rtId = String(pl.runtime_agent_id || ev.actor_id || '');
    const p = findPersona(pid);
    if (p) {
      p.runtime_agent_id = rtId;
    } else if (pid) {
      personas.push({
        persona_id: pid,
        role: String(pl.role || pid.replace('persona_', '').split('_')[0] || 'support'),
        label: String(pl.label || pid),
        runtime_agent_id: rtId,
        state: 'assigned',
        is_main: Boolean(pl.is_main),
        task_ids: [],
      });
    }
  } else if (ev.type === 'agent.status_changed') {
    const p = findPersona(String(pl.persona_id || ''));
    if (p && typeof pl.next_state === 'string') {
      p.state = pl.next_state as VisualState;
    }
  } else if (ev.type === 'task.created') {
    const p = findPersona(String(pl.persona_id || ''));
    const tid = String(pl.task_id || '');
    if (p && tid && !p.task_ids.includes(tid)) {
      p.task_ids.push(tid);
    }
  } else if (ev.type === 'collaboration.started') {
    const cid = String(pl.collaboration_id || '');
    const members = Array.isArray(pl.participant_persona_ids)
      ? (pl.participant_persona_ids as string[]).filter((m) => m !== 'persona_main')
      : [];
    const rev = Number(pl.revision || 1);
    const srefRaw = (pl.source_ref as { kind?: string; id?: string }) || {
      kind: 'collaboration',
      id: cid,
    };
    if (cid && members.length >= 2 && !rawTables.some((t) => t.collaboration_id === cid)) {
      rawTables.push({
        collaboration_id: cid,
        kind: (pl.kind as Collaboration['kind']) || 'collaboration',
        status: 'active',
        topic: String(pl.topic || ev.summary || ''),
        participant_persona_ids: Array.from(new Set(members)),
        ref: {
          kind: (srefRaw.kind as Collaboration['ref']['kind']) || 'collaboration',
          id: String(srefRaw.id || cid),
        },
        last_event_seq: ev.seq,
        revision: rev,
        speakingPersonaId: null,
        prominent: true,
        fromSnapshot: false,
      });
    }
  } else if (ev.type === 'collaboration.members_changed') {
    const cid = String(pl.collaboration_id || '');
    const rev = Number(pl.revision || 2);
    const idx = rawTables.findIndex((t) => t.collaboration_id === cid);
    if (idx >= 0 && rev > rawTables[idx].revision) {
      const members = Array.isArray(pl.participant_persona_ids)
        ? Array.from(new Set((pl.participant_persona_ids as string[]).filter((m) => m !== 'persona_main')))
        : rawTables[idx].participant_persona_ids;
      if (members.length >= 2) {
        rawTables[idx] = {
          ...rawTables[idx],
          participant_persona_ids: members,
          revision: rev,
          last_event_seq: ev.seq,
        };
      } else {
        rawTables.splice(idx, 1);
      }
    }
  } else if (ev.type === 'debate.turn_added') {
    const did = String(pl.debate_id || ev.subject?.id || '');
    const speaker = ev.actor_id || null;
    for (let i = 0; i < rawTables.length; i += 1) {
      if (rawTables[i].ref.id === did || rawTables[i].collaboration_id === did) {
        rawTables[i] = { ...rawTables[i], speakingPersonaId: speaker, last_event_seq: ev.seq };
      }
    }
  } else if (ev.type === 'collaboration.ended') {
    const cid = String(pl.collaboration_id || ev.subject?.id || '');
    const idx = rawTables.findIndex((t) => t.collaboration_id === cid);
    if (idx >= 0) {
      rawTables.splice(idx, 1);
    }
  } else if (ev.type === 'run.completed' || ev.type === 'run.failed') {
    rawTables.length = 0;
  }

  const { prominent, overflow } = allocateVisibleTables(rawTables);
  return {
    runId: ev.run_id || prev.runId,
    atSeq: ev.seq,
    mode: ev.mode || prev.mode,
    personas,
    tables: prominent,
    overflowCollaborations: overflow,
  };
}
