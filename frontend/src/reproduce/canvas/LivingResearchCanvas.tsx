import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Clock,
  Mail,
  Maximize2,
  MessageSquare,
  Minimize2,
  MoveHorizontal,
  Pin,
  Presentation,
  RotateCcw,
  Undo2,
} from 'lucide-react';
import { MarkdownMath } from '../../components/MarkdownMath';
import { toCompactLine, toPlainUiText } from '../plainText';
import type {
  BriefingRecord,
  Collaboration,
  HumanInterventionRecord,
  Persona,
  ReproduceRunMode,
  TableDialogueRecord,
  TableDialogueTurn,
  TimelineSnapshotNode,
  TimelineStage,
} from '../types';
import { BriefingEaselSvg } from './BriefingEaselSvg';
import {
  cloneSnapshotSpatialState,
  computeInitialPlacedCardWorldPos,
  DEFAULT_CARD_EXTENTS,
  DEFAULT_CARD_SIZE,
  emptySnapshotSpatialRecord,
  loadRunSpatialRecords,
  MAX_CARD_SIZE,
  MIN_CARD_SIZE,
  resolveClusterAndCardCollisions,
  saveRunSpatialRecords,
  type PlacedCanvasCard,
  type ResolvedPlacedCard,
  type ShiftedBriefingCluster,
  type SnapshotSpatialRecord,
  type SnapshotSpatialState,
} from './CanvasSpatialState';
import {
  computeLiveBubblePlacements,
  computePersonaHomeAnchors,
  resolvePersonaCollisions,
  type ActiveTableVisual,
} from './CollaborationProjection';
import { InterventionMailSvg } from './InterventionMailSvg';
import { getAgentBubbleStyle, PersonaSvg, type PersonaInteractionRole, type WorldPoint } from './PersonaSvg';
import { AgentInteractionFlows, type HandoffFlow, type SpeechFlow } from './AgentInteractionFlows';
import { computeTablePlacements, ResearchTableSvg, type TablePlacement } from './ResearchTableSvg';

interface LivingResearchCanvasProps {
  runId?: string;
  mode: ReproduceRunMode;
  isHistoricalReplay: boolean;
  replaySeq: number | null;
  personas: Persona[];
  tables: ActiveTableVisual[];
  overflowCollaborations: Collaboration[];
  timelineSnapshots?: TimelineSnapshotNode[];
  activeSnapshotId?: string | null;
  tableDialogues?: Record<string, TableDialogueRecord>;
  briefings?: Record<string, BriefingRecord>;
  interventions?: Record<string, HumanInterventionRecord>;
  activeIntervention?: HumanInterventionRecord | null;
  onSelectTimelineSnapshot?: (snap: TimelineSnapshotNode) => void;
  onReturnToLive?: () => void;
  onSilentSelectPersona?: (persona: Persona) => void;
  onSilentSelectTable: (table: ActiveTableVisual) => void;
  onSilentSelectBriefing?: (briefing: {
    presenterIds: string[];
    topic: string;
    linkedStepIds?: string[];
  }) => void;
  onSelectIntervention?: (intervention: HumanInterventionRecord) => void;
}

const MIN_SCALE = 0.28;
/** Bookkeeping turns that collapse to a single quiet line in compact dialogue popovers. */
const PROCEDURAL_STANCES = new Set(['expand', 'pod', 'align_data', 'resolved', 'join', 'leave', 'members_changed', 'shrink']);
const MAX_SCALE = 2.2;
const PAN_LIMIT_X = 4200;
const PAN_LIMIT_Y = 3200;

/**
 * Derive a single concise line describing what agents around an active table
 * are doing right now in Live mode.
 */
function shortPopoverTitle(topic: string | null | undefined): string {
  const plain = toPlainUiText(topic, 200);
  const head = plain.split(/[:—(]/)[0].trim();
  return toCompactLine(head || plain, 34);
}

function deriveLiveTableStatusLine(
  table: ActiveTableVisual,
  dialogue: TableDialogueRecord | undefined,
  intervention: HumanInterventionRecord | null
): string {
  if (
    intervention &&
    intervention.status === 'pending' &&
    (!intervention.collaboration_id || intervention.collaboration_id === table.collaboration_id)
  ) {
    return 'Awaiting human guidance';
  }
  const turns = dialogue?.turns || [];
  if (turns.length > 0) {
    const last = turns[turns.length - 1];
    const who = (last.speaker_label || 'Agents').replace(/\s*\([^)]*\)/g, '').trim();
    const stanceMap: Record<string, string> = {
      challenge: `${who} challenging`,
      propose_test: `${who} proposing tests`,
      escalate: `${who} escalating`,
      human_guidance: 'Boss relaying guidance',
      expand: 'Team expanding',
      align_data: `${who} checking data`,
      pod: `${who} running checks`,
      verify: `${who} verifying`,
      consensus: `${who} concluding`,
    };
    if (stanceMap[last.stance]) {
      return stanceMap[last.stance];
    }
    const clean = toCompactLine(last.summary, 22);
    if (clean) {
      return `${who}: ${clean}`;
    }
  }
  const rawTopic = toPlainUiText(
    table.topic || dialogue?.start_reason || 'Cross-checking findings',
    64
  );
  const shortTopic = rawTopic.split(/[:—]/)[0].trim();
  return toCompactLine(shortTopic || rawTopic, 28);
}

const STAGE_META: Record<TimelineStage, { key: TimelineStage; label: string; color: string }> = {
  setup: { key: 'setup', label: 'Setup', color: '#3b82f6' },
  briefing: { key: 'briefing', label: 'Brief', color: '#8b5cf6' },
  debate: { key: 'debate', label: 'Debate', color: '#f59e0b' },
  intervention: { key: 'intervention', label: 'Review', color: '#f43f5e' },
  synthesis: { key: 'synthesis', label: 'Report', color: '#10b981' },
};

function getSnapshotStageMeta(snap: TimelineSnapshotNode): {
  key: TimelineStage;
  label: string;
  color: string;
} {
  if (snap.stage && STAGE_META[snap.stage]) {
    return STAGE_META[snap.stage];
  }
  switch (snap.kind) {
    case 'briefing':
      return STAGE_META.briefing;
    case 'huddle_start':
    case 'huddle_expand':
      return STAGE_META.debate;
    case 'intervention':
      return STAGE_META.intervention;
    case 'huddle_end':
    case 'run_completed':
      return STAGE_META.synthesis;
    default:
      return STAGE_META.setup;
  }
}

/**
 * Read-only Living Research Canvas (L1.5 §1-§5, §20):
 * - First frame renders all 6 configured role classes (`Main` left Boss, 5 specialist classes right).
 * - Dynamically spawned instances within any specialist class fan out cleanly without overlap.
 * - Uses Data-page canvas pan/zoom semantics: pointer-centered Ctrl/Cmd+Wheel, left-drag pan,
 *   horizontal %-pill drag zoom (`MoveHorizontal`), and `Fit All`.
 * - Zero permanent edges, zero document cards, zero Inspector panels, zero write POSTs.
 */
export const LivingResearchCanvas: React.FC<LivingResearchCanvasProps> = ({
  runId = 'pending',
  mode,
  isHistoricalReplay,
  replaySeq,
  personas,
  tables,
  overflowCollaborations,
  timelineSnapshots = [],
  activeSnapshotId = null,
  tableDialogues = {},
  briefings = {},
  interventions = {},
  activeIntervention = null,
  onSelectTimelineSnapshot,
  onReturnToLive,
  onSilentSelectPersona,
  onSilentSelectTable,
  onSilentSelectBriefing,
  onSelectIntervention,
}) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const [prefersReduced, setPrefersReduced] = useState<boolean>(() =>
    typeof window !== 'undefined'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false
  );
  const [view, setView] = useState<{ x: number; y: number; scale: number }>({
    x: 0,
    y: 0,
    scale: 1,
  });
  const [isZoomScrubbing, setIsZoomScrubbing] = useState(false);
  const [zoomAnnouncement, setZoomAnnouncement] = useState('');
  const [hoveredTable, setHoveredTable] = useState<{
    collabId: string;
    x: number;
    y: number;
  } | null>(null);
  const [hoveredBriefing, setHoveredBriefing] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [hoveredIntervention, setHoveredIntervention] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [expandedPopover, setExpandedPopover] = useState<'table' | 'briefing' | null>(null);
  const [expandedPlacedCardId, setExpandedPlacedCardId] = useState<string | null>(null);
  const [hoveredSnapId, setHoveredSnapId] = useState<string | null>(null);
  const [spatialBySnapshot, setSpatialBySnapshot] = useState<Record<string, SnapshotSpatialRecord>>(() =>
    loadRunSpatialRecords(runId)
  );
  const [placedCardDims, setPlacedCardDims] = useState<Record<string, { rx: number; ry: number }>>({});
  const [dragSession, setDragSession] = useState<{
    activeKey: string;
    kind: 'single_agent' | 'agent_cluster' | 'placed_card' | 'placed_card_resize';
    targetKey: string;
    previewState: SnapshotSpatialState;
  } | null>(null);

  const hoverCloseTimerRef = useRef<number | null>(null);
  const briefingCloseTimerRef = useRef<number | null>(null);
  const interventionCloseTimerRef = useRef<number | null>(null);
  const histogramRef = useRef<HTMLDivElement>(null);
  const histScrollTargetRef = useRef<number | null>(null);
  const histScrollRafRef = useRef<number | null>(null);
  const worldStageRef = useRef<HTMLDivElement>(null);
  const placedCardElsRef = useRef<Record<string, HTMLDivElement | null>>({});
  const suppressClickRef = useRef<boolean>(false);
  const liveWorldCoordsRef = useRef<Record<string, { x: number; y: number }>>({});
  const liveResolvedCardsRef = useRef<ResolvedPlacedCard[]>([]);
  const liveClusterOffsetsRef = useRef<Record<string, { dx: number; dy: number }>>({});
  const activeDragRef = useRef<{
    pointerId: number;
    kind: 'single_agent' | 'agent_cluster' | 'placed_card' | 'placed_card_resize';
    targetKey: string;
    activeKey: string;
    label: string;
    snapKey: string;
    startClientX: number;
    startClientY: number;
    baseX: number;
    baseY: number;
    baseDx: number;
    baseDy: number;
    startW: number;
    startH: number;
    resizeDir?: 'e' | 's' | 'se' | 'sw';
    initialState: SnapshotSpatialState;
    moved: boolean;
    lastPreview: SnapshotSpatialState;
  } | null>(null);

  const panStartRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const zoomScrubRef = useRef<{
    pointerId: number;
    startClientX: number;
    startScale: number;
    startX: number;
    startY: number;
    centerX: number;
    centerY: number;
  } | null>(null);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = (e: MediaQueryListEvent) => setPrefersReduced(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    setSpatialBySnapshot(loadRunSpatialRecords(runId));
  }, [runId]);

  const updateSpatialBySnapshot = useCallback(
    (updater: (prev: Record<string, SnapshotSpatialRecord>) => Record<string, SnapshotSpatialRecord>) => {
      setSpatialBySnapshot((prev) => {
        const next = updater(prev);
        if (runId && runId !== 'pending') {
          saveRunSpatialRecords(runId, next);
        }
        return next;
      });
    },
    [runId]
  );

  useEffect(() => {
    if (!isHistoricalReplay) {
      setHoveredTable(null);
      setHoveredBriefing(null);
      setExpandedPopover(null);
      setExpandedPlacedCardId(null);
      setDragSession(null);
      activeDragRef.current = null;
    }
  }, [isHistoricalReplay]);

  useEffect(() => {
    setHoveredTable(null);
    setHoveredBriefing(null);
    setExpandedPopover(null);
    setExpandedPlacedCardId(null);
    setDragSession(null);
    activeDragRef.current = null;
    if (activeSnapshotId && histogramRef.current) {
      const histEl = histogramRef.current;
      const activeBtn = histEl.querySelector<HTMLElement>(
        `[data-testid="timeline-snap-${activeSnapshotId}"]`
      );
      if (activeBtn) {
        const maxScroll = Math.max(0, histEl.scrollWidth - histEl.clientWidth);
        const targetLeft = Math.max(
          0,
          Math.min(maxScroll, activeBtn.offsetLeft - histEl.clientWidth / 2 + activeBtn.offsetWidth / 2)
        );
        histScrollTargetRef.current = targetLeft;
        histEl.scrollTo({
          left: targetLeft,
          behavior: prefersReduced ? 'auto' : 'smooth',
        });
      }
    }
  }, [activeSnapshotId, prefersReduced]);

  useEffect(() => {
    return () => {
      if (histScrollRafRef.current !== null) {
        window.cancelAnimationFrame(histScrollRafRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const histEl = histogramRef.current;
    if (!histEl) return undefined;

    const onHistWheel = (e: WheelEvent) => {
      e.stopPropagation();
      e.preventDefault();
      const rawDelta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      const delta = e.deltaMode === 1 ? rawDelta * 32 : rawDelta;
      const maxScroll = Math.max(0, histEl.scrollWidth - histEl.clientWidth);
      if (prefersReduced) {
        histEl.scrollLeft = Math.max(0, Math.min(maxScroll, histEl.scrollLeft + delta));
        return;
      }
      const base = histScrollTargetRef.current ?? histEl.scrollLeft;
      histScrollTargetRef.current = Math.max(0, Math.min(maxScroll, base + delta));
      if (histScrollRafRef.current !== null) return;

      const step = () => {
        const cur = histEl.scrollLeft;
        const target = histScrollTargetRef.current;
        if (target === null) {
          histScrollRafRef.current = null;
          histEl.style.scrollBehavior = '';
          return;
        }
        const diff = target - cur;
        if (Math.abs(diff) < 0.75) {
          histEl.style.scrollBehavior = 'auto';
          histEl.scrollLeft = target;
          histEl.style.scrollBehavior = '';
          histScrollTargetRef.current = null;
          histScrollRafRef.current = null;
          return;
        }
        histEl.style.scrollBehavior = 'auto';
        histEl.scrollLeft = cur + diff * 0.24;
        histScrollRafRef.current = window.requestAnimationFrame(step);
      };
      histScrollRafRef.current = window.requestAnimationFrame(step);
    };

    histEl.addEventListener('wheel', onHistWheel, { passive: false });
    return () => {
      histEl.removeEventListener('wheel', onHistWheel);
    };
  }, [prefersReduced, timelineSnapshots.length]);

  useEffect(() => {
    if (!expandedPopover && !expandedPlacedCardId) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setExpandedPopover(null);
        setExpandedPlacedCardId(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [expandedPopover, expandedPlacedCardId]);

  const activeSnap =
    timelineSnapshots.find((s) => s.snapshot_id === activeSnapshotId) ||
    (isHistoricalReplay && replaySeq !== null
      ? timelineSnapshots.find((s) => s.seq === replaySeq) || null
      : null);

  const activeSnapKey = isHistoricalReplay
    ? activeSnap?.snapshot_id || (replaySeq !== null ? `seq_${replaySeq}` : 'replay_default')
    : null;

  const currentSnapRecord: SnapshotSpatialRecord =
    activeSnapKey && spatialBySnapshot[activeSnapKey]
      ? spatialBySnapshot[activeSnapKey]
      : emptySnapshotSpatialRecord();

  const activeSpatialState: SnapshotSpatialState = useMemo(() => {
    if (isHistoricalReplay && dragSession) {
      return dragSession.previewState;
    }
    if (isHistoricalReplay && activeSnapKey && spatialBySnapshot[activeSnapKey]) {
      const rec = spatialBySnapshot[activeSnapKey];
      return {
        singleAgentCoords: rec.singleAgentCoords,
        clusterOffsets: rec.clusterOffsets,
        placedCards: rec.placedCards,
      };
    }
    return { singleAgentCoords: {}, clusterOffsets: {}, placedCards: {} };
  }, [isHistoricalReplay, dragSession, activeSnapKey, spatialBySnapshot]);

  // Responsive persona sizing: narrower canvases render agents relatively larger so they stay legible
  const [personaScale, setPersonaScale] = useState(1);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const compute = (w: number) => {
      const next = w >= 1180 ? 1 : w >= 920 ? 1.08 : w >= 700 ? 1.14 : 1.2;
      setPersonaScale((prev) => (prev === next ? prev : next));
    };
    compute(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) compute(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const homeAnchorsById = useMemo(() => computePersonaHomeAnchors(personas), [personas]);
  const allPersonaIds = useMemo(() => personas.map((p) => p.persona_id), [personas]);

  const basePlacements = useMemo(
    () => computeTablePlacements(tables, allPersonaIds, homeAnchorsById, personaScale),
    [tables, allPersonaIds, homeAnchorsById, personaScale]
  );

  const baseTableSeatedPersonaIds = useMemo(() => {
    const seated = new Set<string>();
    for (const pl of basePlacements) {
      for (const pid of Object.keys(pl.seatsByPersona)) {
        if (pid !== 'persona_main') seated.add(pid);
      }
    }
    return seated;
  }, [basePlacements]);

  const reportingPersonas = useMemo(
    () =>
      personas.filter(
        (p) =>
          !p.is_main &&
          p.persona_id !== 'persona_main' &&
          (p.state === 'reporting' || p.state === 'handoff') &&
          !baseTableSeatedPersonaIds.has(p.persona_id)
      ),
    [personas, baseTableSeatedPersonaIds]
  );

  const baseBriefingCluster = useMemo<ShiftedBriefingCluster | null>(() => {
    if (reportingPersonas.length === 0) return null;
    const easelCx = 258;
    const easelCy = 294;
    const mainBase = homeAnchorsById['persona_main'] || { x: 165, y: 294 };
    const presenterCoords: Record<string, { x: number; y: number }> = {};
    if (reportingPersonas.length === 1) {
      presenterCoords[reportingPersonas[0].persona_id] = { x: 360, y: 294 };
    } else if (reportingPersonas.length === 2) {
      presenterCoords[reportingPersonas[0].persona_id] = { x: 368, y: 218 };
      presenterCoords[reportingPersonas[1].persona_id] = { x: 368, y: 370 };
    } else {
      const span = Math.min(260, (reportingPersonas.length - 1) * 124);
      reportingPersonas.forEach((rp, idx) => {
        const frac = idx / (reportingPersonas.length - 1);
        presenterCoords[rp.persona_id] = { x: 372, y: 294 - span / 2 + frac * span };
      });
    }
    const pts = [
      { x: easelCx, y: easelCy },
      mainBase,
      ...Object.values(presenterCoords),
    ];
    const minX = Math.min(...pts.map((p) => p.x - 74));
    const maxX = Math.max(...pts.map((p) => p.x + 64));
    const minY = Math.min(...pts.map((p) => p.y - 78));
    const maxY = Math.max(...pts.map((p) => p.y + 78));
    return {
      easelCx,
      easelCy,
      mainCoord: { ...mainBase },
      presenterCoords,
      cx: (minX + maxX) / 2,
      cy: (minY + maxY) / 2,
      outerRx: (maxX - minX) / 2,
      outerRy: (maxY - minY) / 2,
    };
  }, [reportingPersonas, homeAnchorsById]);

  const macroResolved = useMemo(() => {
    const shiftedTables: TablePlacement[] = basePlacements.map((pl) => {
      const off = isHistoricalReplay ? activeSpatialState.clusterOffsets[`table:${pl.collaborationId}`] : undefined;
      if (!off) return pl;
      const seats: Record<string, { x: number; y: number }> = {};
      for (const [pid, pos] of Object.entries(pl.seatsByPersona)) {
        seats[pid] = { x: pos.x + off.dx, y: pos.y + off.dy };
      }
      return { ...pl, cx: pl.cx + off.dx, cy: pl.cy + off.dy, seatsByPersona: seats };
    });

    const shiftedBrief: ShiftedBriefingCluster | null = baseBriefingCluster
      ? (() => {
          const off = isHistoricalReplay ? activeSpatialState.clusterOffsets.briefing : undefined;
          if (!off) return baseBriefingCluster;
          const pCoords: Record<string, { x: number; y: number }> = {};
          for (const [pid, pos] of Object.entries(baseBriefingCluster.presenterCoords)) {
            pCoords[pid] = { x: pos.x + off.dx, y: pos.y + off.dy };
          }
          return {
            ...baseBriefingCluster,
            easelCx: baseBriefingCluster.easelCx + off.dx,
            easelCy: baseBriefingCluster.easelCy + off.dy,
            cx: baseBriefingCluster.cx + off.dx,
            cy: baseBriefingCluster.cy + off.dy,
            mainCoord: {
              x: baseBriefingCluster.mainCoord.x + off.dx,
              y: baseBriefingCluster.mainCoord.y + off.dy,
            },
            presenterCoords: pCoords,
          };
        })()
      : null;

    if (!isHistoricalReplay) {
      return {
        placements: shiftedTables,
        briefingCluster: shiftedBrief,
        resolvedCards: [] as ResolvedPlacedCard[],
      };
    }

    const cards = Object.values(activeSpatialState.placedCards).filter((c) => {
      if (c.kind === 'table') return tables.some((t) => t.collaboration_id === c.targetId);
      if (c.kind === 'briefing') return reportingPersonas.length > 0;
      return false;
    });

    return resolveClusterAndCardCollisions({
      placements: shiftedTables,
      briefingCluster: shiftedBrief,
      placedCards: cards,
      measuredCardDims: placedCardDims,
      activeDragKey: dragSession?.activeKey ?? null,
    });
  }, [
    basePlacements,
    baseBriefingCluster,
    isHistoricalReplay,
    activeSpatialState,
    tables,
    reportingPersonas.length,
    placedCardDims,
    dragSession,
  ]);

  const placements = macroResolved.placements;
  const effectiveBriefingCluster = macroResolved.briefingCluster;
  const resolvedPlacedCards = macroResolved.resolvedCards;
  liveResolvedCardsRef.current = resolvedPlacedCards;
  const nextLiveOffsets: Record<string, { dx: number; dy: number }> = {
    ...activeSpatialState.clusterOffsets,
  };
  for (const pl of placements) {
    const basePl = basePlacements.find((b) => b.collaborationId === pl.collaborationId);
    if (basePl) {
      const dx = pl.cx - basePl.cx;
      const dy = pl.cy - basePl.cy;
      if (Math.hypot(dx, dy) > 0.25) nextLiveOffsets[`table:${pl.collaborationId}`] = { dx, dy };
    }
  }
  if (effectiveBriefingCluster && baseBriefingCluster) {
    const dx = effectiveBriefingCluster.cx - baseBriefingCluster.cx;
    const dy = effectiveBriefingCluster.cy - baseBriefingCluster.cy;
    if (Math.hypot(dx, dy) > 0.25) nextLiveOffsets.briefing = { dx, dy };
  }
  liveClusterOffsetsRef.current = nextLiveOffsets;

  const placementByCollabId = useMemo(
    () => new Map(placements.map((p) => [p.collaborationId, p])),
    [placements]
  );

  const clusterKeyByPersona = useMemo(() => {
    const map: Record<string, string> = {};
    for (const pl of placements) {
      for (const pid of Object.keys(pl.seatsByPersona)) {
        if (pid !== 'persona_main') {
          map[pid] = `table:${pl.collaborationId}`;
        }
      }
    }
    if (effectiveBriefingCluster) {
      map['persona_main'] = 'briefing';
      for (const pid of Object.keys(effectiveBriefingCluster.presenterCoords)) {
        map[pid] = 'briefing';
      }
    }
    return map;
  }, [placements, effectiveBriefingCluster]);

  const liveBubblePlacements = useMemo(() => {
    if (isHistoricalReplay || tables.length === 0) return [];
    const tableInputs = tables
      .map((tbl) => {
        const pl = placementByCollabId.get(tbl.collaboration_id);
        if (!pl) return null;
        return {
          collaborationId: tbl.collaboration_id,
          cx: pl.cx,
          cy: pl.cy,
          rx: pl.rx,
          ry: pl.ry,
          seatsByPersona: pl.seatsByPersona,
          text: deriveLiveTableStatusLine(
            tbl,
            tableDialogues[tbl.collaboration_id],
            activeIntervention
          ),
        };
      })
      .filter((item): item is NonNullable<typeof item> => Boolean(item));
    return computeLiveBubblePlacements({
      tables: tableInputs,
      homeAnchorsById,
      personas,
      sizeScale: personaScale,
    });
  }, [isHistoricalReplay, tables, placementByCollabId, tableDialogues, activeIntervention, homeAnchorsById, personas, personaScale]);

  const worldCoordsByPersona = useMemo(() => {
    const raw: Record<string, { x: number; y: number }> = { ...homeAnchorsById };
    if (isHistoricalReplay) {
      for (const [pid, pos] of Object.entries(activeSpatialState.singleAgentCoords)) {
        if (!clusterKeyByPersona[pid]) {
          raw[pid] = { x: pos.x, y: pos.y };
        }
      }
    }
    const pinnedIds = new Set<string>();
    const groupOf: Record<string, string> = {};
    const tableObstacles = placements.map((pl) => {
      const memberIds = Object.keys(pl.seatsByPersona).filter((pid) => pid !== 'persona_main');
      let outerRx = pl.rx;
      let outerRy = pl.ry;
      for (const pid of memberIds) {
        raw[pid] = pl.seatsByPersona[pid];
        pinnedIds.add(pid);
        groupOf[pid] = `table:${pl.collaborationId}`;
        outerRx = Math.max(outerRx, Math.abs(pl.seatsByPersona[pid].x - pl.cx));
        outerRy = Math.max(outerRy, Math.abs(pl.seatsByPersona[pid].y - pl.cy));
      }
      return { cx: pl.cx, cy: pl.cy, rx: pl.rx, ry: pl.ry, memberIds, outerRx, outerRy };
    });
    if (effectiveBriefingCluster) {
      raw['persona_main'] = effectiveBriefingCluster.mainCoord;
      pinnedIds.add('persona_main');
      groupOf['persona_main'] = 'briefing';
      for (const [pid, pos] of Object.entries(effectiveBriefingCluster.presenterCoords)) {
        raw[pid] = pos;
        pinnedIds.add(pid);
        groupOf[pid] = 'briefing';
      }
    }

    return resolvePersonaCollisions({
      personas,
      rawCoords: raw,
      tableObstacles,
      easelObstacle: effectiveBriefingCluster
        ? { cx: effectiveBriefingCluster.easelCx, cy: effectiveBriefingCluster.easelCy, rx: 44, ry: 42 }
        : null,
      briefingClusterObstacle: effectiveBriefingCluster
        ? {
            cx: effectiveBriefingCluster.cx,
            cy: effectiveBriefingCluster.cy,
            outerRx: effectiveBriefingCluster.outerRx,
            outerRy: effectiveBriefingCluster.outerRy,
            memberIds: ['persona_main', ...Object.keys(effectiveBriefingCluster.presenterCoords)],
          }
        : null,
      bubbleObstacles: liveBubblePlacements,
      pinnedIds,
      sizeScale: personaScale,
      groupOf,
      allowInfiniteBounds: isHistoricalReplay,
      lockClusterSeats: isHistoricalReplay,
      activeDraggedPersonaId: dragSession?.kind === 'single_agent' ? dragSession.targetKey : null,
    });
  }, [
    personas,
    homeAnchorsById,
    isHistoricalReplay,
    activeSpatialState.singleAgentCoords,
    clusterKeyByPersona,
    placements,
    effectiveBriefingCluster,
    liveBubblePlacements,
    personaScale,
    dragSession,
  ]);
  liveWorldCoordsRef.current = worldCoordsByPersona;

  const placedCardKeySig = resolvedPlacedCards.map((c) => c.cardId).join('|');
  useEffect(() => {
    if (!isHistoricalReplay || !placedCardKeySig) return undefined;
    const stageEl = worldStageRef.current;
    if (!stageEl || typeof ResizeObserver === 'undefined') return undefined;

    const syncDims = () => {
      const stageW = stageEl.offsetWidth || 1000;
      const stageH = stageEl.offsetHeight || 600;
      setPlacedCardDims((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const id of placedCardKeySig.split('|')) {
          const el = placedCardElsRef.current[id];
          if (!el) continue;
          const rx = Math.max(120, Math.round((el.offsetWidth / stageW) * 500));
          const ry = Math.max(70, Math.round((el.offsetHeight / stageH) * 300));
          const cur = prev[id];
          if (!cur || Math.abs(cur.rx - rx) > 3 || Math.abs(cur.ry - ry) > 3) {
            next[id] = { rx, ry };
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    };

    const raf = window.requestAnimationFrame(syncDims);
    const ro = new ResizeObserver(syncDims);
    for (const id of placedCardKeySig.split('|')) {
      const el = placedCardElsRef.current[id];
      if (el) ro.observe(el);
    }
    return () => {
      window.cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [isHistoricalReplay, placedCardKeySig]);

  const speakingSet = useMemo(() => {
    const s = new Set<string>();
    for (const t of tables) {
      if (t.speakingPersonaId) s.add(t.speakingPersonaId);
    }
    return s;
  }, [tables]);

  const applyZoomCentered = useCallback(
    (
      nextScaleRaw: number,
      base?: { scale: number; x: number; y: number; centerX: number; centerY: number }
    ) => {
      const clampedScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, nextScaleRaw));
      if (base) {
        const ratio = clampedScale / base.scale;
        setView({
          scale: clampedScale,
          x: base.centerX - (base.centerX - base.x) * ratio,
          y: base.centerY - (base.centerY - base.y) * ratio,
        });
        return clampedScale;
      }
      const rect = rootRef.current?.getBoundingClientRect();
      const centerX = rect ? rect.width / 2 : 480;
      const centerY = rect ? rect.height / 2 : 320;
      setView((prev) => {
        const ratio = clampedScale / prev.scale;
        return {
          scale: clampedScale,
          x: centerX - (centerX - prev.x) * ratio,
          y: centerY - (centerY - prev.y) * ratio,
        };
      });
      return clampedScale;
    },
    []
  );

  const fitAll = useCallback(() => {
    const root = rootRef.current;
    if (!root || personas.length === 0) {
      setView({ x: 0, y: 0, scale: 1 });
      return;
    }
    const xs: number[] = [];
    const ys: number[] = [];
    for (const p of personas) {
      const c = worldCoordsByPersona[p.persona_id] || { x: 500, y: 300 };
      xs.push(c.x - 80, c.x + 80);
      ys.push(c.y - 80, c.y + 90);
    }
    for (const pl of placements) {
      xs.push(pl.cx - pl.rx - 20, pl.cx + pl.rx + 20);
      ys.push(pl.cy - pl.ry - 20, pl.cy + pl.ry + 20);
    }
    for (const card of resolvedPlacedCards) {
      xs.push(card.cx - card.rx - 16, card.cx + card.rx + 16);
      ys.push(card.cy - card.ry - 16, card.cy + card.ry + 16);
    }
    const minWX = Math.min(...xs);
    const maxWX = Math.max(...xs);
    const minWY = Math.min(...ys);
    const maxWY = Math.max(...ys);

    const spanRatioX = Math.max(0.25, (maxWX - minWX) / 1000);
    const spanRatioY = Math.max(0.25, (maxWY - minWY) / 600);
    const nextScale = Math.max(
      MIN_SCALE,
      Math.min(1.18, 0.92 / Math.max(spanRatioX, spanRatioY))
    );

    const rect = root.getBoundingClientRect();
    const cxRatio = (minWX + maxWX) / 2 / 1000 - 0.5;
    const cyRatio = (minWY + maxWY) / 2 / 600 - 0.5;
    const tx = -cxRatio * Math.min(rect.width * 0.92, 1120) * nextScale;
    const ty = -cyRatio * (Math.min(rect.width * 0.92, 1120) * 0.6) * nextScale;
    setView({ x: tx, y: ty, scale: nextScale });
    setZoomAnnouncement(`Canvas fit all (${Math.round(nextScale * 100)}%)`);
  }, [personas, worldCoordsByPersona, placements, resolvedPlacedCards]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const onWheel = (e: WheelEvent) => {
      const targetEl = e.target as HTMLElement | null;
      if (targetEl?.closest('.rep-table-dialogue-popover, .rep-canvas-timeline-dock')) {
        return;
      }
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const rect = root.getBoundingClientRect();
        const px = e.clientX - rect.left - rect.width / 2;
        const py = e.clientY - rect.top - rect.height / 2;
        const factor = e.deltaY < 0 ? 1.08 : 0.925;
        setView((prev) => {
          const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, prev.scale * factor));
          const ratio = nextScale / prev.scale;
          return {
            scale: nextScale,
            x: px - (px - prev.x) * ratio,
            y: py - (py - prev.y) * ratio,
          };
        });
      } else {
        const dx = e.shiftKey && e.deltaX === 0 ? e.deltaY : e.deltaX;
        const dy = e.shiftKey && e.deltaX === 0 ? 0 : e.deltaY;
        setView((prev) => ({
          ...prev,
          x: Math.max(-PAN_LIMIT_X, Math.min(PAN_LIMIT_X, prev.x - dx)),
          y: Math.max(-PAN_LIMIT_Y, Math.min(PAN_LIMIT_Y, prev.y - dy)),
        }));
      }
    };

    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, []);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if (
      (e.target as HTMLElement).closest(
        'button, .rep-table-group, .rep-briefing-easel, .rep-intervention-mail, .rep-persona-node.is-replay-draggable, .canvas-hud-top, .rep-table-dialogue-popover, .rep-canvas-timeline-dock'
      )
    ) {
      return;
    }
    panStartRef.current = { x: e.clientX, y: e.clientY, tx: view.x, ty: view.y };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* ignore synthetic pointer */
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = panStartRef.current;
    if (!st) return;
    setView((prev) => ({
      ...prev,
      x: Math.max(-PAN_LIMIT_X, Math.min(PAN_LIMIT_X, st.tx + (e.clientX - st.x))),
      y: Math.max(-PAN_LIMIT_Y, Math.min(PAN_LIMIT_Y, st.ty + (e.clientY - st.y))),
    }));
  };

  const handlePointerUp = () => {
    panStartRef.current = null;
  };

  const commitPlacedCardRemoval = useCallback(
    (cardId: string, label: string) => {
      if (!activeSnapKey) return;
      updateSpatialBySnapshot((prev) => {
        const cur = prev[activeSnapKey] || emptySnapshotSpatialRecord();
        if (!cur.placedCards[cardId]) return prev;
        const prevState = cloneSnapshotSpatialState(cur);
        const nextPlaced = { ...cur.placedCards };
        delete nextPlaced[cardId];
        return {
          ...prev,
          [activeSnapKey]: {
            ...cur,
            placedCards: nextPlaced,
            dragHistory: [
              ...cur.dragHistory,
              {
                id: `dh_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                targetKey: cardId,
                targetKind: 'placed_card',
                label: `Unpinned ${label}`,
                timestamp: Date.now(),
                prevState,
              },
            ],
          },
        };
      });
      setExpandedPlacedCardId((prev) => (prev === cardId ? null : prev));
    },
    [activeSnapKey, updateSpatialBySnapshot]
  );

  const placeCardOnCanvas = useCallback(
    (
      kind: 'table' | 'briefing',
      targetId: string,
      label: string,
      hoverWorld?: { x: number; y: number } | null
    ): PlacedCanvasCard | null => {
      if (!activeSnapKey) return null;
      const cardId = `${kind}:${targetId}`;
      const curRec = spatialBySnapshot[activeSnapKey] || emptySnapshotSpatialRecord();
      if (curRec.placedCards[cardId]) {
        return curRec.placedCards[cardId];
      }

      const clusters: { cx: number; cy: number; outerRx: number; outerRy: number }[] = placements.map((pl) => {
        let outerRx = pl.rx + 42;
        let outerRy = pl.ry + 42;
        for (const pos of Object.values(pl.seatsByPersona)) {
          outerRx = Math.max(outerRx, Math.abs(pos.x - pl.cx) + 64);
          outerRy = Math.max(outerRy, Math.abs(pos.y - pl.cy) + 74);
        }
        return { cx: pl.cx, cy: pl.cy, outerRx, outerRy };
      });
      if (effectiveBriefingCluster) {
        clusters.push({
          cx: effectiveBriefingCluster.cx,
          cy: effectiveBriefingCluster.cy,
          outerRx: effectiveBriefingCluster.outerRx,
          outerRy: effectiveBriefingCluster.outerRy,
        });
      }

      let anchorCx = 620;
      let anchorCy = 300;
      let outerRx = 160;
      let outerRy = 130;
      if (kind === 'table') {
        const pl = placementByCollabId.get(targetId);
        if (pl) {
          anchorCx = pl.cx;
          anchorCy = pl.cy;
          for (const pos of Object.values(pl.seatsByPersona)) {
            outerRx = Math.max(outerRx, Math.abs(pos.x - pl.cx) + 64);
            outerRy = Math.max(outerRy, Math.abs(pos.y - pl.cy) + 74);
          }
        }
      } else if (effectiveBriefingCluster) {
        anchorCx = effectiveBriefingCluster.cx;
        anchorCy = effectiveBriefingCluster.cy;
        outerRx = effectiveBriefingCluster.outerRx;
        outerRy = effectiveBriefingCluster.outerRy;
      }

      const initialPos = computeInitialPlacedCardWorldPos({
        anchorCx,
        anchorCy,
        outerRx,
        outerRy,
        hoverWorld,
        clusters,
        existingCards: resolvedPlacedCards,
        cardExtents: placedCardDims[cardId] || DEFAULT_CARD_EXTENTS,
      });

      const newCard: PlacedCanvasCard = {
        cardId,
        kind,
        targetId,
        x: initialPos.x,
        y: initialPos.y,
        width: DEFAULT_CARD_SIZE.width,
        height: DEFAULT_CARD_SIZE.height,
      };

      updateSpatialBySnapshot((prev) => {
        const cur = prev[activeSnapKey] || emptySnapshotSpatialRecord();
        const prevState = cloneSnapshotSpatialState(cur);
        return {
          ...prev,
          [activeSnapKey]: {
            ...cur,
            placedCards: {
              ...cur.placedCards,
              [cardId]: newCard,
            },
            dragHistory: [
              ...cur.dragHistory,
              {
                id: `dh_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                targetKey: cardId,
                targetKind: 'placed_card',
                label: `Placed ${label}`,
                timestamp: Date.now(),
                prevState,
              },
            ],
          },
        };
      });

      return newCard;
    },
    [
      activeSnapKey,
      spatialBySnapshot,
      placements,
      effectiveBriefingCluster,
      placementByCollabId,
      resolvedPlacedCards,
      placedCardDims,
      updateSpatialBySnapshot,
    ]
  );

  useEffect(() => {
    const onWinPointerMove = (e: PointerEvent) => {
      const drag = activeDragRef.current;
      if (!drag || e.pointerId !== drag.pointerId) return;
      const distClient = Math.hypot(e.clientX - drag.startClientX, e.clientY - drag.startClientY);
      if (!drag.moved && distClient < 5) return;
      drag.moved = true;
      suppressClickRef.current = true;

      const stageEl = worldStageRef.current;
      const rect = stageEl?.getBoundingClientRect();
      const scaleX = rect && rect.width > 0 ? 1000 / rect.width : 1;
      const scaleY = rect && rect.height > 0 ? 600 / rect.height : 1;
      const wdx = (e.clientX - drag.startClientX) * scaleX;
      const wdy = (e.clientY - drag.startClientY) * scaleY;

      const nextState = cloneSnapshotSpatialState(drag.initialState);
      if (drag.kind === 'single_agent') {
        nextState.singleAgentCoords[drag.targetKey] = {
          x: drag.baseX + wdx,
          y: drag.baseY + wdy,
        };
      } else if (drag.kind === 'agent_cluster') {
        nextState.clusterOffsets[drag.targetKey] = {
          dx: drag.baseDx + wdx,
          dy: drag.baseDy + wdy,
        };
      } else if (drag.kind === 'placed_card') {
        const curCard = nextState.placedCards[drag.targetKey];
        if (curCard) {
          nextState.placedCards[drag.targetKey] = {
            ...curCard,
            x: drag.baseX + wdx,
            y: drag.baseY + wdy,
          };
        }
      } else if (drag.kind === 'placed_card_resize') {
        const curCard = nextState.placedCards[drag.targetKey];
        if (curCard) {
          const rawW =
            drag.resizeDir === 'sw'
              ? drag.startW - wdx
              : drag.resizeDir === 's'
                ? drag.startW
                : drag.startW + wdx;
          const rawH = drag.resizeDir === 'e' ? drag.startH : drag.startH + wdy;
          const nextW = Math.round(Math.max(MIN_CARD_SIZE.width, Math.min(MAX_CARD_SIZE.width, rawW)));
          const nextH = Math.round(Math.max(MIN_CARD_SIZE.height, Math.min(MAX_CARD_SIZE.height, rawH)));
          const dw = nextW - drag.startW;
          const dh = nextH - drag.startH;
          const dxCenter = drag.resizeDir === 'sw' ? -dw / 2 : drag.resizeDir === 's' ? 0 : dw / 2;
          const dyCenter = drag.resizeDir === 'e' ? 0 : dh / 2;
          nextState.placedCards[drag.targetKey] = {
            ...curCard,
            x: drag.baseX + dxCenter,
            y: drag.baseY + dyCenter,
            width: nextW,
            height: nextH,
          };
        }
      }
      drag.lastPreview = nextState;
      setDragSession({
        activeKey: drag.activeKey,
        kind: drag.kind,
        targetKey: drag.targetKey,
        previewState: nextState,
      });
    };

    const onWinPointerUp = (e: PointerEvent) => {
      const drag = activeDragRef.current;
      if (!drag || e.pointerId !== drag.pointerId) return;
      activeDragRef.current = null;
      setDragSession(null);

      if (drag.moved) {
        window.setTimeout(() => {
          suppressClickRef.current = false;
        }, 0);
        // Bake settled positions (resolved coordinates after any collisions) + append drag history
        const settledState = cloneSnapshotSpatialState(drag.lastPreview);
        for (const [pid, pos] of Object.entries(liveWorldCoordsRef.current)) {
          if (settledState.singleAgentCoords[pid]) {
            settledState.singleAgentCoords[pid] = { x: pos.x, y: pos.y };
          }
        }
        for (const rc of liveResolvedCardsRef.current) {
          if (settledState.placedCards[rc.cardId]) {
            settledState.placedCards[rc.cardId] = {
              ...settledState.placedCards[rc.cardId],
              x: rc.cx,
              y: rc.cy,
              width: rc.width,
              height: rc.height,
            };
          }
        }
        settledState.clusterOffsets = { ...liveClusterOffsetsRef.current };
        const snapKey = drag.snapKey;
        const historyKind = drag.kind === 'placed_card_resize' ? 'placed_card' : drag.kind;
        const verb = drag.kind === 'placed_card_resize' ? 'Resized' : 'Moved';
        updateSpatialBySnapshot((prev) => {
          const cur = prev[snapKey] || emptySnapshotSpatialRecord();
          return {
            ...prev,
            [snapKey]: {
              ...settledState,
              dragHistory: [
                ...cur.dragHistory,
                {
                  id: `dh_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                  targetKey: drag.targetKey,
                  targetKind: historyKind,
                  label: `${verb} ${drag.label}`,
                  timestamp: Date.now(),
                  prevState: drag.initialState,
                },
              ],
            },
          };
        });
      } else {
        suppressClickRef.current = false;
      }
    };

    window.addEventListener('pointermove', onWinPointerMove);
    window.addEventListener('pointerup', onWinPointerUp);
    window.addEventListener('pointercancel', onWinPointerUp);
    return () => {
      window.removeEventListener('pointermove', onWinPointerMove);
      window.removeEventListener('pointerup', onWinPointerUp);
      window.removeEventListener('pointercancel', onWinPointerUp);
    };
  }, [updateSpatialBySnapshot]);

  const startDragPersonaOrCluster = useCallback(
    (persona: Persona, e: React.PointerEvent<HTMLDivElement>) => {
      if (!isHistoricalReplay || !activeSnapKey || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();

      const initialState = cloneSnapshotSpatialState(activeSpatialState);
      const clusterKey = clusterKeyByPersona[persona.persona_id];
      if (clusterKey) {
        const curOff = initialState.clusterOffsets[clusterKey] || { dx: 0, dy: 0 };
        activeDragRef.current = {
          pointerId: e.pointerId,
          kind: 'agent_cluster',
          targetKey: clusterKey,
          activeKey: `cluster:${clusterKey}`,
          label: clusterKey === 'briefing' ? 'Briefing Group' : `${persona.label} Group`,
          snapKey: activeSnapKey,
          startClientX: e.clientX,
          startClientY: e.clientY,
          baseX: 0,
          baseY: 0,
          baseDx: curOff.dx,
          baseDy: curOff.dy,
          startW: 0,
          startH: 0,
          initialState,
          moved: false,
          lastPreview: initialState,
        };
      } else {
        const curCoord = worldCoordsByPersona[persona.persona_id] || { x: 560, y: 300 };
        initialState.singleAgentCoords[persona.persona_id] = { x: curCoord.x, y: curCoord.y };
        activeDragRef.current = {
          pointerId: e.pointerId,
          kind: 'single_agent',
          targetKey: persona.persona_id,
          activeKey: `single:${persona.persona_id}`,
          label: persona.label,
          snapKey: activeSnapKey,
          startClientX: e.clientX,
          startClientY: e.clientY,
          baseX: curCoord.x,
          baseY: curCoord.y,
          baseDx: 0,
          baseDy: 0,
          startW: 0,
          startH: 0,
          initialState,
          moved: false,
          lastPreview: initialState,
        };
      }
    },
    [isHistoricalReplay, activeSnapKey, activeSpatialState, clusterKeyByPersona, worldCoordsByPersona]
  );

  const startDragClusterTarget = useCallback(
    (clusterKey: string, label: string, e: React.PointerEvent<SVGGElement>) => {
      if (!isHistoricalReplay || !activeSnapKey || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const initialState = cloneSnapshotSpatialState(activeSpatialState);
      const curOff = initialState.clusterOffsets[clusterKey] || { dx: 0, dy: 0 };
      activeDragRef.current = {
        pointerId: e.pointerId,
        kind: 'agent_cluster',
        targetKey: clusterKey,
        activeKey: `cluster:${clusterKey}`,
        label,
        snapKey: activeSnapKey,
        startClientX: e.clientX,
        startClientY: e.clientY,
        baseX: 0,
        baseY: 0,
        baseDx: curOff.dx,
        baseDy: curOff.dy,
        startW: 0,
        startH: 0,
        initialState,
        moved: false,
        lastPreview: initialState,
      };
    },
    [isHistoricalReplay, activeSnapKey, activeSpatialState]
  );

  const startDragPlacedCard = useCallback(
    (card: ResolvedPlacedCard, label: string, e: React.PointerEvent) => {
      if (!isHistoricalReplay || !activeSnapKey || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const initialState = cloneSnapshotSpatialState(activeSpatialState);
      initialState.placedCards[card.cardId] = {
        cardId: card.cardId,
        kind: card.kind,
        targetId: card.targetId,
        x: card.cx,
        y: card.cy,
        width: card.width,
        height: card.height,
      };
      activeDragRef.current = {
        pointerId: e.pointerId,
        kind: 'placed_card',
        targetKey: card.cardId,
        activeKey: `card:${card.cardId}`,
        label,
        snapKey: activeSnapKey,
        startClientX: e.clientX,
        startClientY: e.clientY,
        baseX: card.cx,
        baseY: card.cy,
        baseDx: 0,
        baseDy: 0,
        startW: card.width,
        startH: card.height,
        initialState,
        moved: false,
        lastPreview: initialState,
      };
    },
    [isHistoricalReplay, activeSnapKey, activeSpatialState]
  );

  const startResizePlacedCard = useCallback(
    (
      card: ResolvedPlacedCard,
      resizeDir: 'e' | 's' | 'se' | 'sw',
      label: string,
      e: React.PointerEvent
    ) => {
      if (!isHistoricalReplay || !activeSnapKey || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const initialState = cloneSnapshotSpatialState(activeSpatialState);
      initialState.placedCards[card.cardId] = {
        cardId: card.cardId,
        kind: card.kind,
        targetId: card.targetId,
        x: card.cx,
        y: card.cy,
        width: card.width,
        height: card.height,
      };
      activeDragRef.current = {
        pointerId: e.pointerId,
        kind: 'placed_card_resize',
        targetKey: card.cardId,
        activeKey: `card:${card.cardId}`,
        label,
        snapKey: activeSnapKey,
        startClientX: e.clientX,
        startClientY: e.clientY,
        baseX: card.cx,
        baseY: card.cy,
        baseDx: 0,
        baseDy: 0,
        startW: card.width,
        startH: card.height,
        resizeDir,
        initialState,
        moved: false,
        lastPreview: initialState,
      };
    },
    [isHistoricalReplay, activeSnapKey, activeSpatialState]
  );

  const handleUndoSnapshotDrag = useCallback(() => {
    if (!activeSnapKey) return;
    updateSpatialBySnapshot((prev) => {
      const cur = prev[activeSnapKey];
      if (!cur || cur.dragHistory.length === 0) return prev;
      const last = cur.dragHistory[cur.dragHistory.length - 1];
      return {
        ...prev,
        [activeSnapKey]: {
          ...cloneSnapshotSpatialState(last.prevState),
          dragHistory: cur.dragHistory.slice(0, -1),
        },
      };
    });
  }, [activeSnapKey, updateSpatialBySnapshot]);

  const handleResetSnapshotLayout = useCallback(() => {
    if (!activeSnapKey) return;
    setExpandedPlacedCardId(null);
    updateSpatialBySnapshot((prev) => {
      const next = { ...prev };
      delete next[activeSnapKey];
      return next;
    });
  }, [activeSnapKey, updateSpatialBySnapshot]);

  const handleZoomPillPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const targetEl = e.target as HTMLElement | null;
    if (targetEl?.closest('button[data-hud-action="true"]')) return;
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();

    const rect = rootRef.current?.getBoundingClientRect();
    const centerX = rect ? rect.width / 2 : 480;
    const centerY = rect ? rect.height / 2 : 320;
    zoomScrubRef.current = {
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startScale: view.scale,
      startX: view.x,
      startY: view.y,
      centerX,
      centerY,
    };
    setIsZoomScrubbing(true);
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  const handleZoomPillPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = zoomScrubRef.current;
    if (!st || st.pointerId !== e.pointerId) return;
    const dx = e.clientX - st.startClientX;
    const sensitivity = e.shiftKey ? 0.0018 : 0.0055;
    applyZoomCentered(st.startScale * Math.exp(dx * sensitivity), {
      scale: st.startScale,
      x: st.startX,
      y: st.startY,
      centerX: st.centerX,
      centerY: st.centerY,
    });
  };

  const finishZoomScrub = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = zoomScrubRef.current;
    if (!st || st.pointerId !== e.pointerId) return;
    zoomScrubRef.current = null;
    setIsZoomScrubbing(false);
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {
      /* ignore */
    }
    setZoomAnnouncement(`Canvas zoom ${Math.round(view.scale * 100)}%`);
  };

  const handleZoomKeyDown = (e: React.KeyboardEvent<HTMLSpanElement>) => {
    let targetScale: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') targetScale = view.scale + 0.05;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') targetScale = view.scale - 0.05;
    else if (e.key === 'Home') targetScale = MIN_SCALE;
    else if (e.key === 'End') targetScale = MAX_SCALE;
    else if (e.key === '0') targetScale = 1.0;

    if (targetScale !== null) {
      e.preventDefault();
      e.stopPropagation();
      const applied = applyZoomCentered(targetScale);
      setZoomAnnouncement(`Canvas zoom ${Math.round(applied * 100)}%`);
    }
  };

  useEffect(() => {
    return () => {
      if (hoverCloseTimerRef.current !== null) {
        window.clearTimeout(hoverCloseTimerRef.current);
      }
      if (briefingCloseTimerRef.current !== null) {
        window.clearTimeout(briefingCloseTimerRef.current);
      }
      if (interventionCloseTimerRef.current !== null) {
        window.clearTimeout(interventionCloseTimerRef.current);
      }
    };
  }, []);

  const clearInterventionCloseTimer = useCallback(() => {
    if (interventionCloseTimerRef.current !== null) {
      window.clearTimeout(interventionCloseTimerRef.current);
      interventionCloseTimerRef.current = null;
    }
  }, []);

  const scheduleInterventionClose = useCallback(() => {
    clearInterventionCloseTimer();
    interventionCloseTimerRef.current = window.setTimeout(() => {
      setHoveredIntervention(null);
      interventionCloseTimerRef.current = null;
    }, 220);
  }, [clearInterventionCloseTimer]);

  const handleMailPointerEnter = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      clearInterventionCloseTimer();
      const rootRect = rootRef.current?.getBoundingClientRect();
      if (!rootRect) return;
      const elRect = e.currentTarget.getBoundingClientRect();
      const popWidth = 328;
      const popHeight = 250;
      const rawX = elRect.left - rootRect.left + elRect.width / 2 - popWidth / 2;
      const rawYAbove = elRect.top - rootRect.top - popHeight - 8;
      const rawY =
        rawYAbove >= 54
          ? rawYAbove
          : Math.min(rootRect.height - popHeight - 76, elRect.bottom - rootRect.top + 8);
      setHoveredIntervention({
        x: Math.max(14, Math.min(rootRect.width - popWidth - 14, rawX)),
        y: Math.max(52, Math.min(rootRect.height - popHeight - 72, rawY)),
      });
    },
    [clearInterventionCloseTimer]
  );

  const clearBriefingCloseTimer = useCallback(() => {
    if (briefingCloseTimerRef.current !== null) {
      window.clearTimeout(briefingCloseTimerRef.current);
      briefingCloseTimerRef.current = null;
    }
  }, []);

  const scheduleBriefingClose = useCallback(() => {
    if (expandedPopover === 'briefing') return;
    clearBriefingCloseTimer();
    briefingCloseTimerRef.current = window.setTimeout(() => {
      setHoveredBriefing(null);
      briefingCloseTimerRef.current = null;
    }, 220);
  }, [clearBriefingCloseTimer, expandedPopover]);

  const openBriefingPopover = useCallback(
    (anchorEl?: Element | null) => {
      if (!isHistoricalReplay) return;
      if (Object.values(activeSpatialState.placedCards).some((c) => c.kind === 'briefing')) return;
      clearBriefingCloseTimer();
      const rootEl = rootRef.current;
      const rootRect = rootEl?.getBoundingClientRect();
      if (!rootEl || !rootRect) return;
      const targetEl =
        rootEl.querySelector('[data-testid="briefing-easel"]') || anchorEl || null;
      const elRect = targetEl?.getBoundingClientRect();
      if (!elRect) return;
      const popWidth = Math.min(460, Math.max(280, rootRect.width - 32));
      const popHeight = Math.min(360, Math.max(220, rootRect.height - 132));
      const rawX = elRect.left - rootRect.left + elRect.width / 2 - popWidth / 2;
      const rawYAbove = elRect.top - rootRect.top - popHeight - 8;
      const rawY =
        rawYAbove >= 54
          ? rawYAbove
          : Math.min(rootRect.height - popHeight - 76, elRect.bottom - rootRect.top + 8);
      setExpandedPopover((prev) => (prev === 'briefing' ? prev : null));
      setHoveredBriefing((prev) =>
        prev ?? {
          x: Math.max(14, Math.min(rootRect.width - popWidth - 14, rawX)),
          y: Math.max(52, Math.min(rootRect.height - popHeight - 72, rawY)),
        }
      );
    },
    [clearBriefingCloseTimer, isHistoricalReplay, activeSpatialState.placedCards]
  );

  const handleEaselPointerEnter = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      openBriefingPopover(e.currentTarget);
    },
    [openBriefingPopover]
  );

  const clearHoverCloseTimer = useCallback(() => {
    if (hoverCloseTimerRef.current !== null) {
      window.clearTimeout(hoverCloseTimerRef.current);
      hoverCloseTimerRef.current = null;
    }
  }, []);

  const scheduleHoverClose = useCallback(() => {
    if (expandedPopover === 'table') return;
    clearHoverCloseTimer();
    hoverCloseTimerRef.current = window.setTimeout(() => {
      setHoveredTable(null);
      hoverCloseTimerRef.current = null;
    }, 220);
  }, [clearHoverCloseTimer, expandedPopover]);

  const openTablePopoverForCollab = useCallback(
    (collabId: string, anchorEl?: Element | null) => {
      if (!isHistoricalReplay) return;
      if (activeSpatialState.placedCards[`table:${collabId}`]) return;
      clearHoverCloseTimer();
      const rootEl = rootRef.current;
      const rootRect = rootEl?.getBoundingClientRect();
      if (!rootEl || !rootRect) return;
      const targetEl =
        rootEl.querySelector(`[data-testid="research-table-${collabId}"]`) || anchorEl || null;
      const tblRect = targetEl?.getBoundingClientRect();
      if (!tblRect) return;
      const popWidth = Math.min(480, Math.max(300, rootRect.width - 32));
      const popHeight = Math.min(400, Math.max(240, rootRect.height - 136));
      const rawX = tblRect.left - rootRect.left + tblRect.width / 2 - popWidth / 2;
      const rawYAbove = tblRect.top - rootRect.top - popHeight - 10;
      const rawY =
        rawYAbove >= 54
          ? rawYAbove
          : Math.min(rootRect.height - popHeight - 98, tblRect.bottom - rootRect.top + 8);
      const clampedX = Math.max(14, Math.min(rootRect.width - popWidth - 14, rawX));
      const clampedY = Math.max(52, Math.min(rootRect.height - popHeight - 98, rawY));
      setExpandedPopover((prev) => (prev === 'table' ? prev : null));
      setHoveredTable((prev) => (prev && prev.collabId === collabId ? prev : { collabId, x: clampedX, y: clampedY }));
    },
    [clearHoverCloseTimer, isHistoricalReplay, activeSpatialState.placedCards]
  );

  const handleTablePointerEnter = useCallback(
    (table: ActiveTableVisual, _placement: TablePlacement, e: React.PointerEvent<SVGGElement>) => {
      openTablePopoverForCollab(table.collaboration_id, e.currentTarget);
    },
    [openTablePopoverForCollab]
  );

  const resolveTableDialogue = useCallback(
    (tbl: ActiveTableVisual): TableDialogueRecord =>
      tableDialogues[tbl.collaboration_id] || {
        collaboration_id: tbl.collaboration_id,
        kind: tbl.kind,
        topic: tbl.topic,
        participants: tbl.participant_persona_ids,
        start_reason: tbl.topic || 'Specialists convened to cross-check findings.',
        end_report: '',
        turns: [],
      },
    [tableDialogues]
  );

  const activeHoveredTable = hoveredTable
    ? tables.find((t) => t.collaboration_id === hoveredTable.collabId) || null
    : null;
  const activeDialogue =
    hoveredTable && activeHoveredTable ? resolveTableDialogue(activeHoveredTable) : null;

  const histogramBars = useMemo(() => {
    if (timelineSnapshots.length === 0) return [];
    const rawDurations = timelineSnapshots.map((snap, idx) => {
      if (typeof snap.duration_sec === 'number' && snap.duration_sec > 0) {
        return snap.duration_sec;
      }
      const nextSnap = timelineSnapshots[idx + 1];
      const seqSpan = nextSnap ? Math.max(1, nextSnap.seq - snap.seq) : 2;
      return snap.kind === 'run_completed' ? 1.5 : seqSpan * 1.4;
    });
    const sqrts = rawDurations.map((d) => Math.sqrt(Math.max(0.25, d)));
    const minSqrt = Math.min(...sqrts);
    const maxSqrt = Math.max(...sqrts);
    return timelineSnapshots.map((snap, idx) => {
      const durSec = rawDurations[idx];
      const norm = maxSqrt > minSqrt ? (sqrts[idx] - minSqrt) / (maxSqrt - minSqrt) : 0.5;
      const heightPx = Math.round(12 + norm * 26);
      const stageMeta = getSnapshotStageMeta(snap);
      return { snap, durSec, heightPx, stageMeta };
    });
  }, [timelineSnapshots]);

  const inspectedBar =
    histogramBars.find((b) => b.snap.snapshot_id === hoveredSnapId) ||
    (activeSnap ? histogramBars.find((b) => b.snap.snapshot_id === activeSnap.snapshot_id) : null) ||
    null;

  const activeBriefing = useMemo<BriefingRecord | null>(() => {
    if (reportingPersonas.length === 0) return null;
    if (activeSnap?.briefing_id && briefings[activeSnap.briefing_id]) {
      return briefings[activeSnap.briefing_id];
    }
    const reporterIds = reportingPersonas.map((p) => p.persona_id);
    const list = Object.values(briefings);
    if (replaySeq !== null) {
      const bySeq = list.find((b) => Math.abs(b.seq - replaySeq) <= 2);
      if (bySeq) return bySeq;
    }
    const byPresenter = [...list]
      .reverse()
      .find((b) => b.presenter_ids.some((pid) => reporterIds.includes(pid)));
    if (byPresenter) return byPresenter;
    return {
      briefing_id: 'brief_live',
      seq: replaySeq ?? 0,
      presenter_ids: reporterIds,
      presenter_labels: reportingPersonas.map((p) => p.label),
      topic: 'Subagent Presentation to Boss',
      summary: `${reportingPersonas.map((p) => p.label).join(' & ')} presenting experimental findings and next steps to Boss Agent.`,
      artifact_ids: [],
      linked_step_ids: [],
    };
  }, [reportingPersonas, activeSnap, briefings, replaySeq]);

  useEffect(() => {
    if (reportingPersonas.length === 0 && hoveredBriefing) {
      setHoveredBriefing(null);
    }
  }, [reportingPersonas.length, hoveredBriefing]);

  const visibleIntervention = useMemo<HumanInterventionRecord | null>(() => {
    if (activeSnap?.kind === 'intervention' && activeSnap.intervention_id && interventions[activeSnap.intervention_id]) {
      return interventions[activeSnap.intervention_id];
    }
    if (isHistoricalReplay && replaySeq !== null) {
      const matched = Object.values(interventions).find(
        (rec) => rec.seq === replaySeq || (rec.resolved_seq !== null && rec.resolved_seq === replaySeq)
      );
      if (matched) return matched;
    }
    return activeIntervention || null;
  }, [activeSnap, interventions, isHistoricalReplay, replaySeq, activeIntervention]);

  useEffect(() => {
    if (!visibleIntervention && hoveredIntervention) {
      setHoveredIntervention(null);
    }
  }, [visibleIntervention, hoveredIntervention]);

  const mailOrigin = useMemo(() => {
    if (!visibleIntervention) return { x: 640, y: 310 };
    if (visibleIntervention.collaboration_id) {
      const pl = placementByCollabId.get(visibleIntervention.collaboration_id);
      if (pl) return { x: pl.cx, y: pl.cy };
    }
    if (placements.length > 0) {
      return { x: placements[0].cx, y: placements[0].cy };
    }
    const coords = visibleIntervention.from_persona_ids
      .map((id) => worldCoordsByPersona[id])
      .filter((c): c is { x: number; y: number } => Boolean(c));
    if (coords.length > 0) {
      return {
        x: coords.reduce((acc, c) => acc + c.x, 0) / coords.length,
        y: coords.reduce((acc, c) => acc + c.y, 0) / coords.length,
      };
    }
    return { x: 640, y: 310 };
  }, [visibleIntervention, placementByCollabId, placements, worldCoordsByPersona]);

  const mainCoord = worldCoordsByPersona['persona_main'] || { x: 165, y: 294 };

  // Agent-to-agent attention model: who looks at whom, who leans in, who nods.
  const interactionByPersona = useMemo(() => {
    const map: Record<string, { role: PersonaInteractionRole; targets: WorldPoint[] }> = {};
    const coordOf = (pid: string): WorldPoint | null => worldCoordsByPersona[pid] || null;

    for (const pl of placements) {
      const tbl = tables.find((t) => t.collaboration_id === pl.collaborationId);
      const members = Object.keys(pl.seatsByPersona).filter((pid) => pid !== 'persona_main');
      const center = { x: pl.cx, y: pl.cy };
      const speaker = tbl?.speakingPersonaId && members.includes(tbl.speakingPersonaId) ? tbl.speakingPersonaId : null;
      const speakerCoord = speaker ? coordOf(speaker) : null;
      for (const pid of members) {
        const others = members
          .filter((o) => o !== pid)
          .map(coordOf)
          .filter((c): c is WorldPoint => Boolean(c));
        if (speaker && pid === speaker) {
          map[pid] = { role: 'speaker', targets: others.length > 0 ? others : [center] };
        } else if (speakerCoord) {
          map[pid] = { role: 'listener', targets: [speakerCoord, center] };
        } else {
          map[pid] = { role: 'seated', targets: [center, ...others] };
        }
      }
    }

    const briefingTarget: WorldPoint = effectiveBriefingCluster
      ? { x: effectiveBriefingCluster.easelCx, y: effectiveBriefingCluster.easelCy - 14 }
      : mainCoord;
    for (const rp of reportingPersonas) {
      map[rp.persona_id] = { role: 'presenter', targets: [briefingTarget, mainCoord] };
    }

    if (reportingPersonas.length > 0) {
      const presenterCoords = reportingPersonas
        .map((rp) => coordOf(rp.persona_id))
        .filter((c): c is WorldPoint => Boolean(c));
      if (presenterCoords.length > 0) map['persona_main'] = { role: 'audience', targets: presenterCoords };
    } else if (visibleIntervention) {
      map['persona_main'] = { role: null, targets: [mailOrigin] };
    } else if (placements.length > 0) {
      map['persona_main'] = { role: null, targets: placements.map((pl) => ({ x: pl.cx, y: pl.cy })) };
    }
    return map;
  }, [
    placements,
    tables,
    worldCoordsByPersona,
    reportingPersonas,
    effectiveBriefingCluster,
    mainCoord,
    visibleIntervention,
    mailOrigin,
  ]);

  const speechFlows = useMemo<SpeechFlow[]>(() => {
    const flows: SpeechFlow[] = [];
    for (const pl of placements) {
      const tbl = tables.find((t) => t.collaboration_id === pl.collaborationId);
      const sp = tbl?.speakingPersonaId;
      if (!sp || !(sp in pl.seatsByPersona)) continue;
      const from = worldCoordsByPersona[sp];
      if (!from) continue;
      flows.push({ id: pl.collaborationId, from, center: { x: pl.cx, y: pl.cy }, rx: pl.rx, ry: pl.ry });
    }
    return flows;
  }, [placements, tables, worldCoordsByPersona]);

  const handoffFlows = useMemo<HandoffFlow[]>(() => {
    const to: WorldPoint = effectiveBriefingCluster
      ? { x: effectiveBriefingCluster.easelCx, y: effectiveBriefingCluster.easelCy - 14 }
      : mainCoord;
    return reportingPersonas
      .map((rp) => {
        const from = worldCoordsByPersona[rp.persona_id];
        return from ? { id: rp.persona_id, from, to } : null;
      })
      .filter((f): f is HandoffFlow => Boolean(f));
  }, [reportingPersonas, worldCoordsByPersona, effectiveBriefingCluster, mainCoord]);

  const renderGroupChatTurns = (turns: TableDialogueTurn[], emptyText: string, compact = false) => {
    if (turns.length === 0) {
      return <div className="rep-tdp-empty">{emptyText}</div>;
    }
    return turns.map((turn, idx) => {
      const style = getAgentBubbleStyle(turn.speaker_id, turn.speaker_label);
      const side = style.isMain ? 'right' : 'left';
      const speakerShort = compact ? turn.speaker_label.replace(/\s*\([^)]*\)\s*$/, '') : turn.speaker_label;
      // Compact view: procedural membership / bookkeeping turns collapse to a single quiet event line
      if (compact && PROCEDURAL_STANCES.has((turn.stance || '').toLowerCase())) {
        return (
          <div
            key={`${turn.seq}_${idx}`}
            className="rep-gchat-event"
            data-speaker-id={turn.speaker_id}
            data-stance={turn.stance}
          >
            <span className="rep-gchat-dot" style={{ backgroundColor: style.dot }} aria-hidden="true" />
            <span className="rep-gchat-event-speaker">{speakerShort}</span>
            <span className="rep-gchat-event-text">{toCompactLine(turn.summary, 64)}</span>
          </div>
        );
      }
      return (
        <div
          key={`${turn.seq}_${idx}`}
          className={`rep-gchat-row ${style.isMain ? 'is-main' : 'is-subagent'}`}
          data-speaker-id={turn.speaker_id}
          data-bubble-side={side}
          data-instance-index={style.instanceIndex}
        >
          <div className="rep-gchat-meta">
            <span
              className="rep-gchat-dot"
              style={{ backgroundColor: style.dot }}
              aria-hidden="true"
            />
            <span className="rep-gchat-speaker">{speakerShort}</span>
            {turn.stance && <span className="rep-gchat-stance">{turn.stance.replace(/_/g, ' ')}</span>}
          </div>
          <div
            className={`rep-gchat-bubble ${compact ? 'is-compact' : 'markdown-body'}`}
            style={{ backgroundColor: style.bg, borderColor: style.border }}
          >
            {compact ? toCompactLine(turn.summary, 120) : <MarkdownMath>{turn.summary}</MarkdownMath>}
          </div>
        </div>
      );
    });
  };

  const gridSize = 22 * view.scale;
  const expandedPlacedCard =
    isHistoricalReplay && expandedPlacedCardId
      ? resolvedPlacedCards.find((c) => c.cardId === expandedPlacedCardId) || null
      : null;

  const renderPlacedCardElement = (card: ResolvedPlacedCard, asExpandedOverlay: boolean) => {
    const isTable = card.kind === 'table';
    const tbl = isTable ? tables.find((t) => t.collaboration_id === card.targetId) || null : null;
    if (isTable && !tbl) return null;
    if (!isTable && !activeBriefing) return null;

    const dlg = isTable && tbl ? resolveTableDialogue(tbl) : null;
    const title = isTable && dlg
      ? shortPopoverTitle(dlg.topic) || 'Team Discussion'
      : shortPopoverTitle(activeBriefing?.topic) || 'Briefing';
    const isDraggingThis =
      (dragSession?.kind === 'placed_card' || dragSession?.kind === 'placed_card_resize') &&
      dragSession.targetKey === card.cardId;
    const useFullDetail = asExpandedOverlay || card.width >= 460 || card.height >= 380;

    return (
      <div
        key={asExpandedOverlay ? `expanded-${card.cardId}` : `placed-${card.cardId}`}
        ref={
          asExpandedOverlay
            ? undefined
            : (el) => {
                placedCardElsRef.current[card.cardId] = el;
              }
        }
        className={`rep-table-dialogue-popover ${
          asExpandedOverlay ? 'is-expanded' : 'is-placed'
        } ${!isTable ? 'rep-briefing-popover' : ''} ${isDraggingThis ? 'is-dragging' : ''}`}
        data-testid={
          asExpandedOverlay
            ? `expanded-placed-card-${card.targetId}`
            : isTable
              ? `placed-dialogue-card-${card.targetId}`
              : `placed-briefing-card-${card.targetId}`
        }
        data-card-id={card.cardId}
        data-world-x={Math.round(card.cx)}
        data-world-y={Math.round(card.cy)}
        data-world-w={Math.round(card.width)}
        data-world-h={Math.round(card.height)}
        role="region"
        aria-label={`Placed ${isTable ? 'table dialogue' : 'briefing'}: ${title}`}
        style={
          asExpandedOverlay
            ? undefined
            : {
                left: `${((card.cx / 1000) * 100).toFixed(3)}%`,
                top: `${((card.cy / 600) * 100).toFixed(3)}%`,
                width: `${((card.width / 1000) * 100).toFixed(3)}%`,
                height: `${((card.height / 600) * 100).toFixed(3)}%`,
              }
        }
        onPointerDown={(e) => e.stopPropagation()}
      >
        <div
          className="rep-tdp-header"
          onPointerDown={
            asExpandedOverlay
              ? undefined
              : (e) => {
                  if ((e.target as HTMLElement).closest('button')) return;
                  startDragPlacedCard(card, title, e);
                }
          }
        >
          <div className="rep-tdp-title-row">
            {isTable ? <MessageSquare size={13} aria-hidden="true" /> : <Presentation size={13} aria-hidden="true" />}
            <span className="rep-tdp-title">{title}</span>
          </div>
          <div className="rep-tdp-header-actions">
            <button
              type="button"
              className="rep-tdp-icon-btn is-active"
              data-icon="pin"
              data-testid={isTable ? `unplace-dialogue-card-btn-${card.targetId}` : 'unplace-briefing-card-btn'}
              aria-label="Unpin window from canvas"
              title="Unpin from canvas (drag header to move)"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                commitPlacedCardRemoval(card.cardId, title);
              }}
            >
              <Pin size={13} className="rep-pin-icon is-pinned" aria-hidden="true" />
            </button>
            <button
              type="button"
              className="rep-tdp-icon-btn"
              data-testid={isTable ? `expand-placed-card-btn-${card.targetId}` : 'expand-placed-briefing-btn'}
              aria-label={asExpandedOverlay ? 'Restore card to canvas' : 'Expand across canvas'}
              title={asExpandedOverlay ? 'Restore' : 'Expand'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedPlacedCardId((prev) => (prev === card.cardId ? null : card.cardId));
              }}
            >
              {asExpandedOverlay ? (
                <Minimize2 size={13} aria-hidden="true" />
              ) : (
                <Maximize2 size={13} aria-hidden="true" />
              )}
            </button>
          </div>
        </div>

        {isTable && dlg && dlg.start_reason && (
          <div className="rep-tdp-reason">
            <strong>Why:</strong>{' '}
            {useFullDetail ? toPlainUiText(dlg.start_reason, 400) : toCompactLine(dlg.start_reason, 72)}
          </div>
        )}

        <div className="rep-gchat-list">
          {isTable && dlg
            ? renderGroupChatTurns(
                dlg.turns,
                'Agents are reviewing evidence around the table',
                !useFullDetail
              )
            : activeBriefing
              ? renderGroupChatTurns(
                  activeBriefing.turns && activeBriefing.turns.length > 0
                    ? activeBriefing.turns
                    : [
                        {
                          seq: activeBriefing.seq,
                          speaker_id: activeBriefing.presenter_ids[0] || 'persona_validation',
                          speaker_label: activeBriefing.presenter_labels.join(' & ') || 'Specialist',
                          stance: 'briefing',
                          summary: activeBriefing.summary,
                        },
                      ],
                  'Preparing whiteboard presentation',
                  !useFullDetail
                )
              : null}
        </div>

        {isTable && dlg && dlg.end_report && (
          <div className="rep-tdp-report">
            <strong>Report:</strong>{' '}
            {useFullDetail ? toPlainUiText(dlg.end_report, 600) : toCompactLine(dlg.end_report, 150)}
          </div>
        )}

        {!asExpandedOverlay && (
          <>
            <div
              className="rep-tdp-resize-edge edge-e"
              aria-hidden="true"
              onPointerDown={(e) => startResizePlacedCard(card, 'e', title, e)}
            />
            <div
              className="rep-tdp-resize-edge edge-s"
              aria-hidden="true"
              onPointerDown={(e) => startResizePlacedCard(card, 's', title, e)}
            />
            <div
              className="rep-tdp-resize-edge corner-sw"
              aria-hidden="true"
              onPointerDown={(e) => startResizePlacedCard(card, 'sw', title, e)}
            />
            <div
              className="rep-tdp-resize-corner corner-se"
              role="separator"
              aria-label="Drag to resize pinned window"
              title="Drag to resize window"
              data-testid={
                isTable ? `resize-placed-card-${card.targetId}` : 'resize-placed-briefing-card'
              }
              onPointerDown={(e) => startResizePlacedCard(card, 'se', title, e)}
            >
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <path d="M9 3L3 9M9 6.5L6.5 9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <section
      ref={rootRef}
      className={`rep-living-canvas ${prefersReduced ? 'is-motion-disabled' : ''} ${
        mode === 'simulation' ? 'is-simulation' : ''
      } ${isHistoricalReplay ? 'is-replay' : ''} ${dragSession ? 'is-dragging-entity' : ''}`}
      data-testid="living-research-canvas"
      data-mode={mode}
      aria-label="Read-only Living Research Canvas"
      style={{
        backgroundPosition: `${view.x}px ${view.y}px`,
        backgroundSize: `${gridSize}px ${gridSize}px`,
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      {/* Top-right read-only status + Data-style horizontal scrub zoom & Fit All */}
      <div className="canvas-hud-top rep-canvas-HUD-bar">
        {mode === 'simulation' && (
          <span className="rep-mode-pill simulation" data-testid="simulation-mode-badge">
            SIMULATION · Non-Live Fixture
          </span>
        )}
        {overflowCollaborations.length > 0 && (
          <span className="rep-mode-pill low-stim" title="Additional collaborations recorded in Chat">
            +{overflowCollaborations.length} quiet activity in Chat
          </span>
        )}

        {isHistoricalReplay && currentSnapRecord.dragHistory.length > 0 && (
          <div className="canvas-hud-pill rep-history-hud-pill">
            <button
              type="button"
              data-hud-action="true"
              data-testid="timeline-undo-drag-btn"
              className="btn-secondary"
              style={{ padding: '3px 8px', fontSize: 11.5 }}
              title={`Undo last canvas edit (${currentSnapRecord.dragHistory[currentSnapRecord.dragHistory.length - 1]?.label || 'edit'})`}
              onClick={handleUndoSnapshotDrag}
            >
              <Undo2 size={12} aria-hidden="true" />
              Undo
            </button>
            <button
              type="button"
              data-hud-action="true"
              data-testid="timeline-reset-layout-btn"
              className="btn-secondary"
              style={{ padding: '3px 8px', fontSize: 11.5 }}
              title="Reset layout and placed windows for this snapshot"
              onClick={handleResetSnapshotLayout}
            >
              <RotateCcw size={12} aria-hidden="true" />
              Reset
            </button>
          </div>
        )}

        {isHistoricalReplay && onReturnToLive && (
          <div className="canvas-hud-pill">
            <button
              type="button"
              data-hud-action="true"
              data-testid="timeline-return-live-btn"
              className="btn-secondary"
              style={{ padding: '3px 9px', fontSize: 11.5 }}
              onClick={onReturnToLive}
            >
              <ArrowLeft size={12} aria-hidden="true" />
              Back
            </button>
          </div>
        )}

        <div
          className={`canvas-hud-pill canvas-zoom-pill ${isZoomScrubbing ? 'is-scrubbing' : ''}`}
          onPointerDown={handleZoomPillPointerDown}
          onPointerMove={handleZoomPillPointerMove}
          onPointerUp={finishZoomScrub}
          onPointerCancel={finishZoomScrub}
          onLostPointerCapture={() => {
            zoomScrubRef.current = null;
            setIsZoomScrubbing(false);
          }}
          title="Drag horizontally to zoom (Shift for fine control, double-click % for 100%)"
        >
          <span
            role="slider"
            tabIndex={0}
            className="canvas-zoom-scrub"
            data-testid="reproduce-zoom-scrub"
            aria-label="Canvas zoom level. Drag horizontally or use arrow keys to adjust zoom."
            aria-valuemin={Math.round(MIN_SCALE * 100)}
            aria-valuemax={Math.round(MAX_SCALE * 100)}
            aria-valuenow={Math.round(view.scale * 100)}
            aria-valuetext={`${Math.round(view.scale * 100)}%`}
            style={
              {
                '--zoom-pct': `${Math.max(
                  0,
                  Math.min(100, ((view.scale - MIN_SCALE) / (MAX_SCALE - MIN_SCALE)) * 100)
                )}%`,
              } as React.CSSProperties
            }
            onKeyDown={handleZoomKeyDown}
            onDoubleClick={(e) => {
              e.stopPropagation();
              const applied = applyZoomCentered(1.0);
              setZoomAnnouncement(`Canvas zoom ${Math.round(applied * 100)}%`);
            }}
          >
            <MoveHorizontal size={11} className="canvas-zoom-arrows" aria-hidden="true" />
            <span className="canvas-zoom-value">{Math.round(view.scale * 100)}%</span>
          </span>
          <button
            type="button"
            data-hud-action="true"
            data-testid="reproduce-fit-all-btn"
            className="btn-secondary"
            style={{ padding: '3px 8px', fontSize: 11.5 }}
            onClick={fitAll}
          >
            <Maximize2 size={12} aria-hidden="true" />
            Fit All
          </button>
        </div>
      </div>

      {/* World Stage (1000x600 world coordinates; depth order: tables < agents) */}
      <div
        className="rep-world-viewport"
        style={{
          transform: `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`,
        }}
      >
        <div ref={worldStageRef} className="rep-world-stage" data-testid="rep-world-stage">
          <svg
            className="rep-tables-svg-layer"
            viewBox="0 0 1000 600"
            preserveAspectRatio="none"
            aria-label="Temporary Research Tables"
          >
            {tables.map((tbl) => {
              const pl = placementByCollabId.get(tbl.collaboration_id);
              if (!pl) return null;
              const isClusterDragging =
                dragSession?.kind === 'agent_cluster' &&
                dragSession.targetKey === `table:${tbl.collaboration_id}`;
              return (
                <ResearchTableSvg
                  key={tbl.collaboration_id}
                  table={tbl}
                  placement={pl}
                  motionDisabled={prefersReduced}
                  isDragging={isClusterDragging}
                  suppressClickRef={suppressClickRef}
                  onSilentSelectTable={onSilentSelectTable}
                  onTablePointerDown={(t, e) =>
                    startDragClusterTarget(`table:${t.collaboration_id}`, t.topic || 'Discussion Group', e)
                  }
                  onTablePointerEnter={handleTablePointerEnter}
                  onTablePointerLeave={scheduleHoverClose}
                />
              );
            })}
            <AgentInteractionFlows
              speechFlows={speechFlows}
              handoffFlows={handoffFlows}
              motionDisabled={prefersReduced}
            />
            {reportingPersonas.length > 0 && activeBriefing && effectiveBriefingCluster && (
              <BriefingEaselSvg
                cx={effectiveBriefingCluster.easelCx}
                cy={effectiveBriefingCluster.easelCy}
                topic={activeBriefing.topic}
                motionDisabled={prefersReduced}
                isDragging={dragSession?.kind === 'agent_cluster' && dragSession.targetKey === 'briefing'}
                suppressClickRef={suppressClickRef}
                onPointerDown={(e) => startDragClusterTarget('briefing', 'Briefing Group', e)}
                onPointerEnter={handleEaselPointerEnter}
                onPointerLeave={scheduleBriefingClose}
                onClick={() =>
                  onSilentSelectBriefing?.({
                    presenterIds: activeBriefing.presenter_ids,
                    topic: activeBriefing.topic,
                    linkedStepIds: activeBriefing.linked_step_ids,
                  })
                }
              />
            )}
            {visibleIntervention && (
              <InterventionMailSvg
                fromX={mailOrigin.x}
                fromY={mailOrigin.y}
                toX={mainCoord.x + 42}
                toY={mainCoord.y - 16}
                isResolved={visibleIntervention.status === 'resolved'}
                motionDisabled={prefersReduced}
                onPointerEnter={handleMailPointerEnter}
                onPointerLeave={scheduleInterventionClose}
                onClick={() => onSelectIntervention?.(visibleIntervention)}
              />
            )}
          </svg>

          <div className="rep-personas-dom-layer">
            {liveBubblePlacements.map((bub) => (
              <div
                key={`thinking-${bub.collabId}`}
                className="rep-live-thinking-bubble"
                data-testid={`live-table-thinking-bubble-${bub.collabId}`}
                data-bubble-cx={Math.round(bub.cx)}
                data-bubble-cy={Math.round(bub.cy)}
              >
                <div
                  className="rep-thinking-pill"
                  title={bub.text}
                  style={{
                    left: `${((bub.cx / 1000) * 100).toFixed(3)}%`,
                    top: `${((bub.cy / 600) * 100).toFixed(3)}%`,
                  }}
                >
                  {bub.text}
                </div>
                <span
                  className="rep-thinking-dot dot-md"
                  style={{
                    left: `${((bub.dotMidX / 1000) * 100).toFixed(3)}%`,
                    top: `${((bub.dotMidY / 600) * 100).toFixed(3)}%`,
                  }}
                  aria-hidden="true"
                />
                <span
                  className="rep-thinking-dot dot-sm"
                  style={{
                    left: `${((bub.dotRimX / 1000) * 100).toFixed(3)}%`,
                    top: `${((bub.dotRimY / 600) * 100).toFixed(3)}%`,
                  }}
                  aria-hidden="true"
                />
              </div>
            ))}
            {personas.map((persona) => {
              const coord = worldCoordsByPersona[persona.persona_id] || { x: 560, y: 300 };
              const cKey = clusterKeyByPersona[persona.persona_id];
              const isDraggingThis = Boolean(
                dragSession &&
                  ((dragSession.kind === 'single_agent' && dragSession.targetKey === persona.persona_id) ||
                    (dragSession.kind === 'agent_cluster' && cKey && dragSession.targetKey === cKey))
              );
              const interactiveMode = isHistoricalReplay
                ? 'replay_drag'
                : cKey
                  ? 'group_click'
                  : 'none';
              return (
                <PersonaSvg
                  key={persona.persona_id}
                  persona={persona}
                  worldX={coord.x}
                  worldY={coord.y}
                  isSpeakingPulse={speakingSet.has(persona.persona_id)}
                  motionDisabled={prefersReduced}
                  lookTargets={interactionByPersona[persona.persona_id]?.targets}
                  interaction={interactionByPersona[persona.persona_id]?.role ?? null}
                  sizeScale={personaScale}
                  interactiveMode={interactiveMode}
                  inCompositeGroup={Boolean(cKey)}
                  isDragging={isDraggingThis}
                  onDragPointerDown={startDragPersonaOrCluster}
                  onPersonaPointerEnter={
                    cKey
                      ? (_p, e) => {
                          if (cKey === 'briefing') {
                            openBriefingPopover(e.currentTarget);
                          } else if (cKey.startsWith('table:')) {
                            openTablePopoverForCollab(cKey.slice(6), e.currentTarget);
                          }
                        }
                      : undefined
                  }
                  onPersonaPointerLeave={
                    cKey
                      ? () => {
                          if (cKey === 'briefing') scheduleBriefingClose();
                          else scheduleHoverClose();
                        }
                      : undefined
                  }
                  onPersonaClick={
                    cKey
                      ? (_p, e) => {
                          e.stopPropagation();
                          if (suppressClickRef.current) return;
                          if (cKey === 'briefing' && activeBriefing) {
                            onSilentSelectBriefing?.({
                              presenterIds: activeBriefing.presenter_ids,
                              topic: activeBriefing.topic,
                              linkedStepIds: activeBriefing.linked_step_ids,
                            });
                          } else if (cKey.startsWith('table:')) {
                            const cid = cKey.slice(6);
                            const matchedTbl = tables.find((t) => t.collaboration_id === cid);
                            if (matchedTbl) onSilentSelectTable(matchedTbl);
                          }
                        }
                      : undefined
                  }
                />
              );
            })}
          </div>

          {isHistoricalReplay && resolvedPlacedCards.length > 0 && (
            <div className="rep-placed-cards-layer" data-testid="rep-placed-cards-layer">
              {resolvedPlacedCards.map((card) => renderPlacedCardElement(card, false))}
            </div>
          )}
        </div>
      </div>

      {/* Expanded Placed Card Overlay at Canvas Root */}
      {expandedPlacedCard && renderPlacedCardElement(expandedPlacedCard, true)}

      {/* Timeline-only Hoverable & Expandable Table Dialogue Popover */}
      {isHistoricalReplay && hoveredTable && activeDialogue && (
        <div
          className={`rep-table-dialogue-popover ${expandedPopover === 'table' ? 'is-expanded' : ''}`}
          data-testid="table-dialogue-popover"
          role="region"
          aria-label={`Table dialogue: ${activeDialogue.topic}`}
          style={expandedPopover === 'table' ? undefined : { left: hoveredTable.x, top: hoveredTable.y }}
          onPointerEnter={clearHoverCloseTimer}
          onPointerLeave={scheduleHoverClose}
        >
          <div className="rep-tdp-header">
            <div className="rep-tdp-title-row">
              <MessageSquare size={13} aria-hidden="true" />
              <span className="rep-tdp-title">
                {shortPopoverTitle(activeDialogue.topic) || 'Team Discussion'}
              </span>
            </div>
            <div className="rep-tdp-header-actions">
              <button
                type="button"
                className="rep-tdp-icon-btn"
                data-icon="pin"
                data-testid="place-dialogue-popover-btn"
                aria-label="Pin dialogue window onto infinite canvas"
                title="Pin on canvas (no hover needed)"
                onClick={(e) => {
                  e.stopPropagation();
                  clearHoverCloseTimer();
                  setExpandedPopover(null);
                  setHoveredTable(null);
                  placeCardOnCanvas(
                    'table',
                    activeDialogue.collaboration_id,
                    shortPopoverTitle(activeDialogue.topic) || 'Team Discussion',
                    null
                  );
                }}
              >
                <Pin size={13} className="rep-pin-icon is-unpinned" aria-hidden="true" />
              </button>
              <button
                type="button"
                className="rep-tdp-icon-btn"
                data-testid="expand-dialogue-popover-btn"
                aria-label={expandedPopover === 'table' ? 'Restore popover size' : 'Expand dialogue across canvas'}
                title={expandedPopover === 'table' ? 'Restore' : 'Expand'}
                onClick={(e) => {
                  e.stopPropagation();
                  clearHoverCloseTimer();
                  setExpandedPopover((prev) => (prev === 'table' ? null : 'table'));
                }}
              >
                {expandedPopover === 'table' ? (
                  <Minimize2 size={13} aria-hidden="true" />
                ) : (
                  <Maximize2 size={13} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>

          {activeDialogue.start_reason && (
            <div className="rep-tdp-reason">
              <strong>Why:</strong>{' '}
              {expandedPopover === 'table'
                ? toPlainUiText(activeDialogue.start_reason, 400)
                : toCompactLine(activeDialogue.start_reason, 72)}
            </div>
          )}

          <div className="rep-gchat-list" data-testid="table-dialogue-turns">
            {renderGroupChatTurns(
              activeDialogue.turns,
              'Agents are reviewing evidence around the table',
              expandedPopover !== 'table'
            )}
          </div>

          {activeDialogue.end_report && (
            <div className="rep-tdp-report">
              <strong>Report:</strong>{' '}
              {expandedPopover === 'table'
                ? toPlainUiText(activeDialogue.end_report, 600)
                : toCompactLine(activeDialogue.end_report, 150)}
            </div>
          )}
        </div>
      )}

      {/* Timeline-only Hoverable & Expandable PPT Whiteboard Briefing Popover */}
      {isHistoricalReplay && hoveredBriefing && activeBriefing && (
        <div
          className={`rep-table-dialogue-popover rep-briefing-popover ${expandedPopover === 'briefing' ? 'is-expanded' : ''}`}
          data-testid="briefing-easel-popover"
          role="region"
          aria-label={`Briefing: ${activeBriefing.topic}`}
          style={expandedPopover === 'briefing' ? undefined : { left: hoveredBriefing.x, top: hoveredBriefing.y }}
          onPointerEnter={clearBriefingCloseTimer}
          onPointerLeave={scheduleBriefingClose}
        >
          <div className="rep-tdp-header">
            <div className="rep-tdp-title-row">
              <Presentation size={13} aria-hidden="true" />
              <span className="rep-tdp-title">{shortPopoverTitle(activeBriefing.topic) || 'Briefing'}</span>
            </div>
            <div className="rep-tdp-header-actions">
              <button
                type="button"
                className="rep-tdp-icon-btn"
                data-icon="pin"
                data-testid="place-briefing-popover-btn"
                aria-label="Pin briefing window onto infinite canvas"
                title="Pin on canvas (no hover needed)"
                onClick={(e) => {
                  e.stopPropagation();
                  clearBriefingCloseTimer();
                  setExpandedPopover(null);
                  setHoveredBriefing(null);
                  placeCardOnCanvas(
                    'briefing',
                    activeBriefing.briefing_id,
                    shortPopoverTitle(activeBriefing.topic) || 'Briefing',
                    null
                  );
                }}
              >
                <Pin size={13} className="rep-pin-icon is-unpinned" aria-hidden="true" />
              </button>
              <button
                type="button"
                className="rep-tdp-icon-btn"
                data-testid="expand-briefing-popover-btn"
                aria-label={expandedPopover === 'briefing' ? 'Restore briefing size' : 'Expand briefing across canvas'}
                title={expandedPopover === 'briefing' ? 'Restore' : 'Expand'}
                onClick={(e) => {
                  e.stopPropagation();
                  clearBriefingCloseTimer();
                  setExpandedPopover((prev) => (prev === 'briefing' ? null : 'briefing'));
                }}
              >
                {expandedPopover === 'briefing' ? (
                  <Minimize2 size={13} aria-hidden="true" />
                ) : (
                  <Maximize2 size={13} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>

          <div className="rep-gchat-list" data-testid="briefing-dialogue-turns">
            {renderGroupChatTurns(
              activeBriefing.turns && activeBriefing.turns.length > 0
                ? activeBriefing.turns
                : [
                    {
                      seq: activeBriefing.seq,
                      speaker_id: activeBriefing.presenter_ids[0] || 'persona_validation',
                      speaker_label: activeBriefing.presenter_labels.join(' & ') || 'Specialist',
                      stance: 'briefing',
                      summary: activeBriefing.summary,
                    },
                  ],
              'Preparing whiteboard presentation',
              expandedPopover !== 'briefing'
            )}
          </div>
        </div>
      )}

      {/* Hoverable Intervention Email Popover */}
      {hoveredIntervention && visibleIntervention && (
        <div
          className="rep-table-dialogue-popover rep-intervention-popover"
          data-testid="intervention-mail-popover"
          role="region"
          aria-label="Human intervention request details"
          style={{ left: hoveredIntervention.x, top: hoveredIntervention.y }}
          onPointerEnter={clearInterventionCloseTimer}
          onPointerLeave={scheduleInterventionClose}
        >
          <div className="rep-tdp-header">
            <div className="rep-tdp-title-row">
              <Mail size={13} aria-hidden="true" />
              <span className="rep-tdp-title">Debate Escalation → Human Review</span>
            </div>
            <span className="rep-tdp-members">
              {visibleIntervention.status === 'resolved' ? 'Resolved' : 'Needs Input'}
            </span>
          </div>

          <div className="rep-gchat-list" data-testid="intervention-dialogue-turns">
            {renderGroupChatTurns(
              [
                {
                  seq: visibleIntervention.seq,
                  speaker_id: visibleIntervention.from_persona_ids[0] || 'persona_validation',
                  speaker_label: `${visibleIntervention.from_labels.join(' & ')}`,
                  stance: 'escalate',
                  summary: visibleIntervention.dissent_summary,
                },
                ...(visibleIntervention.user_decision
                  ? [
                      {
                        seq: visibleIntervention.resolved_seq || visibleIntervention.seq + 1,
                        speaker_id: 'persona_main',
                        speaker_label: 'Boss',
                        stance: 'human_guidance',
                        summary: `Human guidance: ${visibleIntervention.user_decision}`,
                      },
                    ]
                  : []),
              ],
              'Debating specialists requested human guidance.'
            )}
          </div>

          {!visibleIntervention.user_decision && (
            <div className="rep-tdp-reason">
              Click the envelope icon or answer directly in Research Chat.
            </div>
          )}
        </div>
      )}

      {/* Bottom Histogram Milestone Snapshot Timeline Dock */}
      {timelineSnapshots.length > 0 && (
        <div className="rep-canvas-timeline-dock" data-testid="canvas-timeline-dock">
          {activeSnap && (activeSnap.reason || activeSnap.report) && (
            <div className="rep-timeline-banner" data-testid="timeline-snapshot-banner">
              {activeSnap.reason && (
                <div>
                  <strong>Why started:</strong> {toPlainUiText(activeSnap.reason, 360)}
                </div>
              )}
              {activeSnap.report && (
                <div>
                  <strong>Outcome report:</strong> {toPlainUiText(activeSnap.report, 360)}
                </div>
              )}
            </div>
          )}

          <div className="rep-timeline-track" role="toolbar" aria-label="Canvas milestone snapshots">
            <span className="rep-timeline-track-label">
              <Clock size={12} aria-hidden="true" />
              <span>Timeline</span>
            </span>
            <div
              ref={histogramRef}
              className="rep-timeline-histogram"
              data-testid="timeline-histogram"
              tabIndex={0}
              role="region"
              aria-label="Scrollable milestone bars"
            >
              {histogramBars.map(({ snap, durSec, heightPx, stageMeta }) => {
                const isSelected = activeSnap?.snapshot_id === snap.snapshot_id;
                const cleanTip = toPlainUiText(snap.reason || snap.report || snap.label, 140);
                const snapDragCount = spatialBySnapshot[snap.snapshot_id]?.dragHistory.length || 0;
                return (
                  <button
                    key={snap.snapshot_id}
                    type="button"
                    data-testid={`timeline-snap-${snap.snapshot_id}`}
                    data-stage={stageMeta.key}
                    data-bar-height={heightPx}
                    data-duration-sec={durSec.toFixed(1)}
                    data-drag-count={snapDragCount}
                    className={`rep-timeline-bar-btn stage-${stageMeta.key} ${
                      isSelected ? 'is-active' : ''
                    } ${snapDragCount > 0 ? 'has-drag-history' : ''}`}
                    aria-label={`${snap.label} (${stageMeta.label}, ${durSec.toFixed(1)}s)`}
                    title={`${snap.label} · ${stageMeta.label} (${durSec.toFixed(1)}s)${
                      snapDragCount > 0 ? ` · ${snapDragCount} canvas edit(s)` : ''
                    }${cleanTip ? ` — ${cleanTip}` : ''}`}
                    onMouseEnter={() => setHoveredSnapId(snap.snapshot_id)}
                    onMouseLeave={() => setHoveredSnapId((prev) => (prev === snap.snapshot_id ? null : prev))}
                    onFocus={() => setHoveredSnapId(snap.snapshot_id)}
                    onBlur={() => setHoveredSnapId((prev) => (prev === snap.snapshot_id ? null : prev))}
                    onClick={() => onSelectTimelineSnapshot?.(snap)}
                  >
                    {snapDragCount > 0 && <span className="rep-timeline-bar-edited-dot" aria-hidden="true" />}
                    <span
                      className="rep-timeline-bar-col"
                      style={
                        {
                          height: `${heightPx}px`,
                          '--bar-color': stageMeta.color,
                        } as React.CSSProperties
                      }
                      aria-hidden="true"
                    />
                    <span className="visually-hidden">{snap.label}</span>
                  </button>
                );
              })}
            </div>
            {inspectedBar ? (
              <div className="rep-timeline-meta" data-testid="timeline-bar-readout">
                <span
                  className="rep-timeline-stage-dot"
                  style={{ backgroundColor: inspectedBar.stageMeta.color }}
                  aria-hidden="true"
                />
                <span className="rep-timeline-meta-label">{inspectedBar.snap.label}</span>
                <span className="rep-timeline-meta-stage">{inspectedBar.stageMeta.label}</span>
                <span className="rep-timeline-meta-dur">{inspectedBar.durSec.toFixed(1)}s</span>
              </div>
            ) : (
              <div className="rep-timeline-stage-legend" aria-hidden="true">
                {Object.values(STAGE_META).map((st) => (
                  <span key={st.key} className="rep-timeline-legend-item">
                    <span
                      className="rep-timeline-stage-dot"
                      style={{ backgroundColor: st.color }}
                    />
                    <span>{st.label}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div role="status" aria-live="polite" className="visually-hidden">
        {zoomAnnouncement}
      </div>
    </section>
  );
};
