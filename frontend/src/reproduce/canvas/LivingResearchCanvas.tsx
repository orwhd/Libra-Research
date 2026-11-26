import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Clock,
  Mail,
  Maximize2,
  MessageSquare,
  Minimize2,
  MoveHorizontal,
  Presentation,
  Users,
} from 'lucide-react';
import { MarkdownMath } from '../../components/MarkdownMath';
import { toPlainUiText } from '../plainText';
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
  computeLiveBubblePlacements,
  computePersonaHomeAnchors,
  resolvePersonaCollisions,
  type ActiveTableVisual,
} from './CollaborationProjection';
import { InterventionMailSvg } from './InterventionMailSvg';
import { getAgentBubbleStyle, PersonaSvg } from './PersonaSvg';
import { computeTablePlacements, ResearchTableSvg, type TablePlacement } from './ResearchTableSvg';

interface LivingResearchCanvasProps {
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

const MIN_SCALE = 0.4;
const MAX_SCALE = 2.2;

/**
 * Derive a single concise line describing what agents around an active table
 * are doing right now in Live mode.
 */
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
      challenge: `${who} auditing confounders`,
      propose_test: `${who} proposing Round 2 tests`,
      escalate: `${who} escalating dissent`,
      human_guidance: 'Main relaying human guidance',
      expand: `${who} expanding pod team`,
      align_data: `${who} verifying splits & controls`,
      pod: `${who} running ablation checks`,
      verify: `${who} verifying findings`,
      consensus: `${who} synthesizing verdict`,
    };
    if (stanceMap[last.stance]) {
      return stanceMap[last.stance];
    }
    const clean = toPlainUiText(last.summary, 30);
    if (clean) {
      return `${who}: ${clean}`;
    }
  }
  const rawTopic = toPlainUiText(
    table.topic || dialogue?.start_reason || 'Cross-checking findings',
    64
  );
  const shortTopic = rawTopic.split(/[:—]/)[0].trim();
  return toPlainUiText(shortTopic || rawTopic, 30);
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
  const [hoveredSnapId, setHoveredSnapId] = useState<string | null>(null);

  const hoverCloseTimerRef = useRef<number | null>(null);
  const briefingCloseTimerRef = useRef<number | null>(null);
  const interventionCloseTimerRef = useRef<number | null>(null);
  const histogramRef = useRef<HTMLDivElement>(null);
  const histScrollTargetRef = useRef<number | null>(null);
  const histScrollRafRef = useRef<number | null>(null);

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
    if (!isHistoricalReplay) {
      setHoveredTable(null);
      setHoveredBriefing(null);
      setExpandedPopover(null);
    }
  }, [isHistoricalReplay]);

  useEffect(() => {
    setHoveredTable(null);
    setHoveredBriefing(null);
    setExpandedPopover(null);
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
    if (!expandedPopover) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setExpandedPopover(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [expandedPopover]);

  const homeAnchorsById = useMemo(() => computePersonaHomeAnchors(personas), [personas]);
  const allPersonaIds = useMemo(() => personas.map((p) => p.persona_id), [personas]);

  const placements = useMemo(
    () => computeTablePlacements(tables, allPersonaIds, homeAnchorsById),
    [tables, allPersonaIds, homeAnchorsById]
  );

  const placementByCollabId = useMemo(
    () => new Map(placements.map((p) => [p.collaborationId, p])),
    [placements]
  );

  const tableSeatedPersonaIds = useMemo(() => {
    const seated = new Set<string>();
    for (const pl of placements) {
      for (const pid of Object.keys(pl.seatsByPersona)) {
        if (pid !== 'persona_main') seated.add(pid);
      }
    }
    return seated;
  }, [placements]);

  const reportingPersonas = useMemo(
    () =>
      personas.filter(
        (p) =>
          !p.is_main &&
          p.persona_id !== 'persona_main' &&
          (p.state === 'reporting' || p.state === 'handoff') &&
          !tableSeatedPersonaIds.has(p.persona_id)
      ),
    [personas, tableSeatedPersonaIds]
  );

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
    });
  }, [isHistoricalReplay, tables, placementByCollabId, tableDialogues, activeIntervention, homeAnchorsById, personas]);

  const worldCoordsByPersona = useMemo(() => {
    const raw: Record<string, { x: number; y: number }> = { ...homeAnchorsById };
    const pinnedIds = new Set<string>();
    const tableObstacles = placements.map((pl) => {
      const memberIds = Object.keys(pl.seatsByPersona).filter((pid) => pid !== 'persona_main');
      for (const pid of memberIds) {
        raw[pid] = pl.seatsByPersona[pid];
        pinnedIds.add(pid);
      }
      return { cx: pl.cx, cy: pl.cy, rx: pl.rx, ry: pl.ry, memberIds };
    });

    // Position reporting subagents right in front of Main & the PPT whiteboard easel
    if (reportingPersonas.length === 1) {
      raw[reportingPersonas[0].persona_id] = { x: 360, y: 294 };
      pinnedIds.add(reportingPersonas[0].persona_id);
    } else if (reportingPersonas.length === 2) {
      raw[reportingPersonas[0].persona_id] = { x: 368, y: 218 };
      raw[reportingPersonas[1].persona_id] = { x: 368, y: 370 };
      pinnedIds.add(reportingPersonas[0].persona_id);
      pinnedIds.add(reportingPersonas[1].persona_id);
    } else if (reportingPersonas.length >= 3) {
      const span = Math.min(260, (reportingPersonas.length - 1) * 124);
      reportingPersonas.forEach((rp, idx) => {
        const frac = idx / (reportingPersonas.length - 1);
        raw[rp.persona_id] = { x: 372, y: 294 - span / 2 + frac * span };
        pinnedIds.add(rp.persona_id);
      });
    }

    return resolvePersonaCollisions({
      personas,
      rawCoords: raw,
      tableObstacles,
      easelObstacle: reportingPersonas.length > 0 ? { cx: 258, cy: 294, rx: 44, ry: 42 } : null,
      bubbleObstacles: liveBubblePlacements,
      pinnedIds,
    });
  }, [personas, homeAnchorsById, placements, reportingPersonas, liveBubblePlacements]);

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
    const coords = personas.map((p) => worldCoordsByPersona[p.persona_id] || { x: 500, y: 300 });
    const minWX = Math.min(...coords.map((c) => c.x - 80));
    const maxWX = Math.max(...coords.map((c) => c.x + 80));
    const minWY = Math.min(...coords.map((c) => c.y - 80));
    const maxWY = Math.max(...coords.map((c) => c.y + 90));

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
  }, [personas, worldCoordsByPersona]);

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
          x: Math.max(-500, Math.min(500, prev.x - dx)),
          y: Math.max(-360, Math.min(360, prev.y - dy)),
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
        'button, .rep-table-group, .rep-briefing-easel, .rep-intervention-mail, .canvas-hud-top, .rep-table-dialogue-popover, .rep-canvas-timeline-dock'
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
      x: Math.max(-500, Math.min(500, st.tx + (e.clientX - st.x))),
      y: Math.max(-360, Math.min(360, st.ty + (e.clientY - st.y))),
    }));
  };

  const handlePointerUp = () => {
    panStartRef.current = null;
  };

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

  const handleEaselPointerEnter = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      if (!isHistoricalReplay) return;
      clearBriefingCloseTimer();
      const rootRect = rootRef.current?.getBoundingClientRect();
      if (!rootRect) return;
      const elRect = e.currentTarget.getBoundingClientRect();
      const popWidth = Math.min(460, Math.max(280, rootRect.width - 32));
      const popHeight = Math.min(360, Math.max(220, rootRect.height - 132));
      const rawX = elRect.left - rootRect.left + elRect.width / 2 - popWidth / 2;
      const rawYAbove = elRect.top - rootRect.top - popHeight - 8;
      const rawY =
        rawYAbove >= 54
          ? rawYAbove
          : Math.min(rootRect.height - popHeight - 76, elRect.bottom - rootRect.top + 8);
      setExpandedPopover((prev) => (prev === 'briefing' ? prev : null));
      setHoveredBriefing({
        x: Math.max(14, Math.min(rootRect.width - popWidth - 14, rawX)),
        y: Math.max(52, Math.min(rootRect.height - popHeight - 72, rawY)),
      });
    },
    [clearBriefingCloseTimer, isHistoricalReplay]
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

  const handleTablePointerEnter = useCallback(
    (table: ActiveTableVisual, _placement: TablePlacement, e: React.PointerEvent<SVGGElement>) => {
      if (!isHistoricalReplay) return;
      clearHoverCloseTimer();
      const rootRect = rootRef.current?.getBoundingClientRect();
      if (!rootRect) return;
      const tblRect = e.currentTarget.getBoundingClientRect();
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
      setHoveredTable({ collabId: table.collaboration_id, x: clampedX, y: clampedY });
    },
    [clearHoverCloseTimer, isHistoricalReplay]
  );

  const activeHoveredTable = hoveredTable
    ? tables.find((t) => t.collaboration_id === hoveredTable.collabId) || null
    : null;
  const activeDialogue =
    hoveredTable && activeHoveredTable
      ? tableDialogues[hoveredTable.collabId] || {
          collaboration_id: activeHoveredTable.collaboration_id,
          kind: activeHoveredTable.kind,
          topic: activeHoveredTable.topic,
          participants: activeHoveredTable.participant_persona_ids,
          start_reason: activeHoveredTable.topic || 'Specialists convened to cross-check findings.',
          end_report: '',
          turns: [],
        }
      : null;

  const activeSnap =
    timelineSnapshots.find((s) => s.snapshot_id === activeSnapshotId) ||
    (isHistoricalReplay && replaySeq !== null
      ? timelineSnapshots.find((s) => s.seq === replaySeq) || null
      : null);

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
      topic: 'Subagent Presentation to Main',
      summary: `${reportingPersonas.map((p) => p.label).join(' & ')} presenting experimental findings and next steps to Main Agent.`,
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

  const renderGroupChatTurns = (turns: TableDialogueTurn[], emptyText: string) => {
    if (turns.length === 0) {
      return <div className="rep-tdp-empty">{emptyText}</div>;
    }
    return turns.map((turn, idx) => {
      const style = getAgentBubbleStyle(turn.speaker_id, turn.speaker_label);
      const side = style.isMain ? 'right' : 'left';
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
            <span className="rep-gchat-speaker">{turn.speaker_label}</span>
            {turn.stance && <span className="rep-gchat-stance">{turn.stance}</span>}
          </div>
          <div
            className="rep-gchat-bubble markdown-body"
            style={{ backgroundColor: style.bg, borderColor: style.border }}
          >
            <MarkdownMath>{turn.summary}</MarkdownMath>
          </div>
        </div>
      );
    });
  };

  const gridSize = 22 * view.scale;

  return (
    <section
      ref={rootRef}
      className={`rep-living-canvas ${prefersReduced ? 'is-motion-disabled' : ''} ${
        mode === 'simulation' ? 'is-simulation' : ''
      } ${isHistoricalReplay ? 'is-replay' : ''}`}
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
        <div className="rep-world-stage" data-testid="rep-world-stage">
          <svg
            className="rep-tables-svg-layer"
            viewBox="0 0 1000 600"
            preserveAspectRatio="none"
            aria-label="Temporary Research Tables"
          >
            {tables.map((tbl) => {
              const pl = placementByCollabId.get(tbl.collaboration_id);
              if (!pl) return null;
              return (
                <ResearchTableSvg
                  key={tbl.collaboration_id}
                  table={tbl}
                  placement={pl}
                  motionDisabled={prefersReduced}
                  onSilentSelectTable={onSilentSelectTable}
                  onTablePointerEnter={handleTablePointerEnter}
                  onTablePointerLeave={scheduleHoverClose}
                />
              );
            })}
            {reportingPersonas.length > 0 && activeBriefing && (
              <BriefingEaselSvg
                cx={258}
                cy={294}
                topic={activeBriefing.topic}
                motionDisabled={prefersReduced}
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
              return (
                <PersonaSvg
                  key={persona.persona_id}
                  persona={persona}
                  worldX={coord.x}
                  worldY={coord.y}
                  isSpeakingPulse={speakingSet.has(persona.persona_id)}
                  motionDisabled={prefersReduced}
                />
              );
            })}
          </div>
        </div>
      </div>

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
                {toPlainUiText(activeDialogue.topic, 72) || 'Team Discussion'}
              </span>
            </div>
            <div className="rep-tdp-header-actions">
              <span className="rep-tdp-members">
                <Users size={11} aria-hidden="true" />
                {activeDialogue.participants.length}
              </span>
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
              <strong>Why started:</strong> {toPlainUiText(activeDialogue.start_reason, 180)}
            </div>
          )}

          <div className="rep-gchat-list" data-testid="table-dialogue-turns">
            {renderGroupChatTurns(
              activeDialogue.turns,
              'Agents are reviewing evidence around the table…'
            )}
          </div>

          {activeDialogue.end_report && (
            <div className="rep-tdp-report">
              <strong>Report:</strong> {toPlainUiText(activeDialogue.end_report, 200)}
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
              <span className="rep-tdp-title">{toPlainUiText(activeBriefing.topic, 72)}</span>
            </div>
            <div className="rep-tdp-header-actions">
              <span className="rep-tdp-members">
                <Users size={11} aria-hidden="true" />
                {activeBriefing.presenter_labels.join(', ')}
              </span>
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
              'Preparing whiteboard presentation…'
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
                        speaker_label: 'Main',
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
                return (
                  <button
                    key={snap.snapshot_id}
                    type="button"
                    data-testid={`timeline-snap-${snap.snapshot_id}`}
                    data-stage={stageMeta.key}
                    data-bar-height={heightPx}
                    data-duration-sec={durSec.toFixed(1)}
                    className={`rep-timeline-bar-btn stage-${stageMeta.key} ${
                      isSelected ? 'is-active' : ''
                    }`}
                    aria-label={`${snap.label} (${stageMeta.label}, ${durSec.toFixed(1)}s)`}
                    title={`${snap.label} · ${stageMeta.label} (${durSec.toFixed(1)}s)${
                      cleanTip ? ` — ${cleanTip}` : ''
                    }`}
                    onMouseEnter={() => setHoveredSnapId(snap.snapshot_id)}
                    onMouseLeave={() => setHoveredSnapId((prev) => (prev === snap.snapshot_id ? null : prev))}
                    onFocus={() => setHoveredSnapId(snap.snapshot_id)}
                    onBlur={() => setHoveredSnapId((prev) => (prev === snap.snapshot_id ? null : prev))}
                    onClick={() => onSelectTimelineSnapshot?.(snap)}
                  >
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
