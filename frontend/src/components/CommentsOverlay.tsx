import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

function getScrollLockedPinStyle(
  anchor: AnchorPoint,
  dx: number,
  dy: number
): React.CSSProperties {
  return {
    left: `calc(${(anchor.x_norm * 100).toFixed(3)}% - ${Math.round(dx)}px)`,
    top: `calc(${(anchor.y_norm * 100).toFixed(3)}% - ${Math.round(dy)}px)`,
  };
}

function getPopoverPositionStyle(
  anchor: AnchorPoint,
  dx: number,
  dy: number
): React.CSSProperties {
  const openLeft = anchor.x_norm > 0.62;
  const openUp = anchor.y_norm > 0.65;
  const rdx = Math.round(dx);
  const rdy = Math.round(dy);

  return {
    ...(openLeft
      ? { right: `calc(${((1 - anchor.x_norm) * 100).toFixed(2)}% + ${rdx}px + 16px)` }
      : { left: `calc(${(anchor.x_norm * 100).toFixed(2)}% - ${rdx}px + 16px)` }),
    ...(openUp
      ? { bottom: `calc(${((1 - anchor.y_norm) * 100).toFixed(2)}% + ${rdy}px + 12px)` }
      : { top: `calc(${(anchor.y_norm * 100).toFixed(2)}% - ${rdy}px - 8px)` }),
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
  const pendingReqRef = useRef<{
    id: string;
    resolve: (res: { viewState: PreviewViewState; elementHint: string | null }) => void;
  } | null>(null);

  const [activeCommentId, setActiveCommentId] = useState<string | null>(null);
  const [iframeScroll, setIframeScroll] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [optimisticComments, setOptimisticComments] = useState<VisualComment[]>([]);
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
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    setIframeScroll({ x: 0, y: 0 });
    setOptimisticComments([]);
    setDraftAnchor(null);
    setActiveCommentId(null);
  }, [version.artifact_version_id]);

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

  const handleScrollStateChange = useCallback((scrollX: number, scrollY: number) => {
    setIframeScroll((prev) =>
      prev.x === scrollX && prev.y === scrollY ? prev : { x: scrollX, y: scrollY }
    );
  }, []);

  const handleViewStateReceived = useCallback(
    (viewState: PreviewViewState, elementHint: string | null, requestId: string | null) => {
      setDraftViewState(viewState);
      if (elementHint) setDraftHint(elementHint);
      if (pendingReqRef.current && (!requestId || pendingReqRef.current.id === requestId)) {
        pendingReqRef.current.resolve({ viewState, elementHint });
        pendingReqRef.current = null;
      }
    },
    []
  );

  const handleStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!pinMode || !stageRef.current) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x_norm = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
    const y_norm = Math.max(0, Math.min(1, (e.clientY - rect.top) / Math.max(1, rect.height)));
    const initialViewState: PreviewViewState = {
      viewport_width: Math.round(rect.width),
      viewport_height: Math.round(rect.height),
      scroll_x: iframeScroll.x,
      scroll_y: iframeScroll.y,
      zoom: 1,
      filter_state: {},
    };

    setActiveCommentId(null);
    setErrorMsg(null);
    setDraftAnchor({ x_norm, y_norm });
    setDraftHint(null);
    setDraftViewState(initialViewState);
    onExitPinMode();

    const reqId = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    try {
      iframeRef.current?.contentWindow?.postMessage(
        { type: 'RC_REQUEST_VIEW_STATE', x_norm, y_norm, request_id: reqId },
        '*'
      );
    } catch {
      /* ignore if sandboxed iframe not ready */
    }
  };

  const submitDraftComment = async () => {
    const trimmed = commentText.trim();
    if (!draftAnchor || !trimmed) return;

    const capturedAnchor = draftAnchor;
    let capturedViewState = draftViewState;
    let capturedHint = draftHint;

    // Optimistically close form & render saved pin IMMEDIATELY (zero perceived latency)
    const tempId = `optimistic_${Date.now()}`;
    const optimisticPin: VisualComment = {
      comment_id: tempId,
      artifact_version_id: version.artifact_version_id,
      workspace_id: version.workspace_id,
      text: trimmed,
      anchor: capturedAnchor,
      screenshot_ref: '',
      preview_view_state: capturedViewState,
      optional_element_hint: capturedHint,
      status: 'pending',
      resolved_in_version_id: null,
      created_at: new Date().toISOString(),
    };

    setOptimisticComments((prev) => [...prev, optimisticPin]);
    setCommentText('');
    setDraftAnchor(null);
    setDraftHint(null);
    setErrorMsg(null);

    // If bridge target inspection hasn't arrived yet, wait briefly (<=90ms) in background before POSTing
    if (!capturedHint && iframeRef.current?.contentWindow) {
      const reqId = `save_${Date.now()}`;
      const awaited = await new Promise<{
        viewState: PreviewViewState;
        elementHint: string | null;
      } | null>((resolve) => {
        const timer = window.setTimeout(() => {
          if (pendingReqRef.current?.id === reqId) pendingReqRef.current = null;
          resolve(null);
        }, 90);
        pendingReqRef.current = {
          id: reqId,
          resolve: (val) => {
            window.clearTimeout(timer);
            resolve(val);
          },
        };
        try {
          iframeRef.current?.contentWindow?.postMessage(
            {
              type: 'RC_REQUEST_VIEW_STATE',
              x_norm: capturedAnchor.x_norm,
              y_norm: capturedAnchor.y_norm,
              request_id: reqId,
            },
            '*'
          );
        } catch {
          window.clearTimeout(timer);
          resolve(null);
        }
      });
      if (awaited) {
        capturedViewState = awaited.viewState;
        capturedHint = awaited.elementHint;
      }
    }

    try {
      await createVersionComment(version.artifact_version_id, {
        text: trimmed,
        anchor: capturedAnchor,
        preview_view_state: capturedViewState,
        optional_element_hint: capturedHint,
      });
      await onCommentChanged();
    } catch (err) {
      // Rollback optimistic pin and restore draft if network/server failed
      setOptimisticComments((prev) => prev.filter((c) => c.comment_id !== tempId));
      setDraftAnchor(capturedAnchor);
      setDraftViewState(capturedViewState);
      setDraftHint(capturedHint);
      setCommentText(trimmed);
      setErrorMsg(err instanceof Error ? err.message : String(err));
      return;
    }

    setOptimisticComments((prev) => prev.filter((c) => c.comment_id !== tempId));
  };

  const handleDelete = async (commentId: string) => {
    if (commentId.startsWith('optimistic_')) {
      setOptimisticComments((prev) => prev.filter((c) => c.comment_id !== commentId));
      if (activeCommentId === commentId) setActiveCommentId(null);
      return;
    }
    if (activeCommentId === commentId) {
      setActiveCommentId(null);
    }
    await deleteComment(commentId);
    await onCommentChanged();
  };

  const previewComments = useMemo(() => {
    const persisted = comments.filter(
      (c) =>
        (c.preview_view_state?.filter_state as Record<string, unknown> | undefined)?.surface !==
        'data_map'
    );
    const pendingOpt = optimisticComments.filter(
      (opt) =>
        !persisted.some(
          (p) =>
            p.text === opt.text &&
            Math.abs(p.anchor.x_norm - opt.anchor.x_norm) < 0.002 &&
            Math.abs(p.anchor.y_norm - opt.anchor.y_norm) < 0.002
        )
    );
    return [...persisted, ...pendingOpt];
  }, [comments, optimisticComments]);

  const activeComment = previewComments.find((c) => c.comment_id === activeCommentId) || null;

  const getDeltasForState = (pvs?: PreviewViewState) => ({
    dx: iframeScroll.x - (pvs?.scroll_x || 0),
    dy: iframeScroll.y - (pvs?.scroll_y || 0),
  });

  return (
    <div className="preview-stage-wrap" ref={stageRef}>
      <PreviewFrame
        version={version}
        iframeRef={iframeRef}
        onViewStateReceived={handleViewStateReceived}
        onScrollStateChange={handleScrollStateChange}
      />

      {pinMode && (
        <div
          className="pin-capture-overlay"
          data-testid="pin-capture-overlay"
          onClick={handleStageClick}
          title="Click any visual element on the chart to anchor your comment"
        />
      )}

      {previewComments.map((c) => {
        const isOpen = activeCommentId === c.comment_id;
        const { dx, dy } = getDeltasForState(c.preview_view_state);
        return (
          <button
            key={c.comment_id}
            type="button"
            className={`preview-comment-pin ${c.status === 'addressed' ? 'addressed' : ''} ${
              isOpen ? 'open' : ''
            }`}
            style={getScrollLockedPinStyle(c.anchor, dx, dy)}
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

      {activeComment && (() => {
        const { dx, dy } = getDeltasForState(activeComment.preview_view_state);
        return (
          <div
            className="preview-comment-popover"
            style={getPopoverPositionStyle(activeComment.anchor, dx, dy)}
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
                {activeComment.status !== 'addressed' && (
                  <button
                    type="button"
                    className="preview-comment-icon-btn danger"
                    aria-label="Delete comment"
                    onClick={() => void handleDelete(activeComment.comment_id)}
                  >
                    <Trash2 size={12} />
                  </button>
                )}
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
            <div className="preview-comment-popover-body">{activeComment.text}</div>
          </div>
        );
      })()}

      {draftAnchor && (() => {
        const { dx, dy } = getDeltasForState(draftViewState);
        return (
          <>
            <div
              className="preview-comment-pin draft"
              style={getScrollLockedPinStyle(draftAnchor, dx, dy)}
            >
              <MessageSquare size={13} />
            </div>

            <form
              className="preview-comment-popover"
              style={getPopoverPositionStyle(draftAnchor, dx, dy)}
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
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
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
                  disabled={!commentText.trim()}
                >
                  Save
                </button>
              </div>
            </form>
          </>
        );
      })()}
    </div>
  );
};
