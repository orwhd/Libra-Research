import React, { useEffect, useId, useRef, useState } from 'react';
import {
  Sparkles,
  ArrowRight,
  FolderOpen,
  TrendingUp,
  Share2,
  Workflow,
  LayoutGrid,
  Eye,
} from 'lucide-react';
import { listWorkspaces } from '../api';
import type { Workspace } from '../types';
import {
  CHART_VIEW_ITEMS,
  ChartViewPreviewCard,
  type ChartViewId,
} from './ChartViewPreviewCard';
import {
  AttachmentHiddenInputs,
  AttachmentInlineRows,
  AttachmentMenuButton,
  useAttachmentController,
} from './AttachmentControls';

interface LandingHeroProps {
  workspaces: Workspace[];
  submitting: boolean;
  onSubmitPrompt: (prompt: string, uploadId?: string | null) => Promise<void>;
  onSelectWorkspace: (workspaceId: string) => void;
  onOpenWorkspaceByTitle: (title: string) => Promise<void>;
}

const CHART_VIEW_ICONS: Record<ChartViewId, React.ReactNode> = {
  trend: <TrendingUp size={15} className="example-chip-icon" aria-hidden="true" />,
  network: <Share2 size={15} className="example-chip-icon" aria-hidden="true" />,
  bundling: <Workflow size={15} className="example-chip-icon" aria-hidden="true" />,
  heatmap: <LayoutGrid size={15} className="example-chip-icon" aria-hidden="true" />,
};

const FALLBACK_PLACEHOLDER_WORKSPACES = [
  'Phone Market in America',
  'UI Trends',
  'Hot topic in HCI in 2026 fall',
];

const PLACEHOLDER_TEXT = 'Describe your question, or pick a chart view below...';

export const LandingHero: React.FC<LandingHeroProps> = ({
  workspaces,
  submitting,
  onSubmitPrompt,
  onSelectWorkspace,
  onOpenWorkspaceByTitle,
}) => {
  const [prompt, setPrompt] = useState('');
  const [freshWorkspaces, setFreshWorkspaces] = useState<Workspace[]>(workspaces);
  const [activePreviewId, setActivePreviewId] = useState<ChartViewId | null>(null);

  const att = useAttachmentController(submitting);
  const promptTextareaRef = useRef<HTMLTextAreaElement>(null);
  const closePreviewTimerRef = useRef<number | null>(null);
  const basePreviewId = useId();

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
        if (list.length > 0) {
          setFreshWorkspaces(list);
          return;
        }
        if (allowRetry) {
          retryTimer = window.setTimeout(() => void load(false), 1200);
        }
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

  const openPreview = (id: ChartViewId) => {
    if (closePreviewTimerRef.current !== null) {
      window.clearTimeout(closePreviewTimerRef.current);
      closePreviewTimerRef.current = null;
    }
    setActivePreviewId(id);
  };

  const scheduleClosePreview = (id: ChartViewId) => {
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || submitting || att.uploading) return;
    await onSubmitPrompt(prompt.trim(), att.attached?.upload_id ?? null);
  };

  const displayedWorkspaces = freshWorkspaces.slice(0, 3);

  return (
    <main className="landing-shell">
      <header className="landing-hero-top">
        <div className="landing-badge">
          <Sparkles size={16} />
          Libra Research
        </div>
        <h1 className="landing-title">Ask Any Questions of Data</h1>
      </header>

      <section className="landing-hero-center">
        <form className="prompt-form" onSubmit={handleSubmit}>
          <div className="prompt-sizer">
            <div className="prompt-mirror" aria-hidden="true">
              {(prompt || PLACEHOLDER_TEXT) + '\n'}
            </div>
            <textarea
              ref={promptTextareaRef}
              className="prompt-textarea"
              aria-label="Research prompt"
              placeholder={PLACEHOLDER_TEXT}
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

            <button
              type="submit"
              className="btn-primary landing-submit-btn"
              disabled={submitting || att.uploading || !prompt.trim()}
            >
              {submitting ? 'Running...' : 'Run'}
              <ArrowRight size={15} />
            </button>
          </div>
        </form>
      </section>

      <section className="landing-hero-bottom">
        <div className="examples-grid">
          {CHART_VIEW_ITEMS.map((ex) => {
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
                    {CHART_VIEW_ICONS[ex.id]}
                    <span className="example-chip-title">{ex.title}</span>
                  </span>
                  <Eye size={13} className="example-chip-hint" aria-hidden="true" />
                </button>

                <ChartViewPreviewCard id={previewId} item={ex} visible={isOpen} />
              </div>
            );
          })}
        </div>

        <div className="recent-workspaces">
          <h2 className="recent-workspaces-title">Existing Workspaces</h2>
          <div className="workspace-pill-list">
            {displayedWorkspaces.length > 0
              ? displayedWorkspaces.map((ws) => (
                  <button
                    key={ws.workspace_id}
                    type="button"
                    className="btn-secondary"
                    onClick={() => onSelectWorkspace(ws.workspace_id)}
                  >
                    <FolderOpen size={14} />
                    {ws.title}
                  </button>
                ))
              : FALLBACK_PLACEHOLDER_WORKSPACES.map((title) => (
                  <button
                    key={title}
                    type="button"
                    className="btn-secondary"
                    onClick={() => void onOpenWorkspaceByTitle(title)}
                  >
                    <FolderOpen size={14} />
                    {title}
                  </button>
                ))}
          </div>
        </div>
      </section>
    </main>
  );
};
