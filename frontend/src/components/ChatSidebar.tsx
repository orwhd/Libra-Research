import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  MessageSquare,
  Paperclip,
  Plus,
  X,
} from 'lucide-react';
import type {
  ArtifactVersion,
  ChatMessage,
  FollowUpSuggestion,
  RunEvent,
  TaskRun,
} from '../types';
import {
  AttachmentHiddenInputs,
  AttachmentInlineRows,
  AttachmentMenuButton,
  useAttachmentController,
} from './AttachmentControls';
import { MarkdownMath } from './MarkdownMath';
import { WaveProgressText } from './WaveProgressText';

interface ChatSidebarProps {
  workspaceTitle: string;
  messages: ChatMessage[];
  runs: TaskRun[];
  eventsByRun: Record<string, RunEvent[]>;
  artifactVersions: ArtifactVersion[];
  selectedIds: string[];
  attachedCardIds?: string[];
  focusComposerSeq?: number;
  submitting: boolean;
  onNewWorkspace: () => void;
  onSubmitPrompt: (prompt: string, uploadId?: string | null) => Promise<void>;
  onRemoveAttachedCard?: (versionId: string) => void;
  onOpenVersion?: (versionId: string) => void;
}

interface FollowUpRadioBubbleProps {
  messageId: string;
  followUp: FollowUpSuggestion;
  submitting: boolean;
  onSelectSubmit: (prompt: string) => Promise<void>;
}

function hasCjkText(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

const FollowUpRadioBubble: React.FC<FollowUpRadioBubbleProps> = ({
  messageId,
  followUp,
  submitting,
  onSelectSubmit,
}) => {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const safeIndex =
    selectedIndex >= 0 && selectedIndex < followUp.options.length ? selectedIndex : 0;
  const chosenOption = followUp.options[safeIndex];

  if (!followUp.options.length) return null;

  return (
    <div className="followup-radio-card" data-testid={`followup-${messageId}`}>
      <div className="followup-question">{followUp.question}</div>
      <fieldset className="followup-options" aria-label={followUp.question}>
        {followUp.options.map((opt, idx) => {
          const isChecked = idx === safeIndex;
          return (
            <label
              key={`${messageId}_opt_${idx}`}
              className={`followup-option ${isChecked ? 'selected' : ''}`}
            >
              <input
                type="radio"
                name={`followup_${messageId}`}
                checked={isChecked}
                onChange={() => setSelectedIndex(idx)}
              />
              <span className="followup-option-text">{opt.label}</span>
            </label>
          );
        })}
      </fieldset>
      {chosenOption && (
        <div className="followup-actions">
          <button
            type="button"
            className="btn-followup-go"
            disabled={submitting}
            onClick={() => void onSelectSubmit(chosenOption.prompt)}
          >
            Explore
          </button>
        </div>
      )}
    </div>
  );
};

export const ChatSidebar: React.FC<ChatSidebarProps> = ({
  workspaceTitle,
  messages,
  runs,
  eventsByRun,
  artifactVersions,
  attachedCardIds = [],
  focusComposerSeq = 0,
  submitting,
  onNewWorkspace,
  onSubmitPrompt,
  onRemoveAttachedCard,
}) => {
  const [prompt, setPrompt] = useState('');
  const [sendingComments, setSendingComments] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const att = useAttachmentController(submitting);

  useEffect(() => {
    if (focusComposerSeq > 0) {
      composerRef.current?.focus();
    }
  }, [focusComposerSeq]);

  const attachedVersions = useMemo(
    () =>
      attachedCardIds
        .map((id) => artifactVersions.find((v) => v.artifact_version_id === id))
        .filter((v): v is ArtifactVersion => Boolean(v)),
    [attachedCardIds, artifactVersions]
  );

  const totalPendingComments = artifactVersions.reduce(
    (sum, v) => sum + (v.pending_comment_count || 0),
    0
  );
  const visiblePendingComments = sendingComments ? 0 : totalPendingComments;
  const canSubmit =
    !submitting &&
    !att.uploading &&
    (Boolean(prompt.trim()) || visiblePendingComments > 0);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const text = prompt.trim();
    const uploadId = att.attached?.upload_id ?? null;
    const hadComments = visiblePendingComments > 0;
    setPrompt('');
    att.setAttached(null);
    if (hadComments) setSendingComments(true);
    try {
      await onSubmitPrompt(text, uploadId);
    } finally {
      setSendingComments(false);
    }
  };

  const runsById = useMemo(() => new Map(runs.map((r) => [r.run_id, r])), [runs]);
  const assistantRunIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of messages) {
      if (m.role === 'assistant' && m.run_id) {
        ids.add(m.run_id);
      }
    }
    return ids;
  }, [messages]);

  const totalEventCount = useMemo(
    () => Object.values(eventsByRun).reduce((acc, list) => acc + list.length, 0),
    [eventsByRun]
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 180) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, totalEventCount]);

  const extractProgressNotes = (run: TaskRun, msg: ChatMessage): string[] => {
    const events = eventsByRun[run.run_id] || [];
    const notes: string[] = [];
    const seen = new Set<string>();
    const plumbingPattern =
      /\b(?:ds|ar|av|ws|run)_[a-z0-9]+\b|reused immutable|reused computed|miner agent|workspace state to plan|planned targeted revision/i;

    for (const ev of events) {
      const rawNote =
        typeof ev.details?.progress_note === 'string' ? ev.details.progress_note.trim() : '';
      if (!rawNote || plumbingPattern.test(rawNote)) continue;
      if (!seen.has(rawNote)) {
        seen.add(rawNote);
        notes.push(rawNote);
      }
    }

    return notes.slice(-3);
  };

  const renderRunActivityOrFallback = (run: TaskRun, msg: ChatMessage) => {
    const hasAssistantReply = assistantRunIds.has(run.run_id);
    if (hasAssistantReply) return null;

    const isActive =
      run.status === 'queued' || run.status === 'running' || run.status === 'retrying';
    const events = eventsByRun[run.run_id] || [];
    const progressNotes = extractProgressNotes(run, msg);
    const commentTexts = (msg.attached_comments || []).map((c) => c.text).join(' ');
    const isCjk = hasCjkText(`${msg.content} ${commentTexts} ${workspaceTitle}`);

    if (isActive || run.status === 'completed') {
      let latestLabel = '';
      let latestSummary = '';

      for (let i = events.length - 1; i >= 0; i -= 1) {
        const ev = events[i];
        const rawLabel =
          typeof ev.details?.stream_label === 'string' ? ev.details.stream_label.trim() : '';
        if (rawLabel) {
          latestLabel = rawLabel;
          latestSummary = ev.concise_summary || rawLabel;
          break;
        }
      }

      const defaultTicker = isCjk ? '正在处理数据...' : 'Processing data...';
      const displayText = latestLabel ? `${latestLabel}...` : defaultTicker;
      const bodyMarkdown =
        progressNotes.length === 0
          ? ''
          : progressNotes.length === 1
            ? progressNotes[0]
            : progressNotes.map((line) => `- ${line}`).join('\n');

      return (
        <div
          key={`stream_wrap_${run.run_id}`}
          className="live-run-progress-group"
          data-run-id={run.run_id}
        >
          {bodyMarkdown && (
            <div
              className="msg-bubble msg-assistant live-working-report"
              role="status"
              aria-live="polite"
              aria-atomic="false"
              data-testid={`live-report-${run.run_id}`}
            >
              <div className="markdown-body">
                <MarkdownMath>{bodyMarkdown}</MarkdownMath>
              </div>
            </div>
          )}
          <div className="stream-trail is-running" data-testid={`stream-trail-${run.run_id}`}>
            <WaveProgressText text={displayText} title={latestSummary || displayText} />
          </div>
        </div>
      );
    }

    // Resilient fallback if a historical or interrupted terminal run lacks a persisted assistant ChatMessage
    const fallbackError =
      (run.error_message || '').trim() ||
      (isCjk
        ? '该轮分析任务在完成汇总前已中断，画布已有成果均已安全保留，可重新发送指令继续。'
        : 'This run stopped before posting its final summary; existing canvas artifacts remain intact.');
    const notesBlock =
      progressNotes.length > 0 ? `${progressNotes.map((n) => `- ${n}`).join('\n')}\n\n` : '';

    return (
      <div
        key={`fallback_msg_${run.run_id}`}
        className="msg-bubble msg-assistant msg-run-fallback"
        data-testid={`fallback-reply-${run.run_id}`}
      >
        <div className="msg-fallback-header">
          <AlertCircle size={15} className="msg-fallback-icon" aria-hidden="true" />
          <span>{isCjk ? '运行状态说明' : 'Run Status Notice'}</span>
        </div>
        <div className="markdown-body">
          <MarkdownMath>{`${notesBlock}${fallbackError}`}</MarkdownMath>
        </div>
      </div>
    );
  };

  return (
    <aside className="chat-sidebar" aria-label="Research Chat and Agent Activity">
      <header className="chat-header">
        <div className="chat-header-title" title={workspaceTitle}>
          {workspaceTitle}
        </div>
        <button type="button" className="btn-secondary" onClick={onNewWorkspace}>
          <Plus size={14} />
          New
        </button>
      </header>

      <div className="chat-scroll" ref={scrollRef}>
        {messages.map((msg) => {
          const linkedRun = msg.role === 'user' && msg.run_id ? runsById.get(msg.run_id) : undefined;
          return (
            <React.Fragment key={msg.message_id}>
              <div className={`msg-bubble ${msg.role === 'user' ? 'msg-user' : 'msg-assistant'}`}>
                {msg.role === 'user' ? (
                  <div className="msg-user-stack">
                    {msg.attached_comments && msg.attached_comments.length > 0 && (
                      <span
                        className="msg-sent-comments-chip"
                        title={msg.attached_comments
                          .map((c) => (c.artifact_title ? `[${c.artifact_title}] ${c.text}` : c.text))
                          .join('\n')}
                      >
                        <MessageSquare size={12} />
                        {`${msg.attached_comments.length} comments`}
                      </span>
                    )}
                    {msg.content.trim() && <div>{msg.content}</div>}
                  </div>
                ) : (
                  <>
                    {msg.role === 'assistant' &&
                      msg.artifact_version_ids &&
                      msg.artifact_version_ids.length > 0 && (
                        <div className="msg-success-header">
                          <CheckCircle2
                            size={15}
                            className="msg-success-icon"
                            aria-hidden="true"
                          />
                          <span>Success</span>
                        </div>
                      )}
                    <div className="markdown-body">
                      <MarkdownMath>
                        {msg.content.replace(
                          /^Completed\s+\*{0,2}\d+\*{0,2}\s+[^\n]*artifact\(s\)[^\n]*:\s*\n+/i,
                          ''
                        )}
                      </MarkdownMath>
                    </div>
                  </>
                )}

                {msg.role === 'assistant' && msg.follow_up && msg.follow_up.options.length > 0 && (
                  <FollowUpRadioBubble
                    messageId={msg.message_id}
                    followUp={msg.follow_up}
                    submitting={submitting}
                    onSelectSubmit={(p) => onSubmitPrompt(p)}
                  />
                )}
              </div>
              {linkedRun && renderRunActivityOrFallback(linkedRun, msg)}
            </React.Fragment>
          );
        })}
      </div>

      <form className="chat-composer" onSubmit={handleSubmit}>
        {(visiblePendingComments > 0 || attachedVersions.length > 0) && (
          <div className="chat-context-banner" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {attachedVersions.map((av) => (
              <span
                key={av.artifact_version_id}
                className="chat-attached-card-chip"
                title={`${av.title} (v${av.version_number})`}
              >
                <Paperclip size={11} aria-hidden="true" />
                <span className="chat-attached-card-title">
                  {av.title} (v{av.version_number})
                </span>
                {onRemoveAttachedCard && (
                  <button
                    type="button"
                    className="chat-attached-card-remove"
                    aria-label={`Remove ${av.title} from chat`}
                    onClick={() => onRemoveAttachedCard(av.artifact_version_id)}
                  >
                    <X size={11} />
                  </button>
                )}
              </span>
            ))}
            {visiblePendingComments > 0 && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <MessageSquare size={12} />
                {`${visiblePendingComments} comments`}
              </span>
            )}
          </div>
        )}

        <textarea
          ref={composerRef}
          className="chat-composer-input"
          aria-label="Follow-up prompt"
          placeholder="Ask anything..."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          disabled={submitting}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSubmit(e);
            }
          }}
        />

        <AttachmentHiddenInputs controller={att} />
        <AttachmentInlineRows controller={att} disabled={submitting} />

        <div className="chat-composer-actions">
          <AttachmentMenuButton
            controller={att}
            disabled={submitting || att.uploading}
            dropDirection="up"
          />

          <button
            type="submit"
            className="btn-primary chat-send-btn"
            aria-label={submitting ? 'Running' : 'Send'}
            title={submitting ? 'Running...' : 'Send'}
            disabled={!canSubmit}
          >
            <ArrowRight size={18} />
          </button>
        </div>
      </form>
    </aside>
  );
};
