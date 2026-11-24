import React, { useCallback, useEffect, useRef, useState } from 'react';
import { MessageSquare, Trash2, CheckCircle2, Clock, X } from 'lucide-react';
import type { AnchorPoint, ArtifactVersion, PreviewViewState, VisualComment } from '../types';
import { createVersionComment, deleteComment } from '../api';
import { PreviewFrame } from './PreviewFrame';

interface CommentsOverlayProps {
  version: ArtifactVersion;
  comments: VisualComment[];
  pinMode: boolean;
  onExitPinMode: () => void;
  onCommentChanged: () => Promise<void>;
}

function getPopoverPositionStyle(anchor: AnchorPoint): React.CSSProperties {
  const openLeft = anchor.x_norm > 0.62;
  const openUp = anchor.y_norm > 0.65;

  return {
    ...(openLeft
      ? { right: `calc(${((1 - anchor.x_norm) * 100).toFixed(2)}% + 16px)` }
      : { left: `calc(${(anchor.x_norm * 100).toFixed(2)}% + 16px)` }),
    ...(openUp
      ? { bottom: `calc(${((1 - anchor.y_norm) * 100).toFixed(2)}% + 12px)` }
      : { top: `calc(${(anchor.y_norm * 100).toFixed(2)}% - 8px)` }),
  };
}

export const CommentsOverlay: React.FC<CommentsOverlayProps> = ({
  version,
  comments,
  pinMode,
  onExitPinMode,
  onCommentChanged,
}) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  const [activeCommentId, setActiveCommentId] = useState<string | null>(null);
  const [draftAnchor, setDraftAnchor] = useState<AnchorPoint | null>(null);
  const [draftHint, setDraftHint] = useState<string | null>(null);
  const [draftViewState, setDraftViewState] = useState<PreviewViewState>({
    viewport_width: 1280,
    viewport_height: 720,
    scroll_x: 0,
    scroll_y: 0,
    zoom: 1,
    filter_state: {},
  });
  const [commentText, setCommentText] = useState('');
  const [saving, setSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!pinMode && !draftAnchor && !activeCommentId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (draftAnchor) {
        setDraftAnchor(null);
        setCommentText('');
        setErrorMsg(null);
      } else if (activeCommentId) {
        setActiveCommentId(null);
      } else if (pinMode) {
        onExitPinMode();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [pinMode, draftAnchor, activeCommentId, onExitPinMode]);

  const handleViewStateReceived = useCallback(
    (viewState: PreviewViewState, elementHint: string | null) => {
      setDraftViewState(viewState);
      if (elementHint) setDraftHint(elementHint);
    },
    []
  );

  const handleStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!pinMode || !stageRef.current) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x_norm = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
    const y_norm = Math.max(0, Math.min(1, (e.clientY - rect.top) / Math.max(1, rect.height)));
    setActiveCommentId(null);
    setErrorMsg(null);
    setDraftAnchor({ x_norm, y_norm });
    setDraftHint(null);
    onExitPinMode();
    setDraftViewState({
      viewport_width: Math.round(rect.width),
      viewport_height: Math.round(rect.height),
      scroll_x: 0,
      scroll_y: 0,
      zoom: 1,
      filter_state: {},
    });

    try {
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'RC_REQUEST_VIEW_STATE', x_norm, y_norm, request_id: Date.now().toString() },
        '*'
      );
    } catch {
      /* ignore if sandboxed iframe not ready */
    }
  };

  const submitDraftComment = async () => {
    if (!draftAnchor || !commentText.trim() || saving) return;
    setSaving(true);
    setErrorMsg(null);
    try {
      await createVersionComment(version.artifact_version_id, {
        text: commentText.trim(),
        anchor: draftAnchor,
        preview_view_state: draftViewState,
        optional_element_hint: draftHint,
      });
      setCommentText('');
      setDraftAnchor(null);
      setDraftHint(null);
      await onCommentChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (commentId: string) => {
    await deleteComment(commentId);
    if (activeCommentId === commentId) {
      setActiveCommentId(null);
    }
    await onCommentChanged();
  };

  const activeComment = comments.find((c) => c.comment_id === activeCommentId) || null;

  return (
    <div className="preview-stage-wrap" ref={stageRef}>
      <PreviewFrame
        version={version}
        iframeRef={iframeRef}
        onViewStateReceived={handleViewStateReceived}
      />

      {pinMode && (
        <div
          className="pin-capture-overlay"
          data-testid="pin-capture-overlay"
          onClick={handleStageClick}
          title="Click anywhere on the visualization to anchor your comment"
        />
      )}

      {comments.map((c) => {
        const isOpen = activeCommentId === c.comment_id;
        return (
          <button
            key={c.comment_id}
            type="button"
            className={`preview-comment-pin ${c.status === 'addressed' ? 'addressed' : ''} ${
              isOpen ? 'open' : ''
            }`}
            style={{ left: `${c.anchor.x_norm * 100}%`, top: `${c.anchor.y_norm * 100}%` }}
            aria-label={`Comment: ${c.text}`}
            aria-expanded={isOpen}
            onClick={(e) => {
              e.stopPropagation();
              setDraftAnchor(null);
              setErrorMsg(null);
              setActiveCommentId((prev) => (prev === c.comment_id ? null : c.comment_id));
            }}
          >
            <MessageSquare size={13} />
          </button>
        );
      })}

      {activeComment && (
        <div
          className="preview-comment-popover"
          style={getPopoverPositionStyle(activeComment.anchor)}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="preview-comment-popover-head">
            <span
              className={`preview-comment-status ${
                activeComment.status === 'addressed' ? 'addressed' : 'pending'
              }`}
            >
              {activeComment.status === 'addressed' ? (
                <CheckCircle2 size={12} />
              ) : (
                <Clock size={12} />
              )}
              {activeComment.status === 'addressed' ? 'Addressed' : 'Pending'}
            </span>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <button
                type="button"
                className="preview-comment-icon-btn danger"
                aria-label="Delete comment"
                onClick={() => void handleDelete(activeComment.comment_id)}
              >
                <Trash2 size={12} />
              </button>
              <button
                type="button"
                className="preview-comment-icon-btn"
                aria-label="Close comment"
                onClick={() => setActiveCommentId(null)}
              >
                <X size={13} />
              </button>
            </div>
          </div>
          <div className="preview-comment-popover-body">{activeComment.text}</div>
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
            style={getPopoverPositionStyle(draftAnchor)}
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              void submitDraftComment();
            }}
          >
            <textarea
              autoFocus
              rows={2}
              className="preview-comment-input"
              aria-label="Comment text"
              placeholder="Add comment for this spot..."
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void submitDraftComment();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setDraftAnchor(null);
                  setCommentText('');
                  setErrorMsg(null);
                }
              }}
              required
            />
            {errorMsg && <div className="preview-comment-error">{errorMsg}</div>}
            <div className="preview-comment-popover-actions">
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: 11.5, padding: '4px 9px' }}
                onClick={() => {
                  setDraftAnchor(null);
                  setCommentText('');
                  setErrorMsg(null);
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                style={{ fontSize: 11.5, padding: '4px 10px', borderRadius: 8 }}
                disabled={saving || !commentText.trim()}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
};
