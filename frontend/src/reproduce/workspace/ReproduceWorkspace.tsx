import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  CheckCircle2,
  Download,
  Eye,
  FileCode2,
  FileText,
  Mail,
  Paperclip,
  Pause,
  Plus,
  X,
} from 'lucide-react';
import type { LLMSettings, ThinkingEffort } from '../../types';
import { updateLLMSelection } from '../../api';
import { ApiConfigModal } from '../../components/ApiConfigModal';
import { MarkdownMath } from '../../components/MarkdownMath';
import { ModelThinkingSelector } from '../../components/ModelThinkingSelector';
import { WaveProgressText } from '../../components/WaveProgressText';
import { formatShortContextChip, toPlainUiText } from '../plainText';
import {
  bindWorkspaceReproduceRun,
  cancelReproduceRun,
  getReproduceRun,
  getReproduceSnapshot,
  getReproduceTimeline,
  importUploadToRun,
  listReproduceArtifactsDetail,
  listReproduceMessages,
  submitReproduceChat,
  subscribeReproduceEvents,
  uploadRunAttachment,
} from '../api';
import {
  applyHarnessEventToProjection,
  createInitialProjectedState,
  hydrateFromCanvasProjection,
  type ActiveTableVisual,
  type ProjectedCanvasState,
} from '../canvas/CollaborationProjection';
import { LivingResearchCanvas } from '../canvas/LivingResearchCanvas';
import { InChatDocumentViewer } from '../chat/InChatDocumentViewer';
import type {
  ChatActivityMessage,
  HarnessEvent,
  HumanInterventionRecord,
  Persona,
  ReproduceArtifact,
  ReproduceRun,
  RunSnapshot,
  RunTimelineResponse,
  SelectedContext,
  TimelineSnapshotNode,
  UploadReceipt,
} from '../types';

interface PendingInitialLaunch {
  prompt: string;
  uploadId?: string | null;
  requestId: string;
}

interface ReproduceWorkspaceProps {
  workspaceId: string;
  workspaceTitle: string;
  sidebarWidth: number;
  isResizing: boolean;
  llmSettings: LLMSettings | null;
  pendingLaunch: PendingInitialLaunch | null;
  onConsumePendingLaunch: () => void;
  onLLMSettingsChange: (next: LLMSettings) => void;
  onNewWorkspace: () => void;
  onResizerPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
  onResizerPointerMove: (e: React.PointerEvent<HTMLDivElement>) => void;
  onResizerPointerUp: (e: React.PointerEvent<HTMLDivElement>) => void;
  onResizerDoubleClick: () => void;
  onResizerKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => void;
}

function pickLiveStageTicker(activityItems: ChatActivityMessage[]): string {
  for (let i = activityItems.length - 1; i >= 0; i -= 1) {
    const t = activityItems[i].text.toLowerCase();
    if (t.includes('report') || t.includes('pdf') || t.includes('docx')) {
      return 'Compiling final PDF report...';
    }
    if (t.includes('evidence') || t.includes('audit') || t.includes('validation') || t.includes('debate')) {
      return 'Validating experimental evidence...';
    }
    if (t.includes('round 2') || t.includes('ablation') || t.includes('analysis #3')) {
      return 'Running Round 2 robustness checks...';
    }
    if (t.includes('round 1') || t.includes('analysis') || t.includes('experiment')) {
      return 'Running reproduction experiments...';
    }
    if (t.includes('data') || t.includes('manifest') || t.includes('dossier')) {
      return 'Preparing data & benchmark setup...';
    }
    if (t.includes('paper')) {
      return 'Analyzing paper & methodology...';
    }
  }
  return 'Analyzing paper & coordinating experiments...';
}

function formatToolEventTicker(ev: HarnessEvent): string {
  const actorRaw = (ev.actor_id || 'agent').replace(/^persona_/, '').replace(/_/g, ' #');
  const actorLabel = actorRaw.charAt(0).toUpperCase() + actorRaw.slice(1);
  const tool = String((ev.payload as { tool?: unknown })?.tool || '');
  if (tool === 'run_python_experiment') return `${actorLabel} executing Python experiment...`;
  if (tool === 'register_paper_spec') return `${actorLabel} registering paper specification...`;
  if (tool === 'register_data_manifest') return `${actorLabel} verifying data manifest...`;
  if (tool === 'fetch_public_dataset') return `${actorLabel} fetching public dataset...`;
  if (tool === 'record_experiment_finding') return `${actorLabel} recording quantitative finding...`;
  if (tool === 'inspect_workspace_files') return `${actorLabel} inspecting paper & workspace files...`;
  return `${actorLabel} working...`;
}

export const ReproduceWorkspace: React.FC<ReproduceWorkspaceProps> = ({
  workspaceId,
  workspaceTitle,
  sidebarWidth,
  isResizing,
  llmSettings,
  pendingLaunch,
  onConsumePendingLaunch,
  onLLMSettingsChange,
  onNewWorkspace,
  onResizerPointerDown,
  onResizerPointerMove,
  onResizerPointerUp,
  onResizerDoubleClick,
  onResizerKeyDown,
}) => {
  const [run, setRun] = useState<ReproduceRun | null>(null);
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [projected, setProjected] = useState<ProjectedCanvasState>(() =>
    createInitialProjectedState()
  );
  const [messages, setMessages] = useState<ChatActivityMessage[]>([]);
  const [artifacts, setArtifacts] = useState<ReproduceArtifact[]>([]);
  const [timeline, setTimeline] = useState<RunTimelineResponse | null>(null);

  const [prompt, setPrompt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [liveToolHint, setLiveToolHint] = useState<string | null>(null);
  const [selectedContext, setSelectedContext] = useState<{
    ctx: SelectedContext;
    label: string;
  } | null>(null);
  const [silentFilterNotice, setSilentFilterNotice] = useState<string | null>(null);
  const [highlightedMsgIds, setHighlightedMsgIds] = useState<Set<string>>(new Set());
  const [highlightedStepIds, setHighlightedStepIds] = useState<Set<string>>(new Set());
  const [expandedStepFiles, setExpandedStepFiles] = useState<Set<string>>(new Set());
  const [expandedStepDebates, setExpandedStepDebates] = useState<Set<string>>(new Set());
  const [activeSnapshotId, setActiveSnapshotId] = useState<string | null>(null);

  const [uploadedAttachments, setUploadedAttachments] = useState<UploadReceipt[]>([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);

  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const [replayProjected, setReplayProjected] = useState<ProjectedCanvasState | null>(null);

  const [viewerArtifactId, setViewerArtifactId] = useState<string | null>(null);
  const [apiModalOpen, setApiModalOpen] = useState(false);
  const [mobileTab, setMobileTab] = useState<'chat' | 'canvas'>('chat');

  const chatScrollRef = useRef<HTMLDivElement>(null);
  const savedScrollTopRef = useRef<number>(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
  const lastCompositionEndAtRef = useRef<number | null>(null);
  const launchedKeyRef = useRef<string | null>(null);
  const forceScrollBottomRef = useRef<boolean>(false);

  const syncAllReadModels = useCallback(async (runId: string) => {
    const [r, snap, msgPage, arts, tl] = await Promise.all([
      getReproduceRun(runId),
      getReproduceSnapshot(runId),
      listReproduceMessages(runId, { limit: 100 }),
      listReproduceArtifactsDetail(runId),
      getReproduceTimeline(runId).catch(() => null),
    ]);
    setRun(r);
    setSnapshot(snap);
    setProjected(hydrateFromCanvasProjection(snap.canvas, r.run_id, r.mode));
    setMessages(msgPage.messages);
    setArtifacts(arts);
    if (tl) {
      setTimeline(tl);
    }
    return { r, snap };
  }, []);

  // Bind workspace to ReproduceRun without starting paid work (`L1.5 §2`)
  useEffect(() => {
    let active = true;

    (async () => {
      try {
        const bound = await bindWorkspaceReproduceRun(workspaceId, workspaceTitle, 'live');
        if (!active) return;
        await syncAllReadModels(bound.run_id);
      } catch {
        /* keep initial static 6-persona roster visible even if offline */
      }
    })();

    return () => {
      active = false;
    };
  }, [workspaceId, workspaceTitle, syncAllReadModels]);

  // Subscribe to live SSE ONLY while run is active (`running` or `paused` awaiting human input)
  const activeRunId = run?.run_id ?? null;
  const activeRunStatus = run?.status ?? null;
  const currentProjectedSeq = projected.atSeq;
  useEffect(() => {
    if (!activeRunId || (activeRunStatus !== 'running' && activeRunStatus !== 'paused')) {
      return undefined;
    }
    const unsub = subscribeReproduceEvents(activeRunId, currentProjectedSeq, {
      onEvent: (ev) => {
        setProjected((prev) => applyHarnessEventToProjection(prev, ev));
        if (ev.type === 'tool.executed') {
          setLiveToolHint(formatToolEventTicker(ev));
        }
        if (ev.type === 'run.completed' || ev.type === 'run.failed' || ev.type === 'debate.human_intervention_requested') {
          setLiveToolHint(null);
        }
        const nextPersonaState =
          ev.type === 'agent.status_changed'
            ? String((ev.payload as { next_state?: unknown })?.next_state || '')
            : '';
        if (
          ev.type === 'agent.spawned' ||
          nextPersonaState === 'reporting' ||
          nextPersonaState === 'handoff' ||
          ev.type === 'task.started' ||
          ev.type === 'task.completed' ||
          ev.type === 'artifact.created' ||
          ev.type === 'report.generated' ||
          ev.type === 'evidence.reviewed' ||
          ev.type === 'debate.turn_added' ||
          ev.type === 'debate.human_intervention_requested' ||
          ev.type === 'debate.human_intervention_resolved' ||
          ev.type === 'collaboration.started' ||
          ev.type === 'collaboration.members_changed' ||
          ev.type === 'collaboration.ended' ||
          ev.type === 'run.completed' ||
          ev.type === 'run.failed'
        ) {
          void syncAllReadModels(activeRunId);
        }
      },
      onGapDetected: () => {
        void syncAllReadModels(activeRunId);
      },
    });
    return () => {
      unsub();
    };
  }, [activeRunId, activeRunStatus, syncAllReadModels]);

  // Execute pending prompt & optional homepage upload submitted from Reproduce Homepage (`LandingHero`)
  useEffect(() => {
    if (!run || !pendingLaunch) return;
    const key = `${run.run_id}:${pendingLaunch.requestId}`;
    if (launchedKeyRef.current === key) return;
    launchedKeyRef.current = key;
    onConsumePendingLaunch();

    void (async () => {
      setSubmitting(true);
      setUploadErr(null);
      try {
        let attachmentIds: string[] = [];
        if (pendingLaunch.uploadId) {
          try {
            const imported = await importUploadToRun(run.run_id, pendingLaunch.uploadId);
            attachmentIds = imported.map((r) => r.artifact_id);
          } catch (err) {
            setUploadErr(err instanceof Error ? err.message : String(err));
          }
        }
        await submitReproduceChat(run.run_id, {
          request_id: pendingLaunch.requestId,
          text: pendingLaunch.prompt,
          selected_context: null,
          attachment_ids: attachmentIds,
        });
        await syncAllReadModels(run.run_id);
      } finally {
        setSubmitting(false);
      }
    })();
  }, [run, pendingLaunch, onConsumePendingLaunch, syncAllReadModels]);

  // Auto-scroll chat when new messages arrive (only when not viewing a document)
  useEffect(() => {
    if (viewerArtifactId) return;
    const el = chatScrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (forceScrollBottomRef.current || dist < 240) {
      forceScrollBottomRef.current = false;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [messages.length, artifacts.length, viewerArtifactId]);

  const artifactsById = useMemo(
    () => new Map(artifacts.map((a) => [a.artifact_id, a])),
    [artifacts]
  );

  // Final deliverables: final generated PDF report + reproducible scripts
  const finalPdfReports = useMemo(() => {
    const seen = new Set<string>();
    const pdfs: ReproduceArtifact[] = [];
    for (const a of artifacts) {
      if (seen.has(a.artifact_id)) continue;
      const isReportPdf =
        a.kind === 'report' &&
        (a.mime_type === 'application/pdf' || (a.filename || '').toLowerCase().endsWith('.pdf'));
      if (isReportPdf) {
        seen.add(a.artifact_id);
        pdfs.push(a);
      }
    }
    return pdfs.slice(-1);
  }, [artifacts]);

  const reproducibleScripts = useMemo(() => {
    const seen = new Set<string>();
    const list: ReproduceArtifact[] = [];
    for (const a of artifacts) {
      if (seen.has(a.artifact_id)) continue;
      if (a.kind === 'script') {
        seen.add(a.artifact_id);
        list.push(a);
      }
    }
    return list;
  }, [artifacts]);

  /**
   * Silent Canvas -> Chat navigation (`L1.5 §5` & `§8.3`):
   * NEVER triggers any Agent reaction or POSTs `/chat`. Only scrolls/highlights existing Chat items!
   */
  const handleSilentNavigateRef = useCallback(
    (ctx: SelectedContext, label: string) => {
      setViewerArtifactId(null);
      const matchedIds: string[] = [];
      for (const m of messages) {
        if (m.role !== 'activity' && m.related_refs.some((r) => r.kind === ctx.kind && r.id === ctx.id)) {
          matchedIds.push(m.message_id);
        }
      }
      setSelectedContext({ ctx, label });
      if (matchedIds.length > 0) {
        setHighlightedMsgIds(new Set(matchedIds));
        setSilentFilterNotice(null);
        requestAnimationFrame(() => {
          const targetId = matchedIds[matchedIds.length - 1];
          const domNode = chatScrollRef.current?.querySelector<HTMLElement>(
            `[data-msg-id="${targetId}"]`
          );
          domNode?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      } else {
        setHighlightedMsgIds(new Set());
        setSilentFilterNotice(`No recorded activity for ${label} yet.`);
      }
    },
    [messages]
  );

  const handleSilentSelectTable = useCallback(
    (table: ActiveTableVisual) => {
      handleSilentNavigateRef(
        { kind: 'collaboration', id: table.collaboration_id },
        `Table: ${table.topic || table.collaboration_id}`
      );
    },
    [handleSilentNavigateRef]
  );

  const handleSilentSelectBriefing = useCallback(
    (briefing: { presenterIds: string[]; topic: string; linkedStepIds?: string[] }) => {
      setViewerArtifactId(null);
      const linked = briefing.linkedStepIds || [];
      if (linked.length > 0) {
        setHighlightedStepIds(new Set(linked));
        requestAnimationFrame(() => {
          const domNode = chatScrollRef.current?.querySelector<HTMLElement>(
            `[data-step-id="${linked[0]}"]`
          );
          domNode?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      }
      const primaryPresenter = briefing.presenterIds[0] || 'persona_main';
      setSelectedContext({
        ctx: { kind: 'agent', id: primaryPresenter },
        label: `Briefing: ${briefing.topic}`,
      });
    },
    []
  );

  const handleCancelRun = useCallback(async () => {
    if (!run || cancelling) return;
    setCancelling(true);
    try {
      await cancelReproduceRun(run.run_id);
      setLiveToolHint(null);
      await syncAllReadModels(run.run_id);
    } finally {
      setCancelling(false);
    }
  }, [run, cancelling, syncAllReadModels]);

  const handleOpenArtifactViewer = (artifactId: string) => {
    if (chatScrollRef.current) {
      savedScrollTopRef.current = chatScrollRef.current.scrollTop;
    }
    setViewerArtifactId(artifactId);
  };

  const handleCloseArtifactViewer = () => {
    setViewerArtifactId(null);
    requestAnimationFrame(() => {
      if (chatScrollRef.current) {
        chatScrollRef.current.scrollTop = savedScrollTopRef.current;
      }
    });
  };

  const handleExitReplay = () => {
    setReplaySeq(null);
    setReplayProjected(null);
    setActiveSnapshotId(null);
    setHighlightedStepIds(new Set());
  };

  /**
   * Canvas Bottom Timeline -> Left Chat Spine linkage (100% read-only GET):
   * Jumps Canvas to snapshot `seq`, highlights linked vertical spine nodes, and expands debate/files context.
   */
  const handleSelectTimelineSnapshot = useCallback(
    async (snapNode: TimelineSnapshotNode) => {
      if (!run) return;
      setViewerArtifactId(null);
      setActiveSnapshotId(snapNode.snapshot_id);
      setReplaySeq(snapNode.seq);
      const snapAt = await getReproduceSnapshot(run.run_id, snapNode.seq);
      setReplayProjected(hydrateFromCanvasProjection(snapAt.canvas, run.run_id, run.mode));

      const linkedIds = snapNode.linked_step_ids || [];
      setHighlightedStepIds(new Set(linkedIds));
      if (linkedIds.length > 0) {
        if (snapNode.kind.startsWith('huddle_') || snapNode.collaboration_id) {
          setExpandedStepDebates((prev) => {
            const next = new Set(prev);
            for (const sid of linkedIds) next.add(sid);
            return next;
          });
          setExpandedStepFiles((prev) => {
            const next = new Set(prev);
            for (const sid of linkedIds) next.add(sid);
            return next;
          });
        }
        requestAnimationFrame(() => {
          const domNode = chatScrollRef.current?.querySelector<HTMLElement>(
            `[data-step-id="${linkedIds[0]}"]`
          );
          domNode?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      }
    },
    [run]
  );

  const toggleStepFiles = (stepId: string) => {
    setExpandedStepFiles((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  const toggleStepDebate = (stepId: string) => {
    setExpandedStepDebates((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  const handleAttachmentPick = async (files: FileList | null) => {
    if (!files || files.length === 0 || !run) return;
    setUploadErr(null);
    setUploadingFile(true);
    try {
      const receipts: UploadReceipt[] = [];
      for (const f of Array.from(files).slice(0, 4)) {
        const rec = await uploadRunAttachment(run.run_id, f);
        receipts.push(rec);
      }
      setUploadedAttachments((prev) => [...prev, ...receipts]);
    } catch (err) {
      setUploadErr(err instanceof Error ? err.message : String(err));
    } finally {
      setUploadingFile(false);
    }
  };

  const handleSendChat = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = prompt.trim();
    if ((!trimmed && uploadedAttachments.length === 0) || !run || submitting || uploadingFile) return;

    const reqId = `req_${crypto.randomUUID().slice(0, 12)}`;
    const attIds = uploadedAttachments.map((u) => u.artifact_id);
    const ctxPayload = selectedContext?.ctx ?? null;
    const effectiveText =
      trimmed || 'Reproduce and verify the attached paper/files through multi-round experiments.';

    setPrompt('');
    setUploadedAttachments([]);
    setSilentFilterNotice(null);
    setLiveToolHint(null);
    forceScrollBottomRef.current = true;
    handleExitReplay();
    setSubmitting(true);
    try {
      await submitReproduceChat(run.run_id, {
        request_id: reqId,
        text: effectiveText,
        selected_context: ctxPayload,
        attachment_ids: attIds,
      });
      await syncAllReadModels(run.run_id);
    } finally {
      setSubmitting(false);
    }
  };

  const handleQuickModelSelection = async (patch: {
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
      /* keep optimistic state */
    }
  };

  const activeCanvasState = replayProjected ?? projected;
  const activeArtifact = viewerArtifactId ? artifactsById.get(viewerArtifactId) || null : null;

  // Temporarily widen left Chat panel when reading a document, restoring exact sidebarWidth on close (`L1.5 §6`)
  const effectiveLeftWidth = activeArtifact
    ? Math.max(sidebarWidth, Math.min(680, Math.round(window.innerWidth * 0.56)))
    : sidebarWidth;

  const { initialUserMessage, primaryMainSummary, followupConversation } = useMemo(() => {
    const firstUserIdx = messages.findIndex((m) => m.role === 'user');
    const firstUser = firstUserIdx >= 0 ? messages[firstUserIdx] : null;
    const secondUserIdx = messages.findIndex(
      (m, idx) => idx > firstUserIdx && m.role === 'user'
    );
    const primaryCutoff = secondUserIdx >= 0 ? secondUserIdx : messages.length;
    const primaryMain =
      messages.find((m) => m.role === 'main' && m.artifact_ids.length > 0) ||
      messages.slice(0, primaryCutoff).find((m) => m.role === 'main') ||
      null;

    const primaryMainIdx = primaryMain ? messages.indexOf(primaryMain) : -1;
    const followupStartIdx = primaryMainIdx >= 0 ? primaryMainIdx : firstUserIdx;
    const followups =
      followupStartIdx >= 0
        ? messages.filter(
            (m, idx) =>
              idx > followupStartIdx &&
              (m.role === 'user' || m.role === 'main') &&
              m.message_id !== firstUser?.message_id &&
              m.message_id !== primaryMain?.message_id
          )
        : [];
    return {
      initialUserMessage: firstUser,
      primaryMainSummary: primaryMain,
      followupConversation: followups,
    };
  }, [messages]);
  const activityItems = useMemo(
    () => messages.filter((m) => m.role === 'activity'),
    [messages]
  );
  const systemMessages = useMemo(
    () => messages.filter((m) => m.role === 'system'),
    [messages]
  );
  const latestSystemNotice =
    run?.status === 'failed' && systemMessages.length > 0
      ? systemMessages[systemMessages.length - 1]
      : null;
  const chatSteps = timeline?.chat_steps || [];

  const activeIntervention =
    (run?.status === 'paused' ? timeline?.active_intervention : null) || null;
  const isRunActive = run?.status === 'running';
  const isPausedForHuman = run?.status === 'paused' && Boolean(activeIntervention);
  const showLiveProgress = isRunActive || submitting;
  const liveTickerText = useMemo(
    () => liveToolHint || pickLiveStageTicker(activityItems),
    [liveToolHint, activityItems]
  );

  const handleQuickInterventionReply = useCallback(
    async (decisionText: string) => {
      if (!run || submitting) return;
      const reqId = `req_${crypto.randomUUID().slice(0, 12)}`;
      setSubmitting(true);
      setLiveToolHint(null);
      try {
        await submitReproduceChat(run.run_id, {
          request_id: reqId,
          text: decisionText,
          selected_context: { kind: 'agent', id: 'persona_main' },
          attachment_ids: [],
        });
        await syncAllReadModels(run.run_id);
      } finally {
        setSubmitting(false);
      }
    },
    [run, submitting, syncAllReadModels]
  );

  const handleSelectInterventionFromCanvas = useCallback(
    (rec: HumanInterventionRecord) => {
      setViewerArtifactId(null);
      const linked = rec.linked_step_ids || [];
      if (linked.length > 0) {
        setHighlightedStepIds(new Set(linked));
      }
      requestAnimationFrame(() => {
        const card = chatScrollRef.current?.querySelector<HTMLElement>(
          '[data-testid="human-intervention-card"]'
        );
        if (card) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        chatInputRef.current?.focus();
      });
    },
    []
  );

  return (
    <div className="rep-workspace-root">
      {/* Narrow-viewport switcher preserving Chat-first priority (`L1.5 §21`) */}
      <div className="rep-mobile-bar">
        <button
          type="button"
          className={`rep-mobile-tab ${mobileTab === 'chat' ? 'active' : ''}`}
          onClick={() => setMobileTab('chat')}
        >
          Research Chat
        </button>
        <button
          type="button"
          className={`rep-mobile-tab ${mobileTab === 'canvas' ? 'active' : ''}`}
          onClick={() => setMobileTab('canvas')}
        >
          Living Canvas
        </button>
      </div>

      <div
        className={`workspace-split rep-workspace-split ${isResizing ? 'is-resizing' : ''} mobile-${mobileTab}`}
        style={{ gridTemplateColumns: `${effectiveLeftWidth}px 6px minmax(0, 1fr)` }}
      >
        <aside
          className="chat-sidebar rep-chat-sidebar"
          data-testid="reproduce-chat-sidebar"
          aria-label="Research Chat and Scientific Documents"
        >
          <header className="chat-header">
            <div className="rep-chat-header-meta">
              <div className="chat-header-title" title={workspaceTitle}>
                {workspaceTitle}
              </div>
              {activeCanvasState.mode === 'simulation' && (
                <span className="rep-run-badge simulation">SIMULATION</span>
              )}
            </div>
            <div className="rep-chat-header-actions">
              <button type="button" className="btn-secondary" onClick={onNewWorkspace}>
                <Plus size={14} aria-hidden="true" />
                <span>New</span>
              </button>
            </div>
          </header>

          {activeArtifact ? (
            <InChatDocumentViewer
              artifact={activeArtifact}
              onClose={handleCloseArtifactViewer}
            />
          ) : (
            <>
              <div className="chat-scroll" ref={chatScrollRef}>
                {initialUserMessage && (
                  <div
                    key={initialUserMessage.message_id}
                    data-msg-id={initialUserMessage.message_id}
                    className={`msg-bubble msg-user ${
                      highlightedMsgIds.has(initialUserMessage.message_id) ? 'rep-msg-highlighted' : ''
                    }`}
                  >
                    <div className="markdown-body">
                      <MarkdownMath>{initialUserMessage.text}</MarkdownMath>
                    </div>
                  </div>
                )}

                {/* Left-Aligned Vertical Timeline Spine (`Main` init -> agent steps with green `N files` pills -> `Main` summary) */}
                {chatSteps.length > 0 && (
                  <div className="rep-vtimeline" data-testid="chat-vertical-timeline">
                    {chatSteps.map((step) => {
                      const stepArts = (step.artifact_ids || [])
                        .map((aid) => artifactsById.get(aid))
                        .filter((a): a is ReproduceArtifact => Boolean(a));
                      const isFilesOpen = expandedStepFiles.has(step.step_id);
                      const isDebateOpen = expandedStepDebates.has(step.step_id);
                      const isLinked = highlightedStepIds.has(step.step_id);
                      const isFinalMainStep = step.step_id === 'step_main_summary';

                      return (
                        <div
                          key={step.step_id}
                          data-step-id={step.step_id}
                          data-testid={`timeline-step-${step.step_id}`}
                          className={`rep-vtimeline-item status-${step.status} ${
                            isLinked ? 'is-linked' : ''
                          }`}
                        >
                          <span className="rep-vtimeline-dot" aria-hidden="true" />
                          <div className="rep-vtimeline-body">
                            <div className="rep-vtimeline-line">
                              <span
                                className={`rep-vtimeline-text ${step.snapshot_seq ? 'is-clickable' : ''}`}
                                onClick={() => {
                                  if (step.debate_summary) {
                                    toggleStepDebate(step.step_id);
                                  }
                                  if (step.snapshot_seq && timeline?.snapshots) {
                                    const matchedSnap = timeline.snapshots.find(
                                      (s) => s.seq === step.snapshot_seq || s.linked_step_ids.includes(step.step_id)
                                    );
                                    if (matchedSnap) {
                                      void handleSelectTimelineSnapshot(matchedSnap);
                                    }
                                  }
                                }}
                              >
                                {step.text}
                              </span>

                              {step.status === 'done' && !isFinalMainStep && stepArts.length > 0 && (
                                <button
                                  type="button"
                                  className={`rep-step-files-pill ${isFilesOpen ? 'is-open' : ''}`}
                                  data-testid={`step-files-pill-${step.step_id}`}
                                  aria-expanded={isFilesOpen}
                                  onClick={() => toggleStepFiles(step.step_id)}
                                >
                                  {stepArts.length} {stepArts.length === 1 ? 'file' : 'files'}
                                </button>
                              )}
                            </div>

                            {step.debate_summary && (isDebateOpen || isLinked) && (
                              <div
                                className="rep-step-debate-box"
                                data-testid={`step-debate-box-${step.step_id}`}
                              >
                                <strong>Discussion summary:</strong>{' '}
                                {toPlainUiText(step.debate_summary, 200)}
                              </div>
                            )}

                            {isFilesOpen && stepArts.length > 0 && (
                              <div
                                className="rep-step-files-tray"
                                data-testid={`step-files-tray-${step.step_id}`}
                              >
                                {stepArts.map((art) => (
                                  <div key={art.artifact_id} className="rep-step-file-row">
                                    <span className="rep-step-file-name" title={art.filename || art.artifact_id}>
                                      {art.filename || art.artifact_id}
                                    </span>
                                    <div className="rep-step-file-actions">
                                      <button
                                        type="button"
                                        className="btn-secondary rep-step-mini-btn"
                                        data-testid={`preview-step-artifact-${art.artifact_id}`}
                                        onClick={() => handleOpenArtifactViewer(art.artifact_id)}
                                      >
                                        <Eye size={11} aria-hidden="true" />
                                        <span>Preview</span>
                                      </button>
                                      <a
                                        href={art.download_url || `/api/artifacts/${art.artifact_id}/download`}
                                        download={art.filename || true}
                                        className="btn-secondary rep-step-mini-btn"
                                      >
                                        <Download size={11} aria-hidden="true" />
                                        <span>Download</span>
                                      </a>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {showLiveProgress && followupConversation.length === 0 && !primaryMainSummary && (
                  <div className="live-run-progress-group">
                    <div className="stream-trail is-running">
                      <WaveProgressText text={liveTickerText} title={liveTickerText} />
                    </div>
                  </div>
                )}

                {activeIntervention && (
                  <div
                    className="rep-human-intervention-card"
                    data-testid="human-intervention-card"
                  >
                    <div className="rep-hic-head">
                      <span className="rep-hic-title">
                        <Mail size={14} aria-hidden="true" />
                        <span>Human Intervention Requested</span>
                      </span>
                      <span className="rep-hic-from">
                        {activeIntervention.from_labels.join(' & ')} → Main
                      </span>
                    </div>
                    <div className="rep-hic-dissent markdown-body">
                      <MarkdownMath>{activeIntervention.dissent_summary}</MarkdownMath>
                    </div>
                    <div className="rep-hic-question">
                      {toPlainUiText(activeIntervention.question, 180)}
                    </div>
                    {activeIntervention.options.length > 0 && (
                      <div className="rep-hic-options">
                        {activeIntervention.options.map((opt, idx) => (
                          <button
                            key={`hic_opt_${idx}`}
                            type="button"
                            className="rep-hic-option-btn"
                            disabled={submitting}
                            onClick={() => void handleQuickInterventionReply(opt.prompt)}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="rep-hic-hint">
                      Click a quick option above or type your guidance in the input box below.
                    </div>
                  </div>
                )}

                {latestSystemNotice && !isPausedForHuman && (
                  <div className="rep-system-notice" data-testid="reproduce-system-notice">
                    {latestSystemNotice.text}
                  </div>
                )}

                {primaryMainSummary && (
                  <div
                    key={primaryMainSummary.message_id}
                    data-msg-id={primaryMainSummary.message_id}
                    className={`msg-bubble msg-assistant ${
                      highlightedMsgIds.has(primaryMainSummary.message_id) ? 'rep-msg-highlighted' : ''
                    }`}
                  >
                    {primaryMainSummary.artifact_ids.length > 0 && (
                      <div className="msg-success-header">
                        <CheckCircle2 size={15} className="msg-success-icon" aria-hidden="true" />
                        <span>Success</span>
                      </div>
                    )}
                    <div className="markdown-body">
                      <MarkdownMath>{primaryMainSummary.text}</MarkdownMath>
                    </div>
                  </div>
                )}

                {silentFilterNotice && (
                  <div className="rep-silent-empty-note" data-testid="silent-agent-empty-notice">
                    <span>{silentFilterNotice}</span>
                    <button
                      type="button"
                      className="rep-inline-dismiss"
                      aria-label="Dismiss notice"
                      onClick={() => setSilentFilterNotice(null)}
                    >
                      <X size={12} />
                    </button>
                  </div>
                )}

                {/* Final Deliverables: PDF Report + Reproducible Scripts */}
                {(finalPdfReports.length > 0 || reproducibleScripts.length > 0) && (
                  <div className="rep-files-section" data-testid="chat-artifact-cards">
                    <div className="rep-file-card-list">
                      {finalPdfReports.map((art) => (
                        <div
                          key={art.artifact_id}
                          className="rep-file-card"
                          data-testid={`artifact-card-${art.kind}`}
                          data-artifact-id={art.artifact_id}
                        >
                          <div className="rep-file-card-top">
                            <span className="rep-file-icon kind-report">
                              <FileText size={15} aria-hidden="true" />
                            </span>
                            <div className="rep-file-info">
                              <div className="rep-file-name" title={art.filename || art.artifact_id}>
                                {art.filename || 'reproduction_report.pdf'}
                              </div>
                              <div className="rep-file-sub">
                                <span className="rep-file-kind">Final PDF Report</span>
                              </div>
                            </div>
                          </div>

                          <div className="rep-file-actions">
                            <button
                              type="button"
                              className="btn-secondary rep-file-btn"
                              data-testid={`preview-artifact-${art.artifact_id}`}
                              onClick={() => handleOpenArtifactViewer(art.artifact_id)}
                            >
                              <Eye size={13} aria-hidden="true" />
                              <span>Preview</span>
                            </button>
                            <a
                              href={art.download_url || `/api/artifacts/${art.artifact_id}/download`}
                              download={art.filename || true}
                              className="btn-secondary rep-file-btn"
                            >
                              <Download size={13} aria-hidden="true" />
                              <span>Download</span>
                            </a>
                          </div>
                        </div>
                      ))}

                      {reproducibleScripts.length > 0 && (
                        <div className="rep-scripts-summary-row" data-testid="final-scripts-summary">
                          <span className="rep-scripts-label">
                            <FileCode2 size={13} aria-hidden="true" />
                            <span>Reproducible Python Scripts</span>
                          </span>
                          <button
                            type="button"
                            className={`rep-step-files-pill ${
                              expandedStepFiles.has('__final_scripts__') ? 'is-open' : ''
                            }`}
                            data-testid="final-scripts-pill"
                            onClick={() => toggleStepFiles('__final_scripts__')}
                          >
                            {reproducibleScripts.length}{' '}
                            {reproducibleScripts.length === 1 ? 'script' : 'scripts'}
                          </button>
                          {expandedStepFiles.has('__final_scripts__') && (
                            <div className="rep-step-files-tray">
                              {reproducibleScripts.map((sc) => (
                                <div key={sc.artifact_id} className="rep-step-file-row">
                                  <span className="rep-step-file-name">{sc.filename || sc.artifact_id}</span>
                                  <div className="rep-step-file-actions">
                                    <button
                                      type="button"
                                      className="btn-secondary rep-step-mini-btn"
                                      data-testid={`preview-artifact-${sc.artifact_id}`}
                                      onClick={() => handleOpenArtifactViewer(sc.artifact_id)}
                                    >
                                      <Eye size={11} aria-hidden="true" />
                                      <span>Preview</span>
                                    </button>
                                    <a
                                      href={sc.download_url || `/api/artifacts/${sc.artifact_id}/download`}
                                      download={sc.filename || true}
                                      className="btn-secondary rep-step-mini-btn"
                                    >
                                      <Download size={11} aria-hidden="true" />
                                      <span>Download</span>
                                    </a>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {followupConversation.map((msg) => (
                  <div
                    key={msg.message_id}
                    data-msg-id={msg.message_id}
                    data-testid={`followup-msg-${msg.role}`}
                    className={`msg-bubble ${msg.role === 'user' ? 'msg-user' : 'msg-assistant'} ${
                      highlightedMsgIds.has(msg.message_id) ? 'rep-msg-highlighted' : ''
                    }`}
                  >
                    {msg.role === 'main' && msg.artifact_ids.length > 0 && (
                      <div className="msg-success-header">
                        <CheckCircle2 size={15} className="msg-success-icon" aria-hidden="true" />
                        <span>Success</span>
                      </div>
                    )}
                    <div className="markdown-body">
                      <MarkdownMath>{msg.text}</MarkdownMath>
                    </div>
                  </div>
                ))}

                {showLiveProgress && (followupConversation.length > 0 || Boolean(primaryMainSummary)) && (
                  <div className="live-run-progress-group">
                    <div className="stream-trail is-running">
                      <WaveProgressText text={liveTickerText} title={liveTickerText} />
                    </div>
                  </div>
                )}
              </div>

              {/* Sole user command input (`POST /api/runs/{id}/chat`) */}
              <form className="chat-composer" onSubmit={handleSendChat}>
                {(selectedContext || uploadedAttachments.length > 0 || uploadErr) && (
                  <div className="rep-composer-context-row">
                    {selectedContext && (
                      <span
                        className="chat-attached-card-chip"
                        data-testid="selected-context-chip"
                        title={toPlainUiText(selectedContext.label, 120)}
                      >
                        <span className="chat-attached-card-title">
                          Context: {formatShortContextChip(selectedContext.ctx, selectedContext.label)}
                        </span>
                        <button
                          type="button"
                          className="chat-attached-card-remove"
                          aria-label="Clear context reference"
                          onClick={() => {
                            setSelectedContext(null);
                            setHighlightedMsgIds(new Set());
                          }}
                        >
                          <X size={11} />
                        </button>
                      </span>
                    )}
                    {uploadedAttachments.map((att) => (
                      <span key={att.artifact_id} className="chat-attached-card-chip">
                        <Paperclip size={11} aria-hidden="true" />
                        <span>{att.filename || att.artifact_id}</span>
                        <button
                          type="button"
                          className="chat-attached-card-remove"
                          aria-label={`Remove ${att.filename || 'attachment'}`}
                          onClick={() =>
                            setUploadedAttachments((prev) =>
                              prev.filter((x) => x.artifact_id !== att.artifact_id)
                            )
                          }
                        >
                          <X size={11} />
                        </button>
                      </span>
                    ))}
                    {uploadErr && <span className="attach-error">{uploadErr}</span>}
                  </div>
                )}

                <label htmlFor="rep-chat-textarea" className="visually-hidden">
                  Research command or question for Main Agent
                </label>
                <textarea
                  ref={chatInputRef}
                  id="rep-chat-textarea"
                  className="chat-composer-input"
                  data-testid="reproduce-chat-input"
                  placeholder={
                    isPausedForHuman
                      ? 'Reply with your decision for the debating agents...'
                      : 'Ask anything...'
                  }
                  value={prompt}
                  disabled={submitting}
                  onChange={(e) => setPrompt(e.target.value)}
                  onCompositionEnd={(e) => {
                    lastCompositionEndAtRef.current = e.nativeEvent.timeStamp;
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      if (e.nativeEvent.isComposing) return;
                      if (
                        lastCompositionEndAtRef.current !== null &&
                        Math.abs(e.nativeEvent.timeStamp - lastCompositionEndAtRef.current) < 50
                      ) {
                        return;
                      }
                      void handleSendChat(e);
                    }
                  }}
                />

                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.csv,.tsv,.txt,.md,.json,.parquet,.png,.jpg,.jpeg,.py,.zip"
                  multiple
                  className="sr-only"
                  aria-label="Upload paper PDF or dataset attachment"
                  onChange={(e) => {
                    void handleAttachmentPick(e.target.files);
                    e.target.value = '';
                  }}
                />

                <div className="chat-composer-actions">
                  <div className="rep-composer-left">
                    <button
                      type="button"
                      className="btn-attach-icon"
                      aria-label="Upload paper PDF or dataset"
                      title="Upload paper PDF or dataset"
                      disabled={submitting || uploadingFile || !run}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Paperclip size={17} aria-hidden="true" />
                    </button>

                    <ModelThinkingSelector
                      settings={llmSettings}
                      disabled={submitting}
                      onChangeSelection={handleQuickModelSelection}
                      onOpenConfig={() => setApiModalOpen(true)}
                    />
                  </div>

                  {isRunActive ? (
                    <button
                      type="button"
                      className="chat-send-btn rep-composer-pause-btn"
                      data-testid="reproduce-stop-btn"
                      aria-label={cancelling ? 'Stopping run' : 'Pause run'}
                      title={cancelling ? 'Stopping...' : 'Stop current run'}
                      disabled={cancelling}
                      onClick={() => void handleCancelRun()}
                    >
                      <Pause size={16} aria-hidden="true" />
                    </button>
                  ) : (
                    <button
                      type="submit"
                      className="btn-primary chat-send-btn"
                      data-testid="reproduce-chat-send"
                      aria-label={submitting ? 'Running' : 'Send command to Main Agent'}
                      disabled={
                        submitting ||
                        uploadingFile ||
                        (!prompt.trim() && uploadedAttachments.length === 0) ||
                        !run
                      }
                    >
                      <ArrowRight size={18} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </form>
            </>
          )}
        </aside>

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat and living canvas panels"
          aria-valuenow={effectiveLeftWidth}
          tabIndex={0}
          className={`workspace-resizer ${isResizing ? 'dragging' : ''}`}
          onPointerDown={onResizerPointerDown}
          onPointerMove={onResizerPointerMove}
          onPointerUp={onResizerPointerUp}
          onPointerCancel={onResizerPointerUp}
          onDoubleClick={onResizerDoubleClick}
          onKeyDown={onResizerKeyDown}
        />

        <LivingResearchCanvas
          mode={activeCanvasState.mode}
          isHistoricalReplay={replayProjected !== null}
          replaySeq={replaySeq}
          personas={activeCanvasState.personas}
          tables={activeCanvasState.tables}
          overflowCollaborations={activeCanvasState.overflowCollaborations}
          timelineSnapshots={timeline?.snapshots || []}
          activeSnapshotId={activeSnapshotId}
          tableDialogues={timeline?.table_dialogues || {}}
          briefings={timeline?.briefings || {}}
          interventions={timeline?.interventions || {}}
          activeIntervention={activeIntervention}
          onSelectTimelineSnapshot={(s) => void handleSelectTimelineSnapshot(s)}
          onReturnToLive={handleExitReplay}
          onSilentSelectTable={handleSilentSelectTable}
          onSilentSelectBriefing={handleSilentSelectBriefing}
          onSelectIntervention={handleSelectInterventionFromCanvas}
        />
      </div>

      <ApiConfigModal
        open={apiModalOpen}
        settings={llmSettings}
        onSaved={(next) => {
          setApiModalOpen(false);
          onLLMSettingsChange(next);
        }}
        onReset={(next) => {
          setApiModalOpen(false);
          onLLMSettingsChange(next);
        }}
        onClose={() => setApiModalOpen(false)}
      />
    </div>
  );
};
