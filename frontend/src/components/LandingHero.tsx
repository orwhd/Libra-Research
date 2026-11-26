import React, { useEffect, useId, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import {
  Sparkles,
  ArrowRight,
  FolderOpen,
  TrendingUp,
  Share2,
  Workflow,
  LayoutGrid,
  GitBranch,
  Layers,
  CircleDot,
  Compass,
  Eye,
  FileText,
  Code2,
  FlaskConical,
  Cpu,
} from 'lucide-react';
import { listWorkspaces, updateLLMSelection } from '../api';
import type {
  LLMSettings,
  RecommendedPaperIconKind,
  RecommendedPaperItem,
  RecommendedPapersResponse,
  ThinkingEffort,
  TrendingChartIconKind,
  TrendingChartItem,
  WeeklyTrendingChartsResponse,
  Workspace,
  WorkspaceMode,
} from '../types';
import { ChartViewPreviewCard } from './ChartViewPreviewCard';
import {
  AttachmentHiddenInputs,
  AttachmentInlineRows,
  AttachmentMenuButton,
  useAttachmentController,
} from './AttachmentControls';
import { ApiConfigModal } from './ApiConfigModal';
import { ModelThinkingSelector } from './ModelThinkingSelector';

interface LandingHeroProps {
  mode: WorkspaceMode;
  onModeChange: (next: WorkspaceMode) => void;
  workspaces: Workspace[];
  submitting: boolean;
  llmSettings: LLMSettings | null;
  trendingCharts: WeeklyTrendingChartsResponse | null;
  trendingLoading: boolean;
  recommendedPapers: RecommendedPapersResponse | null;
  papersLoading: boolean;
  onLLMSettingsChange: (next: LLMSettings) => void;
  onSubmitPrompt: (
    prompt: string,
    uploadId?: string | null,
    modeOverride?: WorkspaceMode
  ) => Promise<void>;
  onSelectWorkspace: (workspaceId: string) => void;
}

const CHART_VIEW_ICONS: Record<TrendingChartIconKind, React.ReactNode> = {
  trend: <TrendingUp size={15} className="example-chip-icon" aria-hidden="true" />,
  network: <Share2 size={15} className="example-chip-icon" aria-hidden="true" />,
  bundling: <Workflow size={15} className="example-chip-icon" aria-hidden="true" />,
  heatmap: <LayoutGrid size={15} className="example-chip-icon" aria-hidden="true" />,
  flow: <GitBranch size={15} className="example-chip-icon" aria-hidden="true" />,
  hierarchy: <Layers size={15} className="example-chip-icon" aria-hidden="true" />,
  scatter: <CircleDot size={15} className="example-chip-icon" aria-hidden="true" />,
  radial: <Compass size={15} className="example-chip-icon" aria-hidden="true" />,
};

const PAPER_VIEW_ICONS: Record<RecommendedPaperIconKind, React.ReactNode> = {
  paper: <FileText size={15} className="example-chip-icon" aria-hidden="true" />,
  code: <Code2 size={15} className="example-chip-icon" aria-hidden="true" />,
  experiment: <FlaskConical size={15} className="example-chip-icon" aria-hidden="true" />,
  model: <Cpu size={15} className="example-chip-icon" aria-hidden="true" />,
};

const EMERGENCY_CHART_ITEMS: TrendingChartItem[] = [
  {
    id: 'trend-1',
    title: 'Streaming Trend Explorer',
    icon_kind: 'trend',
    summary: 'Multi-metric time series with hover crosshair readouts and brush range zoom.',
    interactions: ['Metric Switch', 'Hover Crosshair', 'Brush Zoom'],
    prompt:
      'Visualize temporal trends, growth trajectories, and key inflection points from the data with interactive metric switching and range zoom.',
    source_label: 'Weekly Research Digest',
    source_url: '',
    schematic_svg: '',
  },
  {
    id: 'network-2',
    title: 'Network & Cluster Graph',
    icon_kind: 'network',
    summary: 'Force-directed community clusters with ego-neighborhood highlighting and tie filtering.',
    interactions: ['Ego-Node Hover', 'Drag & Zoom', 'Tie Weight Filter'],
    prompt:
      'Construct an interactive force-directed relationship network from the data, highlighting key hubs, community clusters, and connection weights.',
    source_label: 'Weekly Research Digest',
    source_url: '',
    schematic_svg: '',
  },
  {
    id: 'flow-3',
    title: 'Flow & Ribbon Sankey',
    icon_kind: 'flow',
    summary: 'Weighted stage-to-stage transitions highlighting dominant pathways on ribbon hover.',
    interactions: ['Path Highlight', 'Stage Filter', 'Flow Tooltip'],
    prompt:
      'Build an interactive Sankey flow view revealing proportional stage-to-stage transitions and dominant pathways in the data.',
    source_label: 'Weekly Research Digest',
    source_url: '',
    schematic_svg: '',
  },
  {
    id: 'heatmap-4',
    title: 'Heatmap & Distribution',
    icon_kind: 'heatmap',
    summary: 'Color-encoded category × time matrix paired with sortable marginal distribution bars.',
    interactions: ['Sort Matrix', 'Cell Inspector', 'Marginal Bars'],
    prompt:
      'Create an interactive category-by-time matrix heatmap and distribution breakdown with sorting, filtering, and detailed hover tooltips.',
    source_label: 'Weekly Research Digest',
    source_url: '',
    schematic_svg: '',
  },
];

const EMERGENCY_PAPER_ITEMS: RecommendedPaperItem[] = [
  {
    id: 'rec-paper-1',
    title: 'Generative Agents: Interactive Simulacra',
    full_title: 'Generative Agents: Interactive Simulacra of Human Behavior',
    icon_kind: 'paper',
    year: 2023,
    venue: 'UIST',
    citations: 2400,
    topic_tag: 'HCI & Agents',
    prompt:
      'Reproduce the core method, experiments, and key evaluation results of "Generative Agents: Interactive Simulacra of Human Behavior" (2023) [https://arxiv.org/abs/2304.03442].',
    source_url: 'https://arxiv.org/abs/2304.03442',
  },
  {
    id: 'rec-paper-2',
    title: 'Direct Preference Optimization (DPO)',
    full_title: 'Direct Preference Optimization: Your Language Model is Secretly a Reward Model',
    icon_kind: 'model',
    year: 2023,
    venue: 'NeurIPS',
    citations: 3800,
    topic_tag: 'Foundation Models',
    prompt:
      'Reproduce the core method, experiments, and key evaluation results of "Direct Preference Optimization: Your Language Model is Secretly a Reward Model" (2023) [https://arxiv.org/abs/2305.18290].',
    source_url: 'https://arxiv.org/abs/2305.18290',
  },
  {
    id: 'rec-paper-3',
    title: 'SWE-agent: Agent-Computer Interfaces',
    full_title: 'SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering',
    icon_kind: 'code',
    year: 2024,
    venue: 'NeurIPS',
    citations: 620,
    topic_tag: 'Interactive Agents',
    prompt:
      'Reproduce the core method, experiments, and key evaluation results of "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering" (2024) [https://arxiv.org/abs/2405.15793].',
    source_url: 'https://arxiv.org/abs/2405.15793',
  },
  {
    id: 'rec-paper-4',
    title: 'Data Formulator: AI-Powered Visualization',
    full_title: 'Data Formulator: AI-Powered Concept-Driven Visualization Authoring',
    icon_kind: 'experiment',
    year: 2024,
    venue: 'IEEE TVCG',
    citations: 145,
    topic_tag: 'Visual Analytics',
    prompt:
      'Reproduce the core method, experiments, and key evaluation results of "Data Formulator: AI-Powered Concept-Driven Visualization Authoring" (2024) [https://arxiv.org/abs/2309.10099].',
    source_url: 'https://arxiv.org/abs/2309.10099',
  },
];

const DATA_PLACEHOLDER_TEXT = 'Describe your question, or pick a chart view below...';
const REPRODUCE_PLACEHOLDER_TEXT = 'Paste a paper title, link, or pick a hot paper below...';

export const LandingHero: React.FC<LandingHeroProps> = ({
  mode,
  onModeChange,
  workspaces,
  submitting,
  llmSettings,
  trendingCharts,
  trendingLoading,
  recommendedPapers,
  papersLoading,
  onLLMSettingsChange,
  onSubmitPrompt,
  onSelectWorkspace,
}) => {
  const [promptsByMode, setPromptsByMode] = useState<Record<WorkspaceMode, string>>({
    data: '',
    reproduce: '',
  });
  const [freshWorkspaces, setFreshWorkspaces] = useState<Workspace[]>(workspaces);
  const [activePreviewId, setActivePreviewId] = useState<string | null>(null);
  const [apiModalOpen, setApiModalOpen] = useState(false);

  const prompt = promptsByMode[mode];
  const setPrompt = (val: string) => {
    setPromptsByMode((prev) => ({ ...prev, [mode]: val }));
  };

  const att = useAttachmentController(submitting);
  const promptTextareaRef = useRef<HTMLTextAreaElement>(null);
  const badgeBtnRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const modeSwitchBtnRef = useRef<HTMLButtonElement>(null);
  const closePreviewTimerRef = useRef<number | null>(null);
  const basePreviewId = useId();

  const applyConfigWithSmartTransition = (next: LLMSettings) => {
    flushSync(() => {
      setApiModalOpen(false);
    });

    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const docWithVT = document as Document & {
      startViewTransition?: (cb: () => void) => { finished: Promise<void> };
    };

    const badgeEl = badgeBtnRef.current;
    const titleEl = headingRef.current;

    if (reducedMotion || !docWithVT.startViewTransition || !badgeEl) {
      onLLMSettingsChange(next);
      badgeBtnRef.current?.focus();
      return;
    }

    badgeEl.setAttribute('data-vt-morph', '1');
    badgeEl.style.viewTransitionName = 'libra-brand-badge';
    if (titleEl) {
      titleEl.style.viewTransitionName = 'landing-hero-heading';
    }

    const transition = docWithVT.startViewTransition(() => {
      flushSync(() => {
        onLLMSettingsChange(next);
      });
    });

    transition.finished.finally(() => {
      if (badgeBtnRef.current) {
        badgeBtnRef.current.style.viewTransitionName = '';
        badgeBtnRef.current.focus();
      }
      if (headingRef.current) {
        headingRef.current.style.viewTransitionName = '';
      }
    });
  };

  const handleSwitchMode = (nextMode: WorkspaceMode) => {
    if (nextMode === mode) return;
    setActivePreviewId(null);

    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const docWithVT = document as Document & {
      startViewTransition?: (cb: () => void) => { finished: Promise<void> };
    };
    const titleEl = headingRef.current;

    if (reducedMotion || !docWithVT.startViewTransition || !titleEl) {
      onModeChange(nextMode);
      requestAnimationFrame(() => modeSwitchBtnRef.current?.focus({ preventScroll: true }));
      return;
    }

    titleEl.style.viewTransitionName = 'landing-hero-heading';
    const transition = docWithVT.startViewTransition(() => {
      flushSync(() => {
        onModeChange(nextMode);
      });
    });

    transition.finished.finally(() => {
      if (headingRef.current) {
        headingRef.current.style.viewTransitionName = '';
      }
      modeSwitchBtnRef.current?.focus({ preventScroll: true });
    });
  };

  useEffect(() => {
    setFreshWorkspaces(workspaces);
  }, [workspaces]);

  useEffect(() => {
    let active = true;
    let retryTimer: number | null = null;

    const load = async (allowRetry: boolean) => {
      try {
        const list = await listWorkspaces();
        if (!active) return;
        setFreshWorkspaces(list);
      } catch {
        if (active && allowRetry) {
          retryTimer = window.setTimeout(() => void load(false), 1200);
        }
      }
    };

    void load(true);
    return () => {
      active = false;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    if (activePreviewId === null) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setActivePreviewId(null);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [activePreviewId]);

  useEffect(() => {
    return () => {
      if (closePreviewTimerRef.current !== null) {
        window.clearTimeout(closePreviewTimerRef.current);
      }
    };
  }, []);

  const openPreview = (id: string) => {
    if (closePreviewTimerRef.current !== null) {
      window.clearTimeout(closePreviewTimerRef.current);
      closePreviewTimerRef.current = null;
    }
    setActivePreviewId(id);
  };

  const scheduleClosePreview = (id: string) => {
    if (closePreviewTimerRef.current !== null) {
      window.clearTimeout(closePreviewTimerRef.current);
    }
    closePreviewTimerRef.current = window.setTimeout(() => {
      setActivePreviewId((cur) => (cur === id ? null : cur));
      closePreviewTimerRef.current = null;
    }, 90);
  };

  const handleSelectChartPrompt = (chartPrompt: string) => {
    setActivePreviewId(null);
    setPrompt(chartPrompt);
    if (!att.attached && !att.urlInputOpen) {
      att.setAttachMenuOpen(true);
    } else {
      requestAnimationFrame(() => promptTextareaRef.current?.focus());
    }
  };

  const handleSelectPaperPrompt = (paperPrompt: string) => {
    setPrompt(paperPrompt);
    requestAnimationFrame(() => promptTextareaRef.current?.focus());
  };

  const handleQuickSelection = async (patch: {
    model?: string;
    thinking_effort?: ThinkingEffort;
  }) => {
    if (llmSettings) {
      onLLMSettingsChange({
        ...llmSettings,
        model: patch.model ?? llmSettings.model,
        thinking_effort: patch.thinking_effort ?? llmSettings.thinking_effort,
      });
    }
    try {
      const updated = await updateLLMSelection(patch);
      onLLMSettingsChange(updated);
    } catch {
      /* keep optimistic selection in state if transient error */
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || att.uploading) return;
    const trimmed = prompt.trim();
    const canAttachSubmit = mode === 'reproduce' && Boolean(att.attached);
    if (!trimmed && !canAttachSubmit) return;
    const effectivePrompt =
      trimmed ||
      `Reproduce the attached paper (${att.attached?.label || 'uploaded PDF'}) and verify its core claims through multi-round experiments.`;
    await onSubmitPrompt(effectivePrompt, att.attached?.upload_id ?? null, mode);
  };

  const placeholderText =
    mode === 'reproduce' ? REPRODUCE_PLACEHOLDER_TEXT : DATA_PLACEHOLDER_TEXT;

  const filteredWorkspaces = freshWorkspaces.filter(
    (ws) => (ws.workspace_type ?? 'data') === mode
  );
  const displayedWorkspaces = filteredWorkspaces.slice(0, 12);

  const paperItems = recommendedPapers?.items?.length
    ? recommendedPapers.items
    : EMERGENCY_PAPER_ITEMS;

  return (
    <main className="landing-shell">
      <header className="landing-hero-top">
        <button
          ref={badgeBtnRef}
          type="button"
          className={`landing-badge ${llmSettings?.configured ? 'is-configured' : ''}`}
          aria-label={
            llmSettings?.configured
              ? `Libra Research — API configured (${llmSettings.model}). Click to edit API configuration`
              : 'Libra Research — Click to open API configuration'
          }
          title={
            llmSettings?.configured
              ? `API Ready · ${llmSettings.model} (Click to configure API)`
              : 'Click to configure API'
          }
          onClick={() => setApiModalOpen(true)}
        >
          <Sparkles
            size={llmSettings?.configured ? 19 : 16}
            className="landing-badge-sparkle"
            aria-hidden="true"
          />
          <span>Libra Research</span>
        </button>
        {llmSettings === null ? (
          <span className="api-config-loading-slot" aria-hidden="true" />
        ) : !llmSettings.configured ? (
          <button
            type="button"
            className="api-config-prompt-link"
            onClick={() => setApiModalOpen(true)}
          >
            click me to complete API config!
          </button>
        ) : null}
        <h1 ref={headingRef} tabIndex={-1} className="landing-title">
          {mode === 'data' ? (
            <>
              Ask Any Questions of{' '}
              <button
                ref={modeSwitchBtnRef}
                type="button"
                className="landing-title-switch"
                data-testid="landing-mode-switch"
                aria-label="Data — Click to switch to Paper Reproduction"
                onClick={() => handleSwitchMode('reproduce')}
              >
                Data
              </button>
            </>
          ) : (
            <>
              Upload Papers to{' '}
              <button
                ref={modeSwitchBtnRef}
                type="button"
                className="landing-title-switch"
                data-testid="landing-mode-switch"
                aria-label="Reproduce — Click to switch to Data Questions"
                onClick={() => handleSwitchMode('data')}
              >
                Reproduce
              </button>
            </>
          )}
        </h1>
      </header>

      <section className="landing-hero-center">
        <form className="prompt-form" onSubmit={handleSubmit}>
          <div className="prompt-sizer">
            <div className="prompt-mirror" aria-hidden="true">
              {(prompt || placeholderText) + '\n'}
            </div>
            <textarea
              ref={promptTextareaRef}
              className="prompt-textarea"
              aria-label="Research prompt"
              placeholder={placeholderText}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={submitting}
            />
          </div>

          <AttachmentHiddenInputs controller={att} />
          <AttachmentInlineRows controller={att} disabled={submitting} />

          <div className="prompt-actions">
            <AttachmentMenuButton
              controller={att}
              disabled={submitting || att.uploading}
              dropDirection="down"
            />

            <div className="prompt-actions-right">
              <ModelThinkingSelector
                settings={llmSettings}
                disabled={submitting}
                onChangeSelection={handleQuickSelection}
                onOpenConfig={() => setApiModalOpen(true)}
              />

              <button
                type="submit"
                className="btn-primary landing-submit-btn"
                disabled={
                  submitting ||
                  att.uploading ||
                  (!prompt.trim() && !(mode === 'reproduce' && Boolean(att.attached)))
                }
              >
                {submitting ? 'Running...' : 'Run'}
                <ArrowRight size={15} />
              </button>
            </div>
          </div>
        </form>
      </section>

      <section className="landing-hero-bottom">
        <div className="examples-grid">
          {mode === 'data' ? (
            trendingLoading && !trendingCharts ? (
              [0, 1, 2, 3].map((slot) => (
                <div
                  key={slot}
                  className="example-chip-skeleton"
                  aria-hidden="true"
                />
              ))
            ) : (
              (trendingCharts?.items?.length ? trendingCharts.items : EMERGENCY_CHART_ITEMS).map(
                (ex) => {
                  const previewId = `${basePreviewId}-${ex.id}`;
                  const isOpen = activePreviewId === ex.id;
                  return (
                    <div
                      key={ex.id}
                      className={`example-chip-wrap ${isOpen ? 'is-preview-open' : ''}`}
                      onPointerEnter={() => openPreview(ex.id)}
                      onPointerLeave={() => scheduleClosePreview(ex.id)}
                    >
                      <button
                        type="button"
                        className={`example-chip ${prompt.trim() === ex.prompt ? 'is-selected' : ''}`}
                        aria-describedby={previewId}
                        onFocus={() => openPreview(ex.id)}
                        onBlur={() => scheduleClosePreview(ex.id)}
                        onClick={() => handleSelectChartPrompt(ex.prompt)}
                      >
                        <span className="example-chip-main">
                          {CHART_VIEW_ICONS[ex.icon_kind] ?? CHART_VIEW_ICONS.trend}
                          <span className="example-chip-title">{ex.title}</span>
                        </span>
                        <Eye size={13} className="example-chip-hint" aria-hidden="true" />
                      </button>

                      <ChartViewPreviewCard
                        id={previewId}
                        item={ex}
                        isoWeek={trendingCharts?.iso_week}
                        visible={isOpen}
                      />
                    </div>
                  );
                }
              )
            )
          ) : papersLoading && !recommendedPapers ? (
            [0, 1, 2, 3].map((slot) => (
              <div
                key={slot}
                className="example-chip-skeleton"
                aria-hidden="true"
              />
            ))
          ) : (
            paperItems.map((paper) => (
              <div key={paper.id} className="example-chip-wrap">
                <button
                  type="button"
                  className={`example-chip ${prompt.trim() === paper.prompt ? 'is-selected' : ''}`}
                  title={`${paper.full_title}${paper.year ? ` (${paper.year})` : ''}${paper.venue ? ` · ${paper.venue}` : ''}`}
                  onClick={() => handleSelectPaperPrompt(paper.prompt)}
                >
                  <span className="example-chip-main">
                    {PAPER_VIEW_ICONS[paper.icon_kind] ?? PAPER_VIEW_ICONS.paper}
                    <span className="example-chip-title">{paper.title}</span>
                  </span>
                  {paper.year ? (
                    <span className="example-chip-year" aria-hidden="true">
                      {paper.year}
                    </span>
                  ) : null}
                </button>
              </div>
            ))
          )}
        </div>

        {displayedWorkspaces.length > 0 && (
          <div className="recent-workspaces">
            <h2 className="recent-workspaces-title">Existing Workspaces</h2>
            <div className="workspace-pill-list">
              {displayedWorkspaces.map((ws) => (
                <button
                  key={ws.workspace_id}
                  type="button"
                  className="btn-secondary workspace-pill-item"
                  title={ws.title}
                  onClick={() => onSelectWorkspace(ws.workspace_id)}
                >
                  <FolderOpen size={14} className="workspace-pill-icon" aria-hidden="true" />
                  <span className="workspace-pill-title">{ws.title}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <ApiConfigModal
        open={apiModalOpen}
        settings={llmSettings}
        onSaved={(next) => {
          applyConfigWithSmartTransition(next);
        }}
        onReset={(next) => {
          applyConfigWithSmartTransition(next);
        }}
        onClose={() => setApiModalOpen(false)}
      />
    </main>
  );
};
