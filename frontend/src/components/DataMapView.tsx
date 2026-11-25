import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Database,
  DownloadCloud,
  Filter,
  Layers,
  Cpu,
  BarChart3,
  ArrowDown,
  ArrowRightLeft,
  CheckCircle2,
  AlertTriangle,
  ShieldCheck,
  HelpCircle,
  MessageSquare,
  Trash2,
  Clock,
  X,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Ban,
  Sparkles,
} from 'lucide-react';
import type {
  AnchorPoint,
  FlowAnalysisNode,
  FlowCategoryNode,
  FlowDatasetNode,
  FlowLink,
  FlowOutcomeKind,
  FlowOutcomeNode,
  FlowSourceNode,
  LineageGraph,
  LineageNode,
  SemanticDataFlow,
  VisualComment,
} from '../types';
import { createVersionComment, deleteComment, getVersionLineage } from '../api';

interface DataMapViewProps {
  artifactVersionId: string;
  comments?: VisualComment[];
  pinMode?: boolean;
  onExitPinMode?: () => void;
  onCommentChanged?: () => Promise<void>;
}

const STAGE_ORDER: Record<LineageNode['stage'], number> = {
  source: 0,
  collection: 1,
  transformation: 2,
  dataset_snapshot: 3,
  analysis_run: 4,
  visualization_artifact: 5,
};

const SPECIAL_METADATA_KEYS = new Set([
  'script_text',
  'summary',
  'schema',
  'transform_steps',
  'missingness_quality',
  'params',
  'upstream_dependencies',
  'validation_report',
]);

const ACRONYMS: Record<string, string> = {
  id: 'ID',
  url: 'URL',
  oa: 'OA',
  svg: 'SVG',
  dom: 'DOM',
  nan: 'NaN',
  cagr: 'CAGR',
  yoy: 'YoY',
  api: 'API',
};

function humanizeKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((part) => {
      const lower = part.toLowerCase();
      if (ACRONYMS[lower]) return ACRONYMS[lower];
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ');
}

function isPlainRecord(val: unknown): val is Record<string, unknown> {
  return Boolean(val) && typeof val === 'object' && !Array.isArray(val);
}

function isPrimitive(val: unknown): boolean {
  return (
    val === null ||
    val === undefined ||
    typeof val === 'string' ||
    typeof val === 'number' ||
    typeof val === 'boolean'
  );
}

function formatScalar(key: string, val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'boolean') return val ? 'Yes' : 'No';
  if (typeof val === 'number') {
    if (!Number.isFinite(val)) return '—';
    if (/year/i.test(key) && Number.isInteger(val) && val >= 1800 && val <= 2200) {
      return String(val);
    }
    if (/(rate|share|cagr|growth)/i.test(key) && Math.abs(val) <= 2 && !/abs/i.test(key)) {
      return `${(val * 100).toFixed(1)}%`;
    }
    if (Number.isInteger(val)) return val.toLocaleString();
    return val.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  const str = String(val).trim();
  if (!str) return '—';
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(str)) {
    const dt = new Date(str);
    if (!Number.isNaN(dt.getTime())) return dt.toLocaleString();
  }
  return str;
}

function formatListValue(key: string, arr: unknown[]): string {
  if (arr.length === 0) return 'None';
  if (arr.every(isPrimitive)) {
    return arr.map((item) => formatScalar(key, item)).join(', ');
  }
  return `${arr.length} item(s)`;
}

function splitSchemaSpec(rawSpec: unknown): { typeLabel: string; description: string } {
  const str = String(rawSpec ?? '').trim();
  const match = str.match(/^(.+?)\s+[-–—]\s+([\s\S]+)$/);
  if (match) {
    return { typeLabel: match[1].trim(), description: match[2].trim() };
  }
  return { typeLabel: str || 'value', description: '' };
}

function StageIcon({ stage }: { stage: LineageNode['stage'] }) {
  switch (stage) {
    case 'source':
      return <Database size={16} color="var(--accent)" />;
    case 'collection':
      return <DownloadCloud size={16} color="var(--accent)" />;
    case 'transformation':
      return <Filter size={16} color="var(--amber)" />;
    case 'dataset_snapshot':
      return <Layers size={16} color="var(--emerald)" />;
    case 'analysis_run':
      return <Cpu size={16} color="#7c3aed" />;
    case 'visualization_artifact':
      return <BarChart3 size={16} color="var(--emerald)" />;
  }
}

type SelectedFlowEntity =
  | { kind: 'source'; node: FlowSourceNode }
  | { kind: 'category'; node: FlowCategoryNode }
  | { kind: 'outcome'; node: FlowOutcomeNode }
  | { kind: 'dataset'; node: FlowDatasetNode }
  | { kind: 'analysis'; node: FlowAnalysisNode };

interface BoxRect {
  id: string;
  col: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface SankeyRibbon {
  link: FlowLink;
  d: string;
  midX: number;
  midY: number;
  thickness: number;
  color: string;
  fillColor: string;
}

const SANKEY_COL_X = [14, 198, 404, 608, 796];
const SANKEY_COL_W = [152, 168, 166, 152, 162];
const SANKEY_SVG_W = 972;

function outcomeStroke(kind: FlowOutcomeKind): { stroke: string; band: string } {
  switch (kind) {
    case 'retained':
      return { stroke: '#0d9488', band: 'rgba(13, 148, 136, 0.22)' };
    case 'excluded':
      return { stroke: '#ea580c', band: 'rgba(249, 115, 22, 0.24)' };
    case 'transformed':
      return { stroke: '#4f46e5', band: 'rgba(99, 102, 241, 0.22)' };
    case 'unclassified':
    default:
      return { stroke: '#64748b', band: 'rgba(100, 116, 139, 0.20)' };
  }
}

function buildSankeyGeometry(
  flow: SemanticDataFlow,
  showAllCategories: boolean
): {
  boxes: Map<string, BoxRect>;
  visibleCategories: FlowCategoryNode[];
  hiddenCategoryCount: number;
  ribbons: SankeyRibbon[];
  svgHeight: number;
} {
  const MAX_DEFAULT_CATS = 6;
  const visibleCategories = showAllCategories
    ? flow.categories
    : flow.categories.slice(0, MAX_DEFAULT_CATS);
  const hiddenCategoryCount = Math.max(0, flow.categories.length - visibleCategories.length);

  const boxes = new Map<string, BoxRect>();
  const topPad = 48;
  const colGap = 14;
  const maxVal = Math.max(1, flow.total_input_count);

  const placeColumn = <T extends { id: string }>(
    colIdx: number,
    items: T[],
    getCount: (item: T) => number,
    minH: number,
    maxColH: number
  ): number => {
    let y = topPad;
    for (const item of items) {
      const cnt = Math.max(0, getCount(item));
      const propH = Math.round((cnt / maxVal) * maxColH);
      const h = Math.max(minH, Math.min(130, propH));
      boxes.set(item.id, {
        id: item.id,
        col: colIdx,
        x: SANKEY_COL_X[colIdx],
        y,
        w: SANKEY_COL_W[colIdx],
        h,
      });
      y += h + colGap;
    }
    return y + 18;
  };

  const h0 = placeColumn(0, flow.sources, (s) => s.raw_count, 72, 220);
  const h1 = placeColumn(1, visibleCategories, (c) => c.raw_count, 64, 260);
  const h2 = placeColumn(2, flow.outcomes, (o) => o.count, 62, 250);
  const h3 = placeColumn(3, flow.datasets, (d) => d.retained_count, 84, 200);
  const h4 = placeColumn(4, flow.analyses, (a) => a.output_count || a.input_count, 88, 200);
  const svgHeight = Math.max(380, h0, h1, h2, h3, h4);

  // Vertically center single-item or sparse columns inside svgHeight
  const centerSparseCol = (colIdx: number) => {
    const colBoxes = Array.from(boxes.values()).filter((b) => b.col === colIdx);
    if (colBoxes.length === 0) return;
    const minY = Math.min(...colBoxes.map((b) => b.y));
    const maxY = Math.max(...colBoxes.map((b) => b.y + b.h));
    const span = maxY - minY;
    const desiredTop = Math.max(topPad, Math.round((svgHeight - span) / 2));
    const shift = desiredTop - minY;
    if (shift > 4) {
      for (const b of colBoxes) b.y += shift;
    }
  };
  [0, 2, 3, 4].forEach(centerSparseCol);

  const visibleCatSet = new Set(visibleCategories.map((c) => c.id));
  const activeLinks = flow.links.filter(
    (l) => boxes.has(l.source_id) && boxes.has(l.target_id) && (l.source_col !== 1 || visibleCatSet.has(l.source_id))
  );

  // Compute port offsets so ribbons stack cleanly along node sides without overlapping
  const outCursor = new Map<string, number>();
  const inCursor = new Map<string, number>();

  const outTotals = new Map<string, number>();
  const inTotals = new Map<string, number>();
  for (const l of activeLinks) {
    outTotals.set(l.source_id, (outTotals.get(l.source_id) || 0) + Math.max(1, l.value));
    inTotals.set(l.target_id, (inTotals.get(l.target_id) || 0) + Math.max(1, l.value));
  }

  const ribbons: SankeyRibbon[] = [];
  for (const link of activeLinks) {
    const sb = boxes.get(link.source_id);
    const tb = boxes.get(link.target_id);
    if (!sb || !tb) continue;

    const val = Math.max(1, link.value);
    const outFrac = val / Math.max(1, outTotals.get(sb.id) || val);
    const inFrac = val / Math.max(1, inTotals.get(tb.id) || val);

    const usableSrcH = Math.max(14, sb.h - 14);
    const usableTgtH = Math.max(14, tb.h - 14);
    const srcBandH = Math.max(4, outFrac * usableSrcH);
    const tgtBandH = Math.max(4, inFrac * usableTgtH);

    const curOut = outCursor.get(sb.id) ?? 7;
    const curIn = inCursor.get(tb.id) ?? 7;
    outCursor.set(sb.id, curOut + srcBandH);
    inCursor.set(tb.id, curIn + tgtBandH);

    const x1 = sb.x + sb.w;
    const y1 = sb.y + curOut + srcBandH / 2;
    const x2 = tb.x;
    const y2 = tb.y + curIn + tgtBandH / 2;
    const mx = (x1 + x2) / 2;
    const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;

    const thickness = Math.max(3.5, Math.min(24, (srcBandH + tgtBandH) / 2));
    const colors = outcomeStroke(link.outcome_kind);
    ribbons.push({
      link,
      d,
      midX: mx,
      midY: (y1 + y2) / 2,
      thickness,
      color: colors.stroke,
      fillColor: colors.band,
    });
  }

  return { boxes, visibleCategories, hiddenCategoryCount, ribbons, svgHeight };
}

export const DataMapView: React.FC<DataMapViewProps> = ({
  artifactVersionId,
  comments = [],
  pinMode = false,
  onExitPinMode,
  onCommentChanged,
}) => {
  const stageRef = useRef<HTMLDivElement>(null);

  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [viewTab, setViewTab] = useState<'semantic' | 'legacy'>('semantic');
  const [selectedLegacyNodeId, setSelectedLegacyNodeId] = useState<string | null>(null);
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [hoveredFlowId, setHoveredFlowId] = useState<string | null>(null);
  const [showAllCategories, setShowAllCategories] = useState(false);
  const [expandedSubcats, setExpandedSubcats] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pinpoint comment state for Data Map
  const [activeCommentId, setActiveCommentId] = useState<string | null>(null);
  const [draftAnchor, setDraftAnchor] = useState<AnchorPoint | null>(null);
  const [draftContext, setDraftContext] = useState<{
    nodeId: string;
    nodeLabel: string;
    nodeKind: string;
  } | null>(null);
  const [commentText, setCommentText] = useState('');
  const [savingComment, setSavingComment] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);

  const dataMapComments = useMemo(
    () =>
      comments.filter(
        (c) =>
          (c.preview_view_state?.filter_state as Record<string, unknown> | undefined)?.surface ===
          'data_map'
      ),
    [comments]
  );

  useEffect(() => {
    let active = true;
    setError(null);
    setDraftAnchor(null);
    setActiveCommentId(null);
    getVersionLineage(artifactVersionId)
      .then((data) => {
        if (!active) return;
        const sorted = [...data.nodes].sort(
          (a, b) => (STAGE_ORDER[a.stage] ?? 99) - (STAGE_ORDER[b.stage] ?? 99)
        );
        setGraph({ ...data, nodes: sorted });
        setSelectedLegacyNodeId(sorted[3]?.node_id || sorted[0]?.node_id || null);

        const sf = data.semantic_flow;
        if (sf && sf.status !== 'unavailable' && sf.categories.length > 0) {
          setViewTab('semantic');
          setSelectedFlowId(sf.categories[0].id);
        } else {
          setViewTab('legacy');
          setSelectedFlowId(null);
        }
      })
      .catch((err) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
    };
  }, [artifactVersionId]);

  useEffect(() => {
    if (!pinMode && !draftAnchor && !activeCommentId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (draftAnchor) {
        setDraftAnchor(null);
        setCommentText('');
        setCommentError(null);
      } else if (activeCommentId) {
        setActiveCommentId(null);
      } else if (pinMode) {
        onExitPinMode?.();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [pinMode, draftAnchor, activeCommentId, onExitPinMode]);

  const semanticFlow = graph?.semantic_flow ?? null;
  const hasSemanticFlow = Boolean(semanticFlow && semanticFlow.status !== 'unavailable');

  const flowEntities = useMemo(() => {
    const map = new Map<string, SelectedFlowEntity>();
    if (!semanticFlow) return map;
    for (const s of semanticFlow.sources) map.set(s.id, { kind: 'source', node: s });
    for (const c of semanticFlow.categories) map.set(c.id, { kind: 'category', node: c });
    for (const o of semanticFlow.outcomes) map.set(o.id, { kind: 'outcome', node: o });
    for (const d of semanticFlow.datasets) map.set(d.id, { kind: 'dataset', node: d });
    for (const a of semanticFlow.analyses) map.set(a.id, { kind: 'analysis', node: a });
    return map;
  }, [semanticFlow]);

  // Compute full upstream + downstream connected path when a node is selected or hovered
  const focusEntityId = hoveredFlowId || selectedFlowId;
  const connectedPath = useMemo(() => {
    const nodeIds = new Set<string>();
    const linkIds = new Set<string>();
    if (!semanticFlow || !focusEntityId) return { nodeIds, linkIds };
    nodeIds.add(focusEntityId);

    const upQ = [focusEntityId];
    while (upQ.length > 0) {
      const cur = upQ.shift()!;
      for (const l of semanticFlow.links) {
        if (l.target_id === cur) {
          linkIds.add(l.id);
          if (!nodeIds.has(l.source_id)) {
            nodeIds.add(l.source_id);
            upQ.push(l.source_id);
          }
        }
      }
    }

    const downQ = [focusEntityId];
    while (downQ.length > 0) {
      const cur = downQ.shift()!;
      for (const l of semanticFlow.links) {
        if (l.source_id === cur) {
          linkIds.add(l.id);
          if (!nodeIds.has(l.target_id)) {
            nodeIds.add(l.target_id);
            downQ.push(l.target_id);
          }
        }
      }
    }

    return { nodeIds, linkIds };
  }, [semanticFlow, focusEntityId]);

  const sankeyGeom = useMemo(() => {
    if (!semanticFlow || semanticFlow.status === 'unavailable') return null;
    return buildSankeyGeometry(semanticFlow, showAllCategories);
  }, [semanticFlow, showAllCategories]);

  const handleStagePinClick = (
    e: React.MouseEvent<HTMLElement>,
    context?: { nodeId: string; nodeLabel: string; nodeKind: string }
  ) => {
    if (!pinMode || !stageRef.current) return;
    e.stopPropagation();
    const el = stageRef.current;
    const rect = el.getBoundingClientRect();
    const x_norm = Math.max(
      0.02,
      Math.min(1.8, (e.clientX - rect.left + el.scrollLeft) / Math.max(1, rect.width))
    );
    const y_norm = Math.max(
      0.04,
      Math.min(2.5, (e.clientY - rect.top + el.scrollTop) / Math.max(1, rect.height))
    );
    setActiveCommentId(null);
    setCommentError(null);
    setDraftAnchor({
      x_norm: Math.min(1, x_norm),
      y_norm: Math.min(1, y_norm),
    });
    setDraftContext(
      context || {
        nodeId: selectedFlowId || 'data_map',
        nodeLabel: 'Data Flow Stage',
        nodeKind: 'data_map',
      }
    );
    onExitPinMode?.();
  };

  const submitDataMapComment = async () => {
    const trimmed = commentText.trim();
    if (!draftAnchor || !trimmed || savingComment) return;
    const capturedAnchor = draftAnchor;
    const capturedCtx = draftContext;
    const rect = stageRef.current?.getBoundingClientRect();
    const sx = stageRef.current?.scrollLeft || 0;
    const sy = stageRef.current?.scrollTop || 0;

    // Close composer immediately for instant UX
    setCommentText('');
    setDraftAnchor(null);
    setDraftContext(null);
    setCommentError(null);
    setSavingComment(true);
    try {
      await createVersionComment(artifactVersionId, {
        text: trimmed,
        anchor: capturedAnchor,
        optional_element_hint: capturedCtx
          ? `data_map:${capturedCtx.nodeKind}:${capturedCtx.nodeLabel}`
          : 'data_map',
        preview_view_state: {
          viewport_width: Math.round(rect?.width || 1100),
          viewport_height: Math.round(rect?.height || 680),
          scroll_x: sx,
          scroll_y: sy,
          zoom: 1,
          filter_state: {
            surface: 'data_map',
            node_id: capturedCtx?.nodeId || '',
            node_label: capturedCtx?.nodeLabel || '',
            node_kind: capturedCtx?.nodeKind || '',
          },
        },
      });
      await onCommentChanged?.();
    } catch (err) {
      setDraftAnchor(capturedAnchor);
      setDraftContext(capturedCtx);
      setCommentText(trimmed);
      setCommentError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingComment(false);
    }
  };

  const handleDeleteComment = async (commentId: string) => {
    await deleteComment(commentId);
    if (activeCommentId === commentId) setActiveCommentId(null);
    await onCommentChanged?.();
  };

  if (error) {
    return (
      <div style={{ padding: 20, color: 'var(--crimson)' }}>
        Failed to load Data Map: {error}
      </div>
    );
  }
  if (!graph) {
    return (
      <div style={{ padding: 20, color: 'var(--ink-muted)' }}>
        Loading provenance Data Map...
      </div>
    );
  }

  // Legacy node & evidence helpers (preserved intact for backward compatibility & fallback)
  const activeLegacyNode =
    graph.nodes.find((n) => n.node_id === selectedLegacyNodeId) || graph.nodes[0];
  const activeFlowEntity = selectedFlowId ? flowEntities.get(selectedFlowId) || null : null;

  // Map selected flow entity back to a legacy node so script/schema/validation details are also inspectable
  const matchingLegacyNode = (() => {
    if (!activeFlowEntity) return activeLegacyNode;
    switch (activeFlowEntity.kind) {
      case 'source':
        return graph.nodes.find((n) => n.stage === 'source') || activeLegacyNode;
      case 'category':
        return graph.nodes.find((n) => n.stage === 'collection') || activeLegacyNode;
      case 'outcome':
        return graph.nodes.find((n) => n.stage === 'transformation') || activeLegacyNode;
      case 'dataset':
        return graph.nodes.find((n) => n.stage === 'dataset_snapshot') || activeLegacyNode;
      case 'analysis':
        return (
          graph.nodes.find((n) => n.stage === 'analysis_run') ||
          graph.nodes.find((n) => n.stage === 'visualization_artifact') ||
          activeLegacyNode
        );
    }
  })();

  const inspectedNode = viewTab === 'legacy' ? activeLegacyNode : matchingLegacyNode;
  const meta = inspectedNode?.metadata || {};
  const scriptText = typeof meta.script_text === 'string' ? meta.script_text : null;

  const summary = isPlainRecord(meta.summary) ? meta.summary : null;
  const summaryHeadline =
    summary && typeof summary.headline === 'string' ? summary.headline.trim() : '';
  const summaryMetrics =
    summary && isPlainRecord(summary.key_metrics)
      ? Object.entries(summary.key_metrics).filter(([, v]) => v !== null && v !== undefined)
      : [];

  const scalarOverview = Object.entries(meta).filter(
    ([k, v]) =>
      !SPECIAL_METADATA_KEYS.has(k) &&
      (isPrimitive(v) || (Array.isArray(v) && v.every(isPrimitive)))
  );

  const extraStructured = Object.entries(meta).filter(
    ([k, v]) =>
      !SPECIAL_METADATA_KEYS.has(k) &&
      !isPrimitive(v) &&
      !(Array.isArray(v) && v.every(isPrimitive))
  );

  const transformSteps = Array.isArray(meta.transform_steps)
    ? meta.transform_steps.map((s) => String(s))
    : [];

  const missingness = isPlainRecord(meta.missingness_quality)
    ? Object.entries(meta.missingness_quality)
    : [];

  const paramsEntries = isPlainRecord(meta.params) ? Object.entries(meta.params) : [];

  const upstreamDeps = Array.isArray(meta.upstream_dependencies)
    ? meta.upstream_dependencies.filter(isPlainRecord)
    : [];

  const validationReport = isPlainRecord(meta.validation_report)
    ? meta.validation_report
    : null;
  const domCheck =
    validationReport && isPlainRecord(validationReport.dom_check)
      ? validationReport.dom_check
      : null;

  const schemaEntries = isPlainRecord(meta.schema) ? Object.entries(meta.schema) : [];

  const renderValue = (key: string, val: unknown) => {
    if (Array.isArray(val)) {
      return <span className="evidence-kv-val">{formatListValue(key, val)}</span>;
    }
    const text = formatScalar(key, val);
    if (typeof val === 'string' && /^https?:\/\//i.test(val)) {
      return (
        <a className="evidence-link" href={val} target="_blank" rel="noopener noreferrer">
          {text}
        </a>
      );
    }
    if (/(id|_ref|path|mode|algorithm)$/i.test(key) && text !== '—') {
      return <code className="evidence-code-val">{text}</code>;
    }
    return <span className="evidence-kv-val">{text}</span>;
  };

  const activeDataMapComment =
    dataMapComments.find((c) => c.comment_id === activeCommentId) || null;

  const getPinPopoverStyle = (anchor: AnchorPoint): React.CSSProperties => {
    const openLeft = anchor.x_norm > 0.58;
    const openUp = anchor.y_norm > 0.62;
    return {
      ...(openLeft
        ? { right: `calc(${((1 - anchor.x_norm) * 100).toFixed(2)}% + 14px)` }
        : { left: `calc(${(anchor.x_norm * 100).toFixed(2)}% + 14px)` }),
      ...(openUp
        ? { bottom: `calc(${((1 - anchor.y_norm) * 100).toFixed(2)}% + 10px)` }
        : { top: `calc(${(anchor.y_norm * 100).toFixed(2)}% - 6px)` }),
    };
  };

  return (
    <div className="datamap-split" data-testid="datamap-view">
      <div
        ref={stageRef}
        className={`datamap-graph-pane ${pinMode ? 'is-pin-mode' : ''}`}
        onClick={(e) => {
          if (pinMode) {
            handleStagePinClick(e);
          } else {
            setActiveCommentId(null);
          }
        }}
      >
        {/* Top Status & Compatibility Banner */}
        <div className="dm-topbar">
          <div className="dm-topbar-left">
            {hasSemanticFlow && semanticFlow ? (
              <>
                <span className="dm-stat-pill">
                  <Database size={12} />
                  {semanticFlow.total_input_count.toLocaleString()} {semanticFlow.record_unit}
                </span>
                <span className="dm-stat-pill retained">
                  <CheckCircle2 size={12} />
                  {semanticFlow.total_retained_count.toLocaleString()} Retained
                </span>
                {semanticFlow.total_excluded_count > 0 && (
                  <span className="dm-stat-pill excluded">
                    <Ban size={12} />
                    {semanticFlow.total_excluded_count.toLocaleString()} Excluded
                  </span>
                )}
                <span
                  className={`dm-stat-pill ${
                    semanticFlow.conservation_check.passed ? 'verified' : 'warn'
                  }`}
                  title={semanticFlow.conservation_check.notes.join(' | ')}
                >
                  <ShieldCheck size={12} />
                  {semanticFlow.conservation_check.passed
                    ? 'Conservation Verified'
                    : 'Check Delta'}
                </span>
              </>
            ) : semanticFlow?.status === 'unavailable' ? (
              <span className="dm-compat-notice warn">
                <AlertTriangle size={13} />
                {semanticFlow.unavailable_reason || '追踪信息不可用'} — 已展示原有血缘视图
              </span>
            ) : (
              <span className="dm-compat-notice">
                <HelpCircle size={13} />
                语义分类信息尚未生成 — 当前展示原有血缘视图
              </span>
            )}
          </div>

          <div className="dm-topbar-right">
            {hasSemanticFlow && (
              <div className="dm-view-switch" role="tablist" aria-label="Data Map Mode">
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewTab === 'semantic'}
                  className={`dm-view-btn ${viewTab === 'semantic' ? 'active' : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewTab('semantic');
                  }}
                >
                  <GitBranch size={12} />
                  Semantic Flow
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewTab === 'legacy'}
                  className={`dm-view-btn ${viewTab === 'legacy' ? 'active' : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewTab('legacy');
                  }}
                >
                  Legacy Lineage
                </button>
              </div>
            )}
          </div>
        </div>

        {pinMode && (
          <div className="dm-pin-banner" data-testid="datamap-pin-banner">
            <MessageSquare size={13} />
            Click any Data Source, Semantic Category, or Excluded flow node to pin a comment
          </div>
        )}

        {/* Main Body: 5-Column Sankey Semantic Data Flow Map OR Legacy Fallback */}
        {viewTab === 'semantic' && semanticFlow && sankeyGeom ? (
          <div className="dm-sankey-wrap">
            {/* Legend Bar */}
            <div className="dm-legend-row">
              <span className="dm-legend-item">
                <span className="dm-legend-swatch retained" /> Retained
              </span>
              <span className="dm-legend-item">
                <span className="dm-legend-swatch excluded" /> Excluded
              </span>
              <span className="dm-legend-item">
                <span className="dm-legend-swatch transformed" /> Transformed / Unit Change
              </span>
              <span className="dm-legend-item">
                <span className="dm-legend-swatch unclassified" /> Unclassified / 待确认
              </span>
            </div>

            <div className="dm-sankey-scroller">
              <div
                className="dm-sankey-stage"
                style={{ width: SANKEY_SVG_W, height: sankeyGeom.svgHeight }}
              >
                {/* Column Headers */}
                {[
                  '1. Data Sources',
                  '2. Semantic Categories',
                  '3. Processing Outcomes',
                  '4. Processed Dataset',
                  '5. Analysis',
                ].map((colTitle, cIdx) => (
                  <div
                    key={colTitle}
                    className="dm-col-header"
                    style={{ left: SANKEY_COL_X[cIdx], width: SANKEY_COL_W[cIdx] }}
                  >
                    {colTitle}
                  </div>
                ))}

                {/* Sankey SVG Ribbons */}
                <svg
                  className="dm-sankey-svg"
                  width={SANKEY_SVG_W}
                  height={sankeyGeom.svgHeight}
                  viewBox={`0 0 ${SANKEY_SVG_W} ${sankeyGeom.svgHeight}`}
                  aria-hidden="true"
                >
                  {sankeyGeom.ribbons.map((rib) => {
                    const isActive = connectedPath.linkIds.has(rib.link.id);
                    const isDimmed = Boolean(focusEntityId) && !isActive;
                    const isExcl = rib.link.outcome_kind === 'excluded';
                    return (
                      <g
                        key={rib.link.id}
                        className={`dm-ribbon-group ${isActive ? 'active' : ''} ${
                          isDimmed ? 'dimmed' : ''
                        }`}
                      >
                        <path
                          d={rib.d}
                          fill="none"
                          stroke={rib.fillColor}
                          strokeWidth={rib.thickness + 4}
                          strokeLinecap="round"
                        />
                        <path
                          d={rib.d}
                          fill="none"
                          stroke={rib.color}
                          strokeWidth={isActive ? Math.max(2.6, rib.thickness * 0.48) : Math.max(1.8, rib.thickness * 0.36)}
                          strokeDasharray={isExcl ? '6 4' : undefined}
                          strokeLinecap="round"
                        />
                      </g>
                    );
                  })}
                </svg>

                {/* Unit Conversion Badges along Col 3 -> Col 4 ribbons */}
                {sankeyGeom.ribbons
                  .filter((r) => r.link.unit_changed)
                  .map((r) => (
                    <div
                      key={`conv_${r.link.id}`}
                      className="dm-unit-badge"
                      style={{ left: r.midX, top: r.midY }}
                      title={r.link.conversion_note}
                    >
                      <ArrowRightLeft size={10} />
                      <span>单位转换</span>
                    </div>
                  ))}

                {/* Col 0: Data Sources */}
                {semanticFlow.sources.map((src) => {
                  const box = sankeyGeom.boxes.get(src.id);
                  if (!box) return null;
                  const isSel = selectedFlowId === src.id;
                  const isConn = connectedPath.nodeIds.has(src.id);
                  const isDim = Boolean(focusEntityId) && !isConn;
                  return (
                    <button
                      key={src.id}
                      type="button"
                      data-flow-node={src.id}
                      data-flow-kind="source"
                      className={`dm-flow-node kind-source ${isSel ? 'selected' : ''} ${
                        isConn && !isSel ? 'connected' : ''
                      } ${isDim ? 'dimmed' : ''}`}
                      style={{ left: box.x, top: box.y, width: box.w, minHeight: box.h }}
                      onMouseEnter={() => setHoveredFlowId(src.id)}
                      onMouseLeave={() => setHoveredFlowId(null)}
                      onClick={(e) => {
                        if (pinMode) {
                          handleStagePinClick(e, {
                            nodeId: src.id,
                            nodeLabel: src.label,
                            nodeKind: 'source',
                          });
                          return;
                        }
                        e.stopPropagation();
                        setSelectedFlowId(src.id);
                      }}
                    >
                      <div className="dm-node-kicker">
                        <Database size={12} />
                        Source
                      </div>
                      <div className="dm-node-title" title={src.label}>
                        {src.label}
                      </div>
                      <div className="dm-node-count">
                        <strong>{src.raw_count.toLocaleString()}</strong> {src.record_unit}
                      </div>
                    </button>
                  );
                })}

                {/* Col 1: Semantic Categories */}
                {sankeyGeom.visibleCategories.map((cat) => {
                  const box = sankeyGeom.boxes.get(cat.id);
                  if (!box) return null;
                  const isSel = selectedFlowId === cat.id;
                  const isConn = connectedPath.nodeIds.has(cat.id);
                  const isDim = Boolean(focusEntityId) && !isConn;
                  const retPct = cat.raw_count > 0 ? Math.round((cat.retained_count / cat.raw_count) * 100) : 0;
                  return (
                    <button
                      key={cat.id}
                      type="button"
                      data-flow-node={cat.id}
                      data-flow-kind="category"
                      className={`dm-flow-node ${
                        cat.is_unclassified ? 'kind-unclassified' : 'kind-category'
                      } ${isSel ? 'selected' : ''} ${isConn && !isSel ? 'connected' : ''} ${
                        isDim ? 'dimmed' : ''
                      }`}
                      style={{ left: box.x, top: box.y, width: box.w, minHeight: box.h }}
                      onMouseEnter={() => setHoveredFlowId(cat.id)}
                      onMouseLeave={() => setHoveredFlowId(null)}
                      onClick={(e) => {
                        if (pinMode) {
                          handleStagePinClick(e, {
                            nodeId: cat.id,
                            nodeLabel: cat.label,
                            nodeKind: 'category',
                          });
                          return;
                        }
                        e.stopPropagation();
                        setSelectedFlowId(cat.id);
                      }}
                    >
                      <div className="dm-node-kicker">
                        <span>{cat.is_unclassified ? 'Unclassified' : 'Category'}</span>
                        <span className={`dm-conf-tag ${cat.confidence}`}>{cat.confidence}</span>
                      </div>
                      <div className="dm-node-title" title={cat.label}>
                        {cat.label}
                      </div>
                      <div className="dm-node-split-counts">
                        <span>Raw {cat.raw_count}</span>
                        <span className="txt-ret">✓ {cat.retained_count}</span>
                        {cat.excluded_count > 0 && (
                          <span className="txt-exc">✕ {cat.excluded_count}</span>
                        )}
                      </div>
                      <div className="dm-node-minibar" aria-hidden="true">
                        <span style={{ width: `${retPct}%` }} />
                      </div>
                    </button>
                  );
                })}

                {/* Col 2: Processing Outcomes */}
                {semanticFlow.outcomes.map((out) => {
                  const box = sankeyGeom.boxes.get(out.id);
                  if (!box) return null;
                  const isSel = selectedFlowId === out.id;
                  const isConn = connectedPath.nodeIds.has(out.id);
                  const isDim = Boolean(focusEntityId) && !isConn;
                  return (
                    <button
                      key={out.id}
                      type="button"
                      data-flow-node={out.id}
                      data-flow-kind={`outcome_${out.outcome_kind}`}
                      className={`dm-flow-node kind-${out.outcome_kind} ${
                        isSel ? 'selected' : ''
                      } ${isConn && !isSel ? 'connected' : ''} ${isDim ? 'dimmed' : ''}`}
                      style={{ left: box.x, top: box.y, width: box.w, minHeight: box.h }}
                      onMouseEnter={() => setHoveredFlowId(out.id)}
                      onMouseLeave={() => setHoveredFlowId(null)}
                      onClick={(e) => {
                        if (pinMode) {
                          handleStagePinClick(e, {
                            nodeId: out.id,
                            nodeLabel: out.label,
                            nodeKind: out.outcome_kind,
                          });
                          return;
                        }
                        e.stopPropagation();
                        setSelectedFlowId(out.id);
                      }}
                    >
                      <div className="dm-node-kicker">
                        {out.outcome_kind === 'excluded' ? (
                          <Ban size={11} />
                        ) : (
                          <CheckCircle2 size={11} />
                        )}
                        <span>{out.outcome_kind}</span>
                      </div>
                      <div className="dm-node-title" title={out.label}>
                        {out.label}
                      </div>
                      <div className="dm-node-count">
                        <strong>{out.count.toLocaleString()}</strong> {semanticFlow.record_unit}
                      </div>
                    </button>
                  );
                })}

                {/* Col 3: Processed Dataset */}
                {semanticFlow.datasets.map((ds) => {
                  const box = sankeyGeom.boxes.get(ds.id);
                  if (!box) return null;
                  const isSel = selectedFlowId === ds.id;
                  const isConn = connectedPath.nodeIds.has(ds.id);
                  const isDim = Boolean(focusEntityId) && !isConn;
                  return (
                    <button
                      key={ds.id}
                      type="button"
                      data-flow-node={ds.id}
                      data-flow-kind="dataset"
                      className={`dm-flow-node kind-dataset ${isSel ? 'selected' : ''} ${
                        isConn && !isSel ? 'connected' : ''
                      } ${isDim ? 'dimmed' : ''}`}
                      style={{ left: box.x, top: box.y, width: box.w, minHeight: box.h }}
                      onMouseEnter={() => setHoveredFlowId(ds.id)}
                      onMouseLeave={() => setHoveredFlowId(null)}
                      onClick={(e) => {
                        if (pinMode) {
                          handleStagePinClick(e, {
                            nodeId: ds.id,
                            nodeLabel: ds.label,
                            nodeKind: 'dataset',
                          });
                          return;
                        }
                        e.stopPropagation();
                        setSelectedFlowId(ds.id);
                      }}
                    >
                      <div className="dm-node-kicker">
                        <Layers size={11} />
                        <span>{ds.version_label}</span>
                      </div>
                      <div className="dm-node-title">Cleaned Dataset</div>
                      <div className="dm-node-count">
                        <strong>{ds.retained_count.toLocaleString()}</strong> {ds.record_unit}
                      </div>
                      <div className="dm-node-sub">{ds.schema_field_count} fields</div>
                    </button>
                  );
                })}

                {/* Col 4: Analysis */}
                {semanticFlow.analyses.map((an) => {
                  const box = sankeyGeom.boxes.get(an.id);
                  if (!box) return null;
                  const isSel = selectedFlowId === an.id;
                  const isConn = connectedPath.nodeIds.has(an.id);
                  const isDim = Boolean(focusEntityId) && !isConn;
                  return (
                    <button
                      key={an.id}
                      type="button"
                      data-flow-node={an.id}
                      data-flow-kind="analysis"
                      className={`dm-flow-node ${
                        an.unit_changed ? 'kind-transformed' : 'kind-dataset'
                      } ${isSel ? 'selected' : ''} ${isConn && !isSel ? 'connected' : ''} ${
                        isDim ? 'dimmed' : ''
                      }`}
                      style={{ left: box.x, top: box.y, width: box.w, minHeight: box.h }}
                      onMouseEnter={() => setHoveredFlowId(an.id)}
                      onMouseLeave={() => setHoveredFlowId(null)}
                      onClick={(e) => {
                        if (pinMode) {
                          handleStagePinClick(e, {
                            nodeId: an.id,
                            nodeLabel: an.label,
                            nodeKind: 'analysis',
                          });
                          return;
                        }
                        e.stopPropagation();
                        setSelectedFlowId(an.id);
                      }}
                    >
                      <div className="dm-node-kicker">
                        <Cpu size={11} />
                        <span>Analysis</span>
                      </div>
                      <div className="dm-node-title" title={an.label}>
                        {an.label}
                      </div>
                      <div className="dm-node-count">
                        <strong>{an.output_count.toLocaleString()}</strong> {an.output_unit}
                      </div>
                      {an.unit_changed && (
                        <div className="dm-node-sub" style={{ color: '#4f46e5', fontWeight: 600 }}>
                          Unit converted from {an.input_count.toLocaleString()} {an.input_unit}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {sankeyGeom.hiddenCategoryCount > 0 && (
              <div className="dm-expand-row">
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: 12, padding: '5px 12px' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowAllCategories((v) => !v);
                  }}
                >
                  {showAllCategories
                    ? 'Show Main Categories Only'
                    : `Expand +${sankeyGeom.hiddenCategoryCount} More Categories`}
                </button>
              </div>
            )}
          </div>
        ) : (
          /* Original 6-Stage Legacy Lineage View (fallback & toggleable compatibility view) */
          <div className="datamap-stage-chain">
            {graph.nodes.map((node, idx) => (
              <React.Fragment key={node.node_id}>
                <button
                  type="button"
                  className={`datamap-node-btn ${
                    activeLegacyNode?.node_id === node.node_id ? 'active' : ''
                  }`}
                  data-stage={node.stage}
                  onClick={(e) => {
                    if (pinMode) {
                      handleStagePinClick(e, {
                        nodeId: node.node_id,
                        nodeLabel: node.label,
                        nodeKind: node.stage,
                      });
                      return;
                    }
                    e.stopPropagation();
                    setSelectedLegacyNodeId(node.node_id);
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                    <StageIcon stage={node.stage} />
                    <span>
                      <div
                        style={{
                          fontSize: 10.5,
                          textTransform: 'uppercase',
                          color: 'var(--ink-muted)',
                          fontWeight: 700,
                        }}
                      >
                        {node.stage.replace('_', ' ')}
                      </div>
                      <div style={{ fontSize: 13, fontWeight: 700 }}>{node.label}</div>
                      <div style={{ fontSize: 11.5, color: 'var(--ink-secondary)' }}>
                        {node.subtitle}
                      </div>
                    </span>
                  </span>
                </button>
                {idx < graph.nodes.length - 1 && (
                  <div className="datamap-arrow" aria-hidden="true">
                    <ArrowDown size={14} />
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        )}

        {/* Saved Data Map Pinpoint Comments */}
        {dataMapComments.map((c) => {
          const isOpen = activeCommentId === c.comment_id;
          return (
            <button
              key={c.comment_id}
              type="button"
              className={`preview-comment-pin ${c.status === 'addressed' ? 'addressed' : ''} ${
                isOpen ? 'open' : ''
              }`}
              style={{ left: `${c.anchor.x_norm * 100}%`, top: `${c.anchor.y_norm * 100}%` }}
              aria-label={`Data Map Comment: ${c.text}`}
              aria-expanded={isOpen}
              onClick={(e) => {
                e.stopPropagation();
                setDraftAnchor(null);
                setActiveCommentId((prev) => (prev === c.comment_id ? null : c.comment_id));
              }}
            >
              <MessageSquare size={13} />
            </button>
          );
        })}

        {activeDataMapComment && (
          <div
            className="preview-comment-popover"
            style={getPinPopoverStyle(activeDataMapComment.anchor)}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="preview-comment-popover-head">
              <span
                className={`preview-comment-status ${
                  activeDataMapComment.status === 'addressed' ? 'addressed' : 'pending'
                }`}
              >
                {activeDataMapComment.status === 'addressed' ? (
                  <CheckCircle2 size={12} />
                ) : (
                  <Clock size={12} />
                )}
                {activeDataMapComment.status === 'addressed' ? 'Addressed' : 'Pending'}
              </span>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <button
                  type="button"
                  className="preview-comment-icon-btn danger"
                  aria-label="Delete comment"
                  onClick={() => void handleDeleteComment(activeDataMapComment.comment_id)}
                >
                  <Trash2 size={12} />
                </button>
                <button
                  type="button"
                  className="preview-comment-icon-btn"
                  aria-label="Dismiss comment"
                  onClick={() => setActiveCommentId(null)}
                >
                  <X size={13} />
                </button>
              </div>
            </div>
            <div className="preview-comment-popover-body">{activeDataMapComment.text}</div>
          </div>
        )}

        {draftAnchor && (
          <>
            <div
              className="preview-comment-pin draft"
              style={{ left: `${draftAnchor.x_norm * 100}%`, top: `${draftAnchor.y_norm * 100}%` }}
            >
              <MessageSquare size={13} />
            </div>

            <form
              className="preview-comment-popover"
              style={getPinPopoverStyle(draftAnchor)}
              onClick={(e) => e.stopPropagation()}
              onSubmit={(e) => {
                e.preventDefault();
                void submitDataMapComment();
              }}
            >
              <textarea
                autoFocus
                rows={2}
                className="preview-comment-input"
                aria-label="Data Map comment text"
                placeholder="Comment on this data source, category, or filter rule..."
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void submitDataMapComment();
                  }
                }}
                required
              />
              {commentError && <div className="preview-comment-error">{commentError}</div>}
              <div className="preview-comment-popover-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: 11.5, padding: '4px 9px' }}
                  onClick={() => {
                    setDraftAnchor(null);
                    setCommentText('');
                    setCommentError(null);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  style={{ fontSize: 11.5, padding: '4px 10px', borderRadius: 8 }}
                  disabled={savingComment || !commentText.trim()}
                >
                  {savingComment ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </>
        )}
      </div>

      {/* Right-Hand Side Detail Inspector */}
      <aside className="datamap-inspector" aria-label="Data Flow & Lineage Details">
        <div className="evidence-stack">
          {viewTab === 'semantic' && activeFlowEntity && (
            <>
              {activeFlowEntity.kind === 'category' && (
                <div className="dm-detail-block">
                  <header className="evidence-head">
                    <div className="evidence-stage-kicker">
                      Semantic Category ·{' '}
                      {activeFlowEntity.node.is_unclassified ? 'Unclassified' : activeFlowEntity.node.confidence} confidence
                    </div>
                    <h3 className="evidence-title">{activeFlowEntity.node.label}</h3>
                    <p className="evidence-subtitle">{activeFlowEntity.node.description}</p>
                  </header>

                  <section className="evidence-section" style={{ marginTop: 12 }}>
                    <div className="evidence-section-title">Volume Reconciliation</div>
                    <div className="evidence-metrics-grid">
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Raw Input</span>
                        <span className="evidence-metric-value">
                          {activeFlowEntity.node.raw_count.toLocaleString()}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Retained</span>
                        <span className="evidence-metric-value" style={{ color: 'var(--emerald)' }}>
                          {activeFlowEntity.node.retained_count.toLocaleString()}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Excluded</span>
                        <span className="evidence-metric-value" style={{ color: '#ea580c' }}>
                          {activeFlowEntity.node.excluded_count.toLocaleString()}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Dataset Version</span>
                        <span className="evidence-metric-value" style={{ fontSize: 12 }}>
                          {activeFlowEntity.node.dataset_version}
                        </span>
                      </div>
                    </div>
                  </section>

                  <section className="evidence-section" style={{ marginTop: 12 }}>
                    <div className="evidence-section-title">Classification Basis &amp; Rule</div>
                    <div className="evidence-callout">{activeFlowEntity.node.field_basis}</div>
                    <div className="evidence-kv-list">
                      <div className="evidence-kv-row">
                        <span className="evidence-kv-key">Filter Rule</span>
                        <code className="evidence-code-val">{activeFlowEntity.node.rule_summary}</code>
                      </div>
                      <div className="evidence-kv-row">
                        <span className="evidence-kv-key">Verifiable Status</span>
                        <span className="evidence-kv-val">{activeFlowEntity.node.verifiable_status}</span>
                      </div>
                    </div>
                  </section>

                  {activeFlowEntity.node.subcategories.length > 0 && (
                    <section className="evidence-section" style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className="dm-subcat-toggle"
                        onClick={() => setExpandedSubcats((v) => !v)}
                      >
                        {expandedSubcats ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        Subcategories ({activeFlowEntity.node.subcategories.length})
                      </button>
                      {expandedSubcats && (
                        <div className="evidence-schema-list">
                          {activeFlowEntity.node.subcategories.map((sub) => (
                            <div key={sub.id} className="evidence-schema-row">
                              <div className="evidence-schema-head">
                                <span style={{ fontWeight: 650, fontSize: 12 }}>{sub.label}</span>
                                <span className="evidence-field-type">
                                  {sub.retained_count}/{sub.raw_count} retained
                                </span>
                              </div>
                              {sub.rule_summary && (
                                <div className="evidence-field-desc">{sub.rule_summary}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </section>
                  )}

                  {activeFlowEntity.node.secondary_tags.length > 0 && (
                    <section className="evidence-section" style={{ marginTop: 12 }}>
                      <div className="evidence-section-title">
                        Overlapping Semantic Tags (Non-additive)
                      </div>
                      <div className="dm-tag-list">
                        {activeFlowEntity.node.secondary_tags.map((t) => (
                          <span key={t.label} className="dm-tag-chip">
                            <Sparkles size={11} />
                            {t.label}: {t.count.toLocaleString()}
                          </span>
                        ))}
                      </div>
                    </section>
                  )}
                </div>
              )}

              {activeFlowEntity.kind === 'outcome' && (
                <div className="dm-detail-block">
                  <header className="evidence-head">
                    <div
                      className="evidence-stage-kicker"
                      style={{
                        color:
                          activeFlowEntity.node.outcome_kind === 'excluded'
                            ? '#ea580c'
                            : 'var(--emerald)',
                      }}
                    >
                      Processing Outcome · {activeFlowEntity.node.outcome_kind}
                    </div>
                    <h3 className="evidence-title">{activeFlowEntity.node.label}</h3>
                    <p className="evidence-subtitle">Stage: {activeFlowEntity.node.stage}</p>
                  </header>

                  <section className="evidence-section" style={{ marginTop: 12 }}>
                    <div className="evidence-metrics-grid">
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Record Count</span>
                        <span className="evidence-metric-value">
                          {activeFlowEntity.node.count.toLocaleString()}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Processing Stage</span>
                        <span className="evidence-metric-value" style={{ fontSize: 12 }}>
                          {activeFlowEntity.node.stage}
                        </span>
                      </div>
                    </div>
                  </section>

                  <section className="evidence-section" style={{ marginTop: 12 }}>
                    <div className="evidence-section-title">
                      {activeFlowEntity.node.outcome_kind === 'excluded'
                        ? 'Exclusion Reason & Filter Rule'
                        : 'Processing Rule'}
                    </div>
                    <div className="evidence-callout">{activeFlowEntity.node.reason_label}</div>
                    <div className="evidence-kv-list">
                      <div className="evidence-kv-row">
                        <span className="evidence-kv-key">Applied Filter Rule</span>
                        <code className="evidence-code-val">{activeFlowEntity.node.filter_rule}</code>
                      </div>
                    </div>
                  </section>

                  {activeFlowEntity.node.affected_categories.length > 0 && (
                    <section className="evidence-section" style={{ marginTop: 12 }}>
                      <div className="evidence-section-title">Breakdown by Semantic Category</div>
                      <div className="evidence-kv-list">
                        {activeFlowEntity.node.affected_categories.map((ref) => (
                          <button
                            key={ref.category_id}
                            type="button"
                            className="evidence-kv-row dm-cat-ref-btn"
                            onClick={() => setSelectedFlowId(ref.category_id)}
                          >
                            <span className="evidence-kv-key">{ref.category_label}</span>
                            <span className="evidence-kv-val" style={{ fontWeight: 700 }}>
                              {ref.count.toLocaleString()}
                            </span>
                          </button>
                        ))}
                      </div>
                    </section>
                  )}
                </div>
              )}

              {activeFlowEntity.kind === 'source' && (
                <div className="dm-detail-block">
                  <header className="evidence-head">
                    <div className="evidence-stage-kicker">Data Source Origin</div>
                    <h3 className="evidence-title">{activeFlowEntity.node.label}</h3>
                    <p className="evidence-subtitle">
                      {activeFlowEntity.node.provider} · {activeFlowEntity.node.raw_count.toLocaleString()}{' '}
                      {activeFlowEntity.node.record_unit}
                    </p>
                  </header>
                </div>
              )}

              {activeFlowEntity.kind === 'dataset' && (
                <div className="dm-detail-block">
                  <header className="evidence-head">
                    <div className="evidence-stage-kicker">Processed Dataset Snapshot</div>
                    <h3 className="evidence-title">{activeFlowEntity.node.label}</h3>
                    <p className="evidence-subtitle">
                      {activeFlowEntity.node.retained_count.toLocaleString()}{' '}
                      {activeFlowEntity.node.record_unit} ·{' '}
                      {activeFlowEntity.node.schema_field_count} fields
                    </p>
                  </header>
                </div>
              )}

              {activeFlowEntity.kind === 'analysis' && (
                <div className="dm-detail-block">
                  <header className="evidence-head">
                    <div className="evidence-stage-kicker">Downstream Analysis</div>
                    <h3 className="evidence-title">{activeFlowEntity.node.label}</h3>
                    {activeFlowEntity.node.summary_headline && (
                      <p className="evidence-subtitle">{activeFlowEntity.node.summary_headline}</p>
                    )}
                  </header>

                  {activeFlowEntity.node.unit_changed && (
                    <section className="evidence-section" style={{ marginTop: 12 }}>
                      <div className="evidence-section-title">Unit Conversion (No Data Loss)</div>
                      <div className="evidence-callout">
                        {activeFlowEntity.node.conversion_explanation}
                      </div>
                    </section>
                  )}
                </div>
              )}
            </>
          )}

          {/* Provenance Stage Evidence (shared below or standalone in legacy view) */}
          {inspectedNode && (
            <>
              {viewTab === 'legacy' && (
                <header className="evidence-head">
                  <div className="evidence-stage-kicker">
                    {inspectedNode.stage.replace('_', ' ')}
                  </div>
                  <h3 className="evidence-title">{inspectedNode.label}</h3>
                  {inspectedNode.subtitle && (
                    <p className="evidence-subtitle">{inspectedNode.subtitle}</p>
                  )}
                </header>
              )}

              {(summaryHeadline || summaryMetrics.length > 0) && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Key Findings</div>
                  {summaryHeadline && <div className="evidence-callout">{summaryHeadline}</div>}
                  {summaryMetrics.length > 0 && (
                    <div className="evidence-metrics-grid">
                      {summaryMetrics.map(([k, v]) => (
                        <div key={k} className="evidence-metric-card">
                          <span className="evidence-metric-label">{humanizeKey(k)}</span>
                          <span className="evidence-metric-value">
                            {Array.isArray(v) ? formatListValue(k, v) : formatScalar(k, v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {scalarOverview.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Stage Properties</div>
                  <div className="evidence-kv-list">
                    {scalarOverview.map(([k, v]) => (
                      <div key={k} className="evidence-kv-row">
                        <span className="evidence-kv-key">{humanizeKey(k)}</span>
                        {renderValue(k, v)}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {transformSteps.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">
                    Transformation Pipeline ({transformSteps.length})
                  </div>
                  <ol className="evidence-steps-list">
                    {transformSteps.map((step, i) => (
                      <li key={i} className="evidence-step-item">
                        {step}
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {missingness.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Data Quality</div>
                  <div className="evidence-metrics-grid">
                    {missingness.map(([k, v]) => (
                      <div key={k} className="evidence-metric-card">
                        <span className="evidence-metric-label">{humanizeKey(k)}</span>
                        <span className="evidence-metric-value">{formatScalar(k, v)}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {paramsEntries.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Execution Parameters</div>
                  <div className="evidence-kv-list">
                    {paramsEntries.map(([k, v]) => (
                      <div key={k} className="evidence-kv-row">
                        <span className="evidence-kv-key">{humanizeKey(k)}</span>
                        {renderValue(k, v)}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {validationReport && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Browser Validation</div>
                  <div
                    className={`evidence-status-banner ${
                      validationReport.passed ? 'passed' : 'failed'
                    }`}
                  >
                    {validationReport.passed ? (
                      <CheckCircle2 size={14} />
                    ) : (
                      <AlertTriangle size={14} />
                    )}
                    <span>
                      {validationReport.passed
                        ? 'Headless Chromium QA passed'
                        : 'Validation flagged issues'}
                    </span>
                  </div>
                  <div className="evidence-metrics-grid">
                    {domCheck && (
                      <>
                        <div className="evidence-metric-card">
                          <span className="evidence-metric-label">SVG Elements</span>
                          <span className="evidence-metric-value">
                            {formatScalar('svgCount', domCheck.svgCount)}
                          </span>
                        </div>
                        <div className="evidence-metric-card">
                          <span className="evidence-metric-label">Rendered Marks</span>
                          <span className="evidence-metric-value">
                            {formatScalar('markCount', domCheck.markCount)}
                          </span>
                        </div>
                        <div className="evidence-metric-card">
                          <span className="evidence-metric-label">Controls</span>
                          <span className="evidence-metric-value">
                            {formatScalar('interactiveControls', domCheck.interactiveControls)}
                          </span>
                        </div>
                        <div className="evidence-metric-card">
                          <span className="evidence-metric-label">NaN Attributes</span>
                          <span className="evidence-metric-value">
                            {formatScalar('nanAttrs', domCheck.nanAttrs)}
                          </span>
                        </div>
                      </>
                    )}
                    <div className="evidence-metric-card">
                      <span className="evidence-metric-label">Interaction Tested</span>
                      <span className="evidence-metric-value">
                        {formatScalar('interaction_tested', validationReport.interaction_tested)}
                      </span>
                    </div>
                    <div className="evidence-metric-card">
                      <span className="evidence-metric-label">Preview Captured</span>
                      <span className="evidence-metric-value">
                        {formatScalar('thumbnail_saved', validationReport.thumbnail_saved)}
                      </span>
                    </div>
                  </div>
                </section>
              )}

              {upstreamDeps.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">Upstream Sources</div>
                  <div className="evidence-source-list">
                    {upstreamDeps.map((dep, i) => (
                      <div key={i} className="evidence-source-card">
                        <div className="evidence-source-name">
                          {formatScalar('name', dep.name)}
                        </div>
                        {typeof dep.url === 'string' && dep.url && (
                          <a
                            className="evidence-link"
                            href={dep.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {dep.url}
                          </a>
                        )}
                        {dep.api_total_matching_works !== undefined && (
                          <div className="evidence-source-meta">
                            Matching catalog records:{' '}
                            <strong>{formatScalar('count', dep.api_total_matching_works)}</strong>
                          </div>
                        )}
                        {typeof dep.fallback_reason === 'string' && dep.fallback_reason && (
                          <p className="evidence-source-note">{dep.fallback_reason}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {schemaEntries.length > 0 && (
                <section className="evidence-section">
                  <div className="evidence-section-title">
                    Dataset Schema ({schemaEntries.length} fields)
                  </div>
                  <div className="evidence-schema-list">
                    {schemaEntries.map(([fieldName, rawSpec]) => {
                      const { typeLabel, description } = splitSchemaSpec(rawSpec);
                      return (
                        <div key={fieldName} className="evidence-schema-row">
                          <div className="evidence-schema-head">
                            <code className="evidence-field-name">{fieldName}</code>
                            <span className="evidence-field-type">{typeLabel}</span>
                          </div>
                          {description && <div className="evidence-field-desc">{description}</div>}
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}

              {extraStructured.map(([sectionKey, sectionVal]) => (
                <section key={sectionKey} className="evidence-section">
                  <div className="evidence-section-title">{humanizeKey(sectionKey)}</div>
                  {isPlainRecord(sectionVal) ? (
                    <div className="evidence-kv-list">
                      {Object.entries(sectionVal).map(([subK, subV]) => (
                        <div key={subK} className="evidence-kv-row">
                          <span className="evidence-kv-key">{humanizeKey(subK)}</span>
                          {renderValue(subK, subV)}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="evidence-kv-list">
                      {(Array.isArray(sectionVal) ? sectionVal : [sectionVal]).map((item, idx) => (
                        <div key={idx} className="evidence-kv-row">
                          <span className="evidence-kv-key">#{idx + 1}</span>
                          {isPlainRecord(item) ? (
                            <span className="evidence-kv-val">
                              {Object.entries(item)
                                .map(([ik, iv]) => `${humanizeKey(ik)}: ${formatScalar(ik, iv)}`)
                                .join(' · ')}
                            </span>
                          ) : (
                            renderValue(sectionKey, item)
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              ))}

              {scriptText && (
                <section className="evidence-section">
                  <details className="evidence-script-details">
                    <summary className="evidence-script-summary">
                      Executed Python Script (Sandbox)
                    </summary>
                    <pre className="code-block">{scriptText}</pre>
                  </details>
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
};
