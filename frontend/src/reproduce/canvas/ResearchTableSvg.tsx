import React, { useEffect, useMemo, useRef, useState } from 'react';
import { HOME_ANCHORS_WORLD, type ActiveTableVisual } from './CollaborationProjection';

export const SAMPLE_POINTS = 64;
const AGENT_SEAT_CLEARANCE = 64; // Keeps agent head + hanging name/status label clear of table perimeter
const BASE_SEAT_CLEARANCE_X = 64;
/** Agent node box in world units at scale 1 (head + hanging name label), used for tight seating. */
const AGENT_BOX_HALF_W = 58;
const AGENT_BOX_TOP = 62;
const AGENT_BOX_BOTTOM = 70;
/** Normalized ellipse distance the agent box must keep from the table centre (1 = touching). */
const SEAT_BOX_MARGIN_NORM = 1.12;
const BASE_SEAT_CLEARANCE_TOP = 76;
const BASE_SEAT_CLEARANCE_BOTTOM = 66;

export type TableShapeKind = 'rectangle' | 'circle' | 'ellipse';

export function getTableShapeKind(memberCount: number): TableShapeKind {
  if (memberCount <= 2) return 'rectangle';
  if (memberCount <= 4) return 'circle';
  return 'ellipse';
}

function hashCollaborationSeed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 1000;
}

/**
 * Sample a rounded-rectangle, circle, or horizontal ellipse at 64 equal parameter angles
 * with deterministic collaboration_id-seeded pen irregularity (±2.1 world units).
 * Equal point count guarantees smooth continuous path morphing without unmounting `<path>`.
 */
/** Single source of truth for table half-extents (placement, seats, and drawn perimeter). */
export function getTableDims(memberCount: number): { rx: number; ry: number } {
  const shape = getTableShapeKind(memberCount);
  if (shape === 'rectangle') return { rx: 46, ry: 26 };
  if (shape === 'circle') {
    const r = memberCount === 3 ? 50 : 60;
    return { rx: r, ry: r };
  }
  const extra = Math.max(0, memberCount - 6);
  return { rx: 88 + extra * 14, ry: 52 + extra * 10 };
}

export function sampleTablePerimeter(
  cx: number,
  cy: number,
  memberCount: number,
  seed: number
): [number, number][] {
  const shape = getTableShapeKind(memberCount);
  const pts: [number, number][] = [];

  for (let i = 0; i < SAMPLE_POINTS; i += 1) {
    const theta = (i / SAMPLE_POINTS) * Math.PI * 2;
    const cos = Math.cos(theta);
    const sin = Math.sin(theta);

    const dims = getTableDims(memberCount);
    let bx = 0;
    let by = 0;
    if (shape === 'rectangle') {
      // Rounded superellipse approximating a hand-drawn rectangular table (§20.2)
      const exp = 2 / 4.6;
      bx = Math.sign(cos) * Math.pow(Math.abs(cos), exp) * dims.rx;
      by = Math.sign(sin) * Math.pow(Math.abs(sin), exp) * dims.ry;
    } else {
      bx = cos * dims.rx;
      by = sin * dims.ry;
    }

    const wobble =
      1.9 * Math.sin(theta * 3 + seed) + 1.1 * Math.cos(theta * 5 - seed * 1.7);
    pts.push([cx + bx + cos * wobble, cy + by + sin * wobble]);
  }
  return pts;
}

export function pointsToSvgPath(pts: [number, number][]): string {
  if (pts.length === 0) return '';
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 1; i < pts.length; i += 1) {
    d += `L${pts[i][0].toFixed(1)} ${pts[i][1].toFixed(1)}`;
  }
  return `${d}Z`;
}

export interface TablePlacement {
  collaborationId: string;
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  shapeKind: TableShapeKind;
  seatsByPersona: Record<string, { x: number; y: number }>;
}

/**
 * Solve table center & outer perimeter seats in 1000x600 world coordinates (§20.1 & §20.2):
 * - Keeps Main's left Boss safety zone `x ∈ [0, 275]` completely clear.
 * - Places every participant seat strictly OUTSIDE the table edge by `AGENT_SEAT_CLEARANCE`.
 * - Assigns seats greedily by closest angle to each persona's home anchor so adding/removing
 *   a member shifts existing seats minimally.
 */
export function computeTablePlacements(
  tables: ActiveTableVisual[],
  allPersonaIds: string[],
  homeAnchorsById: Record<string, { x: number; y: number }> = HOME_ANCHORS_WORLD,
  clearanceScale = 1
): TablePlacement[] {
  const SEAT_CLEARANCE_X = BASE_SEAT_CLEARANCE_X * clearanceScale;
  const SEAT_CLEARANCE_TOP = BASE_SEAT_CLEARANCE_TOP * clearanceScale;
  const SEAT_CLEARANCE_BOTTOM = BASE_SEAT_CLEARANCE_BOTTOM * clearanceScale;
  const draftTables: {
    tbl: ActiveTableVisual;
    members: string[];
    count: number;
    shapeKind: TableShapeKind;
    cx: number;
    cy: number;
    rx: number;
    ry: number;
    minCy: number;
    maxCy: number;
  }[] = [];

  for (const tbl of tables) {
    const members = tbl.participant_persona_ids;
    const count = members.length;
    const shapeKind = getTableShapeKind(count);
    // Compact tables keep seated collaborators visibly close to each other
    const { rx: tableRx, ry: tableRy } = getTableDims(count);

    // Centroid of participating personas' home anchors (including dynamic multi-instance workers)
    let sumX = 0;
    let sumY = 0;
    for (const pid of members) {
      const h = homeAnchorsById[pid] || HOME_ANCHORS_WORLD[pid] || { x: 600, y: 300 };
      sumX += h.x;
      sumY += h.y;
    }
    const minCx = Math.min(540, 306 + tableRx + SEAT_CLEARANCE_X - 8);
    const maxCx = Math.max(660, 940 - tableRx - SEAT_CLEARANCE_X + 8);
    // Keep the whole seated ring (not just the table) inside the stage so seats are never clamped into the table
    const ringTop = tableRy + (count >= 3 ? SEAT_CLEARANCE_TOP : 0);
    const ringBottom = tableRy + (count >= 3 ? SEAT_CLEARANCE_BOTTOM : 0);
    const minCy = Math.min(300, Math.max(198, 62 * clearanceScale + ringTop + 4));
    const maxCy = Math.max(300, Math.min(415, 600 - 74 * clearanceScale - ringBottom - 4));
    const baseCx = Math.max(minCx, Math.min(maxCx, sumX / Math.max(1, count)));
    const baseCy = Math.max(minCy, Math.min(maxCy, sumY / Math.max(1, count)));

    // Candidate grid search around centroid minimizing travel + overlap + Main-zone & boundary penalties
    let bestCx = baseCx;
    let bestCy = baseCy;
    let bestCost = Number.POSITIVE_INFINITY;

    const offsets = [0, -55, 55, -110, 110];
    for (const dx of offsets) {
      for (const dy of offsets) {
        const cx = baseCx + dx;
        const cy = baseCy + dy;
        if (cx < minCx - 16 || cx > maxCx + 16 || cy < minCy - 18 || cy > maxCy + 18) continue;

        let cost = Math.hypot(cx - baseCx, cy - baseCy) * 0.65;
        if (cx < 455) cost += (455 - cx) * 4.5;

        for (const pid of allPersonaIds) {
          if (members.includes(pid)) continue;
          const h = homeAnchorsById[pid] || HOME_ANCHORS_WORLD[pid];
          if (!h) continue;
          const d = Math.hypot(cx - h.x, cy - h.y);
          if (d < 240) cost += (240 - d) * 3.4;
        }
        for (const prev of draftTables) {
          const minDx = tableRx + prev.rx + 124;
          const minDy = tableRy + prev.ry + 116;
          const norm = Math.hypot((cx - prev.cx) / minDx, (cy - prev.cy) / minDy);
          if (norm < 1) cost += (1 - norm) * 2400;
        }

        if (cost < bestCost) {
          bestCost = cost;
          bestCx = cx;
          bestCy = cy;
        }
      }
    }

    draftTables.push({
      tbl,
      members,
      count,
      shapeKind,
      cx: bestCx,
      cy: bestCy,
      rx: tableRx,
      ry: tableRy,
      minCy,
      maxCy,
    });
  }

  // Table-vs-Table elliptical collision volume solver so two concurrent tables never collide
  for (let iter = 0; iter < 24; iter += 1) {
    for (let i = 0; i < draftTables.length; i += 1) {
      for (let j = i + 1; j < draftTables.length; j += 1) {
        const a = draftTables[i];
        const b = draftTables[j];
        let dx = b.cx - a.cx;
        let dy = b.cy - a.cy;
        const minDx = a.rx + b.rx + 124;
        const minDy = a.ry + b.ry + 116;
        if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
          dx = 20;
          dy = 40;
        }
        const norm = Math.hypot(dx / minDx, dy / minDy);
        if (norm < 1) {
          const push = (1 / Math.max(0.08, norm) - 1) * 0.5;
          a.cx = Math.max(440, Math.min(800, a.cx - dx * push));
          a.cy = Math.max(a.minCy, Math.min(a.maxCy, a.cy - dy * push));
          b.cx = Math.max(440, Math.min(800, b.cx + dx * push));
          b.cy = Math.max(b.minCy, Math.min(b.maxCy, b.cy + dy * push));
        }
      }
    }
  }

  const results: TablePlacement[] = [];
  for (const dt of draftTables) {
    const { tbl, members, count, shapeKind, cx: bestCx, cy: bestCy, rx: tableRx, ry: tableRy } = dt;
    const candidateSeats: { x: number; y: number; angle: number }[] = [];
    // Tight seating: walk outward along each seat angle until the agent's full box
    // (head + hanging name label) clears the table perimeter by a small margin.
    const halfW = AGENT_BOX_HALF_W * clearanceScale;
    const boxTop = AGENT_BOX_TOP * clearanceScale;
    const boxBottom = AGENT_BOX_BOTTOM * clearanceScale;
    const seatAt = (a: number) => {
      const ux = Math.cos(a);
      const uy = Math.sin(a);
      for (let t = Math.min(tableRx, tableRy); t < 600; t += 2) {
        const sx = bestCx + ux * t * (tableRx / Math.max(tableRx, tableRy));
        const sy = bestCy + uy * t * (tableRy / Math.max(tableRx, tableRy));
        const qx = Math.max(sx - halfW, Math.min(bestCx, sx + halfW));
        const qy = Math.max(sy - boxTop, Math.min(bestCy, sy + boxBottom));
        if (Math.hypot((qx - bestCx) / tableRx, (qy - bestCy) / tableRy) >= SEAT_BOX_MARGIN_NORM) {
          return { x: sx, y: sy, angle: a };
        }
      }
      return { x: bestCx + ux * (tableRx + SEAT_CLEARANCE_X), y: bestCy + uy * (tableRy + SEAT_CLEARANCE_TOP), angle: a };
    };
    if (shapeKind === 'rectangle') {
      candidateSeats.push(seatAt(Math.PI));
      candidateSeats.push(seatAt(0));
    } else {
      const startAngle = -Math.PI / 2;
      for (let i = 0; i < count; i += 1) {
        candidateSeats.push(seatAt(startAngle + (i / count) * Math.PI * 2));
      }
    }

    const usedSeatIdx = new Set<number>();
    const seatsByPersona: Record<string, { x: number; y: number }> = {};
    for (const pid of members) {
      const home = homeAnchorsById[pid] || HOME_ANCHORS_WORLD[pid] || { x: bestCx, y: bestCy };
      let chosen = 0;
      let minDist = Number.POSITIVE_INFINITY;
      for (let sIdx = 0; sIdx < candidateSeats.length; sIdx += 1) {
        if (usedSeatIdx.has(sIdx)) continue;
        const dist = Math.hypot(candidateSeats[sIdx].x - home.x, candidateSeats[sIdx].y - home.y);
        if (dist < minDist) {
          minDist = dist;
          chosen = sIdx;
        }
      }
      usedSeatIdx.add(chosen);
      seatsByPersona[pid] = {
        x: Math.max(306, Math.min(1000 - 74 * clearanceScale, candidateSeats[chosen].x)),
        y: Math.max(62 * clearanceScale, Math.min(600 - 74 * clearanceScale, candidateSeats[chosen].y)),
      };
    }

    results.push({
      collaborationId: tbl.collaboration_id,
      cx: bestCx,
      cy: bestCy,
      rx: tableRx,
      ry: tableRy,
      shapeKind,
      seatsByPersona,
    });
  }

  return results;
}

interface ResearchTableSvgProps {
  table: ActiveTableVisual;
  placement: TablePlacement;
  motionDisabled: boolean;
  isDragging?: boolean;
  suppressClickRef?: React.MutableRefObject<boolean>;
  onSilentSelectTable: (table: ActiveTableVisual) => void;
  onTablePointerDown?: (
    table: ActiveTableVisual,
    e: React.PointerEvent<SVGGElement>
  ) => void;
  onTablePointerEnter?: (
    table: ActiveTableVisual,
    placement: TablePlacement,
    e: React.PointerEvent<SVGGElement>
  ) => void;
  onTablePointerLeave?: (table: ActiveTableVisual) => void;
}

/**
 * Hand-drawn pen Research Table SVG element:
 * - Single persistent `<path>` element per `collaboration_id` (`data-testid="research-table-path-..."`)
 *   that continuously interpolates its 64 sampled points when `participant_persona_ids.length` changes
 *   (2 -> rectangle, 3-4 -> circle, 5-6 -> ellipse) WITHOUT unmounting!
 */
export const ResearchTableSvg: React.FC<ResearchTableSvgProps> = ({
  table,
  placement,
  motionDisabled,
  isDragging = false,
  suppressClickRef,
  onSilentSelectTable,
  onTablePointerDown,
  onTablePointerEnter,
  onTablePointerLeave,
}) => {
  const seed = useMemo(() => hashCollaborationSeed(table.collaboration_id), [table.collaboration_id]);
  const targetPts = useMemo(
    () => sampleTablePerimeter(placement.cx, placement.cy, table.participant_persona_ids.length, seed),
    [placement.cx, placement.cy, table.participant_persona_ids.length, seed]
  );

  const pathRef = useRef<SVGPathElement>(null);
  const currentPtsRef = useRef<[number, number][]>(targetPts);
  const [sketchProgress, setSketchProgress] = useState<number>(() =>
    table.fromSnapshot || motionDisabled ? 1 : 0
  );

  // Initial pen-sketch reveal (`Approach 0.8s -> Sketch outline 0.48s`) when live collaboration starts
  useEffect(() => {
    if (table.fromSnapshot || motionDisabled) {
      setSketchProgress(1);
      return undefined;
    }
    let raf = 0;
    const delayMs = 420;
    const durMs = 440;
    const t0 = performance.now();

    const step = (now: number) => {
      const elapsed = now - t0 - delayMs;
      if (elapsed <= 0) {
        setSketchProgress(0);
        raf = window.requestAnimationFrame(step);
        return;
      }
      const k = Math.min(1, elapsed / durMs);
      setSketchProgress(k);
      if (k < 1) {
        raf = window.requestAnimationFrame(step);
      }
    };
    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [table.collaboration_id, table.fromSnapshot, motionDisabled]);

  // Continuous 64-point morph whenever member count or center updates (`UI-05`, `UI-06`, `ui.table-morph-no-remount`)
  useEffect(() => {
    if (motionDisabled || isDragging) {
      currentPtsRef.current = targetPts;
      pathRef.current?.setAttribute('d', pointsToSvgPath(targetPts));
      return undefined;
    }

    const fromPts = currentPtsRef.current;
    const start = performance.now();
    const dur = 620;
    let raf = 0;

    const animateMorph = (now: number) => {
      const rawT = Math.min(1, (now - start) / dur);
      const k = rawT < 0.5 ? 4 * rawT * rawT * rawT : 1 - Math.pow(-2 * rawT + 2, 3) / 2;
      const blended: [number, number][] = targetPts.map((tp, idx) => {
        const fp = fromPts[idx] || tp;
        return [fp[0] + (tp[0] - fp[0]) * k, fp[1] + (tp[1] - fp[1]) * k];
      });
      currentPtsRef.current = blended;
      pathRef.current?.setAttribute('d', pointsToSvgPath(blended));
      if (rawT < 1) {
        raf = window.requestAnimationFrame(animateMorph);
      }
    };

    raf = window.requestAnimationFrame(animateMorph);
    return () => window.cancelAnimationFrame(raf);
  }, [targetPts, motionDisabled, isDragging]);

  const initialD = useMemo(() => pointsToSvgPath(currentPtsRef.current), []);
  const cx = placement.cx;
  const cy = placement.cy;

  return (
    <g
      className={`rep-table-group ${isDragging ? 'is-dragging' : ''}`}
      data-testid={`research-table-${table.collaboration_id}`}
      data-collaboration-id={table.collaboration_id}
      data-table-shape={placement.shapeKind}
      data-member-count={table.participant_persona_ids.length}
      style={{ opacity: sketchProgress > 0 ? 1 : 0 }}
      onPointerDown={(e) => onTablePointerDown?.(table, e)}
      onPointerEnter={(e) => onTablePointerEnter?.(table, placement, e)}
      onPointerLeave={() => onTablePointerLeave?.(table)}
      onClick={(e) => {
        e.stopPropagation();
        if (suppressClickRef?.current) return;
        onSilentSelectTable(table);
      }}
    >
      <path
        ref={pathRef}
        d={initialD}
        pathLength={1}
        data-testid={`research-table-path-${table.collaboration_id}`}
        className="rep-table-outline"
        style={{
          strokeDasharray: sketchProgress < 1 ? '1 1' : undefined,
          strokeDashoffset: sketchProgress < 1 ? String(1 - sketchProgress) : undefined,
          fillOpacity: Math.min(1, sketchProgress * 1.15),
        }}
      />

      {/* Two pen-drawn lines growing sequentially to represent active shared-context writing */}
      {sketchProgress > 0.75 && (
        <g
          className={`rep-table-traces ${!motionDisabled ? 'is-writing' : ''}`}
          data-testid={`table-context-motif-${table.collaboration_id}`}
          aria-hidden="true"
        >
          <path
            pathLength={1}
            d={`M${(cx - 22).toFixed(1)} ${(cy - 5.5).toFixed(1)} Q${(cx - 2).toFixed(1)} ${(cy - 7.1).toFixed(1)} ${(cx + 22).toFixed(1)} ${(cy - 5.2).toFixed(1)}`}
            className="rep-table-context-line line-1"
          />
          <path
            pathLength={1}
            d={`M${(cx - 22).toFixed(1)} ${(cy + 5.5).toFixed(1)} Q${(cx - 5).toFixed(1)} ${(cy + 6.9).toFixed(1)} ${(cx + 12).toFixed(1)} ${(cy + 5.4).toFixed(1)}`}
            className="rep-table-context-line line-2"
          />
        </g>
      )}
    </g>
  );
};
