import type { TablePlacement } from './ResearchTableSvg';

export interface PlacedCanvasCard {
  cardId: string;
  kind: 'table' | 'briefing';
  targetId: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
}

export interface SnapshotSpatialState {
  singleAgentCoords: Record<string, { x: number; y: number }>;
  clusterOffsets: Record<string, { dx: number; dy: number }>;
  placedCards: Record<string, PlacedCanvasCard>;
}

export interface SnapshotDragHistoryEntry {
  id: string;
  targetKey: string;
  targetKind: 'single_agent' | 'agent_cluster' | 'placed_card';
  label: string;
  timestamp: number;
  prevState: SnapshotSpatialState;
}

export interface SnapshotSpatialRecord extends SnapshotSpatialState {
  dragHistory: SnapshotDragHistoryEntry[];
}

export interface ResolvedPlacedCard extends PlacedCanvasCard {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  width: number;
  height: number;
}

export interface ShiftedBriefingCluster {
  easelCx: number;
  easelCy: number;
  mainCoord: { x: number; y: number };
  presenterCoords: Record<string, { x: number; y: number }>;
  outerRx: number;
  outerRy: number;
  cx: number;
  cy: number;
}

export const DEFAULT_CARD_SIZE = { width: 380, height: 300 };
export const MIN_CARD_SIZE = { width: 320, height: 220 };
export const MAX_CARD_SIZE = { width: 1400, height: 1100 };
export const DEFAULT_CARD_EXTENTS = { rx: DEFAULT_CARD_SIZE.width / 2, ry: DEFAULT_CARD_SIZE.height / 2 };
const STORAGE_PREFIX = 'libra_reproduce_spatial_v1:';

export function emptySnapshotSpatialRecord(): SnapshotSpatialRecord {
  return {
    singleAgentCoords: {},
    clusterOffsets: {},
    placedCards: {},
    dragHistory: [],
  };
}

export function cloneSnapshotSpatialState(state: SnapshotSpatialState): SnapshotSpatialState {
  const singleAgentCoords: Record<string, { x: number; y: number }> = {};
  for (const [k, v] of Object.entries(state.singleAgentCoords)) {
    singleAgentCoords[k] = { x: v.x, y: v.y };
  }
  const clusterOffsets: Record<string, { dx: number; dy: number }> = {};
  for (const [k, v] of Object.entries(state.clusterOffsets)) {
    clusterOffsets[k] = { dx: v.dx, dy: v.dy };
  }
  const placedCards: Record<string, PlacedCanvasCard> = {};
  for (const [k, v] of Object.entries(state.placedCards)) {
    placedCards[k] = { ...v };
  }
  return { singleAgentCoords, clusterOffsets, placedCards };
}

export function loadRunSpatialRecords(runId: string): Record<string, SnapshotSpatialRecord> {
  if (!runId || typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${runId}`);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return parsed as Record<string, SnapshotSpatialRecord>;
  } catch {
    return {};
  }
}

export function saveRunSpatialRecords(
  runId: string,
  records: Record<string, SnapshotSpatialRecord>
): void {
  if (!runId || typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${runId}`, JSON.stringify(records));
  } catch {
    /* ignore quota or private mode failures */
  }
}

function getTableOuterExtents(pl: TablePlacement): { outerRx: number; outerRy: number } {
  let outerRx = pl.rx + 42;
  let outerRy = pl.ry + 42;
  for (const pos of Object.values(pl.seatsByPersona)) {
    outerRx = Math.max(outerRx, Math.abs(pos.x - pl.cx) + 64);
    outerRy = Math.max(outerRy, Math.abs(pos.y - pl.cy) + 74);
  }
  return { outerRx, outerRy };
}

function aabbOverlap(
  ax: number,
  ay: number,
  arx: number,
  ary: number,
  bx: number,
  by: number,
  brx: number,
  bry: number,
  gap: number
): { overlapX: number; overlapY: number; dx: number; dy: number } | null {
  const dx = bx - ax;
  const dy = by - ay;
  const minX = arx + brx + gap;
  const minY = ary + bry + gap;
  const ox = minX - Math.abs(dx);
  const oy = minY - Math.abs(dy);
  if (ox > 0 && oy > 0) {
    return { overlapX: ox, overlapY: oy, dx, dy };
  }
  return null;
}

/**
 * Pick an initial world position for a newly placed dialogue/briefing card
 * outside any existing table/briefing cluster and other placed cards.
 */
export function computeInitialPlacedCardWorldPos(options: {
  anchorCx: number;
  anchorCy: number;
  outerRx: number;
  outerRy: number;
  hoverWorld?: { x: number; y: number } | null;
  clusters: { cx: number; cy: number; outerRx: number; outerRy: number }[];
  existingCards: ResolvedPlacedCard[];
  cardExtents?: { rx: number; ry: number };
}): { x: number; y: number } {
  const {
    anchorCx,
    anchorCy,
    outerRx,
    outerRy,
    hoverWorld,
    clusters,
    existingCards,
    cardExtents = DEFAULT_CARD_EXTENTS,
  } = options;

  const candidates: { x: number; y: number }[] = [];
  if (hoverWorld) {
    candidates.push(hoverWorld);
  }
  const offsetX = outerRx + cardExtents.rx + 28;
  const offsetY = outerRy + cardExtents.ry + 28;
  candidates.push(
    { x: anchorCx + offsetX, y: anchorCy },
    { x: anchorCx - offsetX, y: anchorCy },
    { x: anchorCx, y: anchorCy - offsetY },
    { x: anchorCx, y: anchorCy + offsetY },
    { x: anchorCx + offsetX * 0.85, y: anchorCy - offsetY * 0.7 },
    { x: anchorCx - offsetX * 0.85, y: anchorCy - offsetY * 0.7 },
    { x: anchorCx + offsetX * 0.85, y: anchorCy + offsetY * 0.7 }
  );

  let best = candidates[0];
  let bestCost = Number.POSITIVE_INFINITY;

  for (let i = 0; i < candidates.length; i += 1) {
    const c = candidates[i];
    let cost = i * 4;
    // Slight preference to stay reasonably near default viewport horizontally/vertically
    if (c.x < 40) cost += (40 - c.x) * 1.2;
    if (c.x > 1120) cost += (c.x - 1120) * 1.2;
    if (c.y < 40) cost += (40 - c.y) * 1.2;
    if (c.y > 580) cost += (c.y - 580) * 1.2;

    for (const cl of clusters) {
      const ov = aabbOverlap(c.x, c.y, cardExtents.rx, cardExtents.ry, cl.cx, cl.cy, cl.outerRx, cl.outerRy, 20);
      if (ov) cost += ov.overlapX * ov.overlapY * 0.8 + 4000;
    }
    for (const card of existingCards) {
      const ov = aabbOverlap(c.x, c.y, cardExtents.rx, cardExtents.ry, card.cx, card.cy, card.rx, card.ry, 18);
      if (ov) cost += ov.overlapX * ov.overlapY + 5000;
    }
    if (cost < bestCost) {
      bestCost = cost;
      best = c;
    }
  }

  return { x: best.x, y: best.y };
}

/**
 * Rigid macro collision solver for placed dialogue cards, table clusters, and the briefing cluster:
 * - Translates table/briefing clusters strictly as whole rigid bodies (never detaching seated agents).
 * - Resolves Card vs. Card, Card vs. Cluster, and Cluster vs. Cluster overlaps in 20 iterations.
 */
export function resolveClusterAndCardCollisions(options: {
  placements: TablePlacement[];
  briefingCluster: ShiftedBriefingCluster | null;
  placedCards: PlacedCanvasCard[];
  measuredCardDims: Record<string, { rx: number; ry: number }>;
  activeDragKey: string | null;
}): {
  placements: TablePlacement[];
  briefingCluster: ShiftedBriefingCluster | null;
  resolvedCards: ResolvedPlacedCard[];
} {
  const clonedPlacements: TablePlacement[] = options.placements.map((pl) => {
    const seats: Record<string, { x: number; y: number }> = {};
    for (const [pid, pos] of Object.entries(pl.seatsByPersona)) {
      seats[pid] = { x: pos.x, y: pos.y };
    }
    return {
      ...pl,
      seatsByPersona: seats,
    };
  });

  const tableOuters = clonedPlacements.map((pl) => getTableOuterExtents(pl));

  let brief: ShiftedBriefingCluster | null = options.briefingCluster
    ? {
        ...options.briefingCluster,
        mainCoord: { ...options.briefingCluster.mainCoord },
        presenterCoords: Object.fromEntries(
          Object.entries(options.briefingCluster.presenterCoords).map(([k, v]) => [k, { ...v }])
        ),
      }
    : null;

  const cards: ResolvedPlacedCard[] = options.placedCards.map((c) => {
    const width = c.width ?? DEFAULT_CARD_SIZE.width;
    const height = c.height ?? DEFAULT_CARD_SIZE.height;
    const measured = options.measuredCardDims[c.cardId];
    return {
      ...c,
      cx: c.x,
      cy: c.y,
      width,
      height,
      rx: c.width ? width / 2 : Math.max(width / 2, measured?.rx ?? 0),
      ry: c.height ? height / 2 : Math.max(height / 2, measured?.ry ?? 0),
    };
  });

  const shiftTable = (idx: number, dx: number, dy: number) => {
    const pl = clonedPlacements[idx];
    pl.cx += dx;
    pl.cy += dy;
    for (const pid of Object.keys(pl.seatsByPersona)) {
      pl.seatsByPersona[pid].x += dx;
      pl.seatsByPersona[pid].y += dy;
    }
  };

  const shiftBrief = (dx: number, dy: number) => {
    if (!brief) return;
    brief.easelCx += dx;
    brief.easelCy += dy;
    brief.cx += dx;
    brief.cy += dy;
    brief.mainCoord.x += dx;
    brief.mainCoord.y += dy;
    for (const pid of Object.keys(brief.presenterCoords)) {
      brief.presenterCoords[pid].x += dx;
      brief.presenterCoords[pid].y += dy;
    }
  };

  const activeKey = options.activeDragKey;

  for (let iter = 0; iter < 20; iter += 1) {
    // 1. Table Cluster vs. Table Cluster (rigid separation; placed cards stay on upper layer without colliding with agents)
    for (let i = 0; i < clonedPlacements.length; i += 1) {
      for (let j = i + 1; j < clonedPlacements.length; j += 1) {
        const a = clonedPlacements[i];
        const b = clonedPlacements[j];
        const oa = tableOuters[i];
        const ob = tableOuters[j];
        const ov = aabbOverlap(a.cx, a.cy, oa.outerRx, oa.outerRy, b.cx, b.cy, ob.outerRx, ob.outerRy, 16);
        if (!ov) continue;
        const aDrag = activeKey === `cluster:table:${a.collaborationId}`;
        const bDrag = activeKey === `cluster:table:${b.collaborationId}`;
        const wA = aDrag ? 0 : bDrag ? 1 : 0.5;
        const wB = bDrag ? 0 : aDrag ? 1 : 0.5;
        const tot = wA + wB || 1;
        if (ov.overlapX < ov.overlapY) {
          const dir = ov.dx >= 0 ? 1 : -1;
          shiftTable(i, -dir * ov.overlapX * (wA / tot), 0);
          shiftTable(j, dir * ov.overlapX * (wB / tot), 0);
        } else {
          const dir = ov.dy >= 0 ? 1 : -1;
          shiftTable(i, 0, -dir * ov.overlapY * (wA / tot));
          shiftTable(j, 0, dir * ov.overlapY * (wB / tot));
        }
      }

      if (brief) {
        const a = clonedPlacements[i];
        const oa = tableOuters[i];
        const ov = aabbOverlap(a.cx, a.cy, oa.outerRx, oa.outerRy, brief.cx, brief.cy, brief.outerRx, brief.outerRy, 16);
        if (ov) {
          const aDrag = activeKey === `cluster:table:${a.collaborationId}`;
          const bDrag = activeKey === 'cluster:briefing';
          const wA = aDrag ? 0 : bDrag ? 1 : 0.5;
          const wB = bDrag ? 0 : aDrag ? 1 : 0.5;
          const tot = wA + wB || 1;
          if (ov.overlapX < ov.overlapY) {
            const dir = ov.dx >= 0 ? 1 : -1;
            shiftTable(i, -dir * ov.overlapX * (wA / tot), 0);
            shiftBrief(dir * ov.overlapX * (wB / tot), 0);
          } else {
            const dir = ov.dy >= 0 ? 1 : -1;
            shiftTable(i, 0, -dir * ov.overlapY * (wA / tot));
            shiftBrief(0, dir * ov.overlapY * (wB / tot));
          }
        }
      }
    }
  }

  return {
    placements: clonedPlacements,
    briefingCluster: brief,
    resolvedCards: cards,
  };
}
