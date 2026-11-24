import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowRight,
  CheckCircle2,
  MessageSquare,
  Plus,
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

interface ChatSidebarProps {
  workspaceTitle: string;
  messages: ChatMessage[];
  runs: TaskRun[];
  eventsByRun: Record<string, RunEvent[]>;
  artifactVersions: ArtifactVersion[];
  selectedIds: string[];
  submitting: boolean;
  onNewWorkspace: () => void;
  onSubmitPrompt: (prompt: string, uploadId?: string | null) => Promise<void>;
  onOpenVersion?: (versionId: string) => void;
}

interface FollowUpRadioBubbleProps {
  messageId: string;
  followUp: FollowUpSuggestion;
  submitting: boolean;
  onSelectSubmit: (prompt: string) => Promise<void>;
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

const WaveProgressText: React.FC<{ text: string; title?: string }> = ({ text, title }) => {
  const chars = Array.from(text);
  const charRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    charRefs.current = charRefs.current.slice(0, chars.length);
    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      for (let i = 0; i < chars.length; i += 1) {
        const el = charRefs.current[i];
        if (el) el.style.opacity = '0.75';
      }
      return undefined;
    }

    let rafId = 0;
    const start = performance.now();
    const speed = 16;
    const halfWidth = 2.2;
    const minOpacity = 0.2;
    const maxOpacity = 0.8;
    const spanLen = chars.length + halfWidth * 2 + 1.5;

    const tick = (now: number) => {
      const elapsedSec = (now - start) / 1000;
      const head = ((elapsedSec * speed) % spanLen) - halfWidth;

      for (let i = 0; i < chars.length; i += 1) {
        const el = charRefs.current[i];
        if (!el) continue;
        const d = Math.abs(i - head);
        const boost = d < halfWidth ? Math.cos((d / halfWidth) * (Math.PI / 2)) ** 2 : 0;
        el.style.opacity = (minOpacity + (maxOpacity - minOpacity) * boost).toFixed(3);
      }
      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [text, chars.length]);

  return (
    <span className="wave-progress-text" title={title} aria-label={text}>
      {chars.map((ch, idx) => (
        <span
          key={idx}
          ref={(el) => {
            charRefs.current[idx] = el;
          }}
          className="wave-char"
          aria-hidden="true"
        >
          {ch}
        </span>
      ))}
    </span>
  );
};

export const ChatSidebar: React.FC<ChatSidebarProps> = ({
  workspaceTitle,
  messages,
  runs,
  eventsByRun,
  artifactVersions,
  submitting,
  onNewWorkspace,
  onSubmitPrompt,
}) => {
  const [prompt, setPrompt] = useState('');
  const [sendingComments, setSendingComments] = useState(false);
  const att = useAttachmentController(submitting);

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

  const runsById = new Map(runs.map((r) => [r.run_id, r]));

  const renderStreamTrail = (run: TaskRun) => {
    const isActive =
      run.status === 'queued' || run.status === 'running' || run.status === 'retrying';
    if (!isActive) return null;

    const events = eventsByRun[run.run_id] || [];
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

    const displayText = latestLabel ? `${latestLabel}...` : 'Thinking...';

    return (
      <div
        key={`stream_${run.run_id}`}
        className="stream-trail is-running"
        data-run-id={run.run_id}
      >
        <WaveProgressText text={displayText} title={latestSummary || displayText} />
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

      <div className="chat-scroll">
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
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content.replace(
                          /^Completed\s+\*{0,2}\d+\*{0,2}\s+[^\n]*artifact\(s\)[^\n]*:\s*\n+/i,
                          ''
                        )}
                      </ReactMarkdown>
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
              {linkedRun && renderStreamTrail(linkedRun)}
            </React.Fragment>
          );
        })}
      </div>

      <form className="chat-composer" onSubmit={handleSubmit}>
        {visiblePendingComments > 0 && (
          <div className="chat-context-banner">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <MessageSquare size={12} />
              {`${visiblePendingComments} comments`}
            </span>
          </div>
        )}

        <textarea
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
