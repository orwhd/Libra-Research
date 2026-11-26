import React, { useEffect, useMemo, useRef, useState } from 'react';
import { HOME_ANCHORS_WORLD, type ActiveTableVisual } from './CollaborationProjection';

export const SAMPLE_POINTS = 64;
const AGENT_SEAT_CLEARANCE = 64; // Keeps agent head + hanging name/status label clear of table perimeter
const SEAT_CLEARANCE_X = 68;
const SEAT_CLEARANCE_TOP = 64;
const SEAT_CLEARANCE_BOTTOM = 54;

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

    let bx = 0;
    let by = 0;
    if (shape === 'rectangle') {
      // Rounded superellipse p=5 approximating 160x74 hand-drawn rectangular table (§20.2)
      const hw = 80;
      const hh = 37;
      const exp = 2 / 4.6;
      bx = Math.sign(cos) * Math.pow(Math.abs(cos), exp) * hw;
      by = Math.sign(sin) * Math.pow(Math.abs(sin), exp) * hh;
    } else if (shape === 'circle') {
      const r = memberCount === 3 ? 78 : 88;
      bx = cos * r;
      by = sin * r;
    } else {
      const extra = Math.max(0, memberCount - 6);
      const rx = 126 + extra * 14;
      const ry = 74 + extra * 10;
      bx = cos * rx;
      by = sin * ry;
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
  homeAnchorsById: Record<string, { x: number; y: number }> = HOME_ANCHORS_WORLD
): TablePlacement[] {
  const draftTables: {
    tbl: ActiveTableVisual;
    members: string[];
    count: number;
    shapeKind: TableShapeKind;
    cx: number;
    cy: number;
    rx: number;
    ry: number;
  }[] = [];

  for (const tbl of tables) {
    const members = tbl.participant_persona_ids;
    const count = members.length;
    const shapeKind = getTableShapeKind(count);
    let tableRx = 80;
    let tableRy = 37;
    if (shapeKind === 'circle') {
      const r = count === 3 ? 78 : 88;
      tableRx = r;
      tableRy = r;
    } else if (shapeKind === 'ellipse') {
      const extra = Math.max(0, count - 6);
      tableRx = 126 + extra * 14;
      tableRy = 74 + extra * 10;
    }

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
    const minCy = 198;
    const maxCy = 415;
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
          if (d < 165) cost += (165 - d) * 3.4;
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
          a.cy = Math.max(185, Math.min(425, a.cy - dy * push));
          b.cx = Math.max(440, Math.min(800, b.cx + dx * push));
          b.cy = Math.max(185, Math.min(425, b.cy + dy * push));
        }
      }
    }
  }

  const results: TablePlacement[] = [];
  for (const dt of draftTables) {
    const { tbl, members, count, shapeKind, cx: bestCx, cy: bestCy, rx: tableRx, ry: tableRy } = dt;
    const candidateSeats: { x: number; y: number; angle: number }[] = [];
    if (shapeKind === 'rectangle') {
      const seatRx = tableRx + SEAT_CLEARANCE_X;
      candidateSeats.push({ x: bestCx - seatRx, y: bestCy, angle: Math.PI });
      candidateSeats.push({ x: bestCx + seatRx, y: bestCy, angle: 0 });
    } else {
      const seatRx = tableRx + SEAT_CLEARANCE_X;
      const startAngle = -Math.PI / 2;
      for (let i = 0; i < count; i += 1) {
        const a = startAngle + (i / count) * Math.PI * 2;
        const seatRy = tableRy + (Math.sin(a) < 0 ? SEAT_CLEARANCE_TOP : SEAT_CLEARANCE_BOTTOM);
        candidateSeats.push({
          x: bestCx + Math.cos(a) * seatRx,
          y: bestCy + Math.sin(a) * seatRy,
          angle: a,
        });
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
        x: Math.max(306, Math.min(940, candidateSeats[chosen].x)),
        y: Math.max(66, Math.min(530, candidateSeats[chosen].y)),
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
  onSilentSelectTable: (table: ActiveTableVisual) => void;
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
  onSilentSelectTable,
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
    if (motionDisabled) {
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
  }, [targetPts, motionDisabled]);

  const initialD = useMemo(() => pointsToSvgPath(currentPtsRef.current), []);
  const cx = placement.cx;
  const cy = placement.cy;

  return (
    <g
      className="rep-table-group"
      data-testid={`research-table-${table.collaboration_id}`}
      data-collaboration-id={table.collaboration_id}
      data-table-shape={placement.shapeKind}
      data-member-count={table.participant_persona_ids.length}
      style={{ opacity: sketchProgress > 0 ? 1 : 0 }}
      onPointerEnter={(e) => onTablePointerEnter?.(table, placement, e)}
      onPointerLeave={() => onTablePointerLeave?.(table)}
      onClick={(e) => {
        e.stopPropagation();
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
