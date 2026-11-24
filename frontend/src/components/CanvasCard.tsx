import React, { useRef, useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import type { ArtifactVersion } from '../types';

interface CanvasCardProps {
  version: ArtifactVersion;
  selected: boolean;
  canvasScale: number;
  onOpen: (versionId: string) => void;
  onToggleSelect: (versionId: string) => void;
  onDragMove: (versionId: string, x: number, y: number) => void;
  onDragEnd: (versionId: string, x: number, y: number) => void;
}

const DRAG_THRESHOLD_PX = 6;

export const CanvasCard: React.FC<CanvasCardProps> = ({
  version,
  selected,
  canvasScale,
  onOpen,
  onToggleSelect,
  onDragMove,
  onDragEnd,
}) => {
  const [dragging, setDragging] = useState(false);
  const dragStateRef = useRef<{
    active: boolean;
    moved: boolean;
    pointerId: number | null;
    startClientX: number;
    startClientY: number;
    startWorldX: number;
    startWorldY: number;
    lastWorldX: number;
    lastWorldY: number;
  }>({
    active: false,
    moved: false,
    pointerId: null,
    startClientX: 0,
    startClientY: 0,
    startWorldX: 0,
    startWorldY: 0,
    lastWorldX: 0,
    lastWorldY: 0,
  });
  const suppressClickRef = useRef(false);

  const hoverDetails = `${version.title} · Created ${new Date(
    version.created_at
  ).toLocaleString()} · Snapshots: ${
    version.dataset_snapshot_ids.join(', ') || 'none'
  }`;

  const handlePointerDown = (e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0 || e.ctrlKey) return;
    e.stopPropagation();

    const ds = dragStateRef.current;
    ds.active = true;
    ds.moved = false;
    ds.pointerId = e.pointerId;
    ds.startClientX = e.clientX;
    ds.startClientY = e.clientY;
    ds.startWorldX = version.canvas.x;
    ds.startWorldY = version.canvas.y;
    ds.lastWorldX = version.canvas.x;
    ds.lastWorldY = version.canvas.y;

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* ignore if synthetic event */
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLElement>) => {
    const ds = dragStateRef.current;
    if (!ds.active) return;

    const dx = e.clientX - ds.startClientX;
    const dy = e.clientY - ds.startClientY;

    if (!ds.moved && Math.hypot(dx, dy) >= DRAG_THRESHOLD_PX) {
      ds.moved = true;
      setDragging(true);
    }
    if (!ds.moved) return;

    const scale = Math.max(0.1, canvasScale);
    const nextX = Math.round(ds.startWorldX + dx / scale);
    const nextY = Math.round(ds.startWorldY + dy / scale);
    ds.lastWorldX = nextX;
    ds.lastWorldY = nextY;
    onDragMove(version.artifact_version_id, nextX, nextY);
  };

  const finishDrag = (e: React.PointerEvent<HTMLElement>) => {
    const ds = dragStateRef.current;
    if (!ds.active) return;
    ds.active = false;

    if (ds.pointerId !== null) {
      try {
        if (e.currentTarget.hasPointerCapture(ds.pointerId)) {
          e.currentTarget.releasePointerCapture(ds.pointerId);
        }
      } catch {
        /* ignore */
      }
    }
    ds.pointerId = null;

    if (ds.moved) {
      suppressClickRef.current = true;
      setDragging(false);
      onDragEnd(version.artifact_version_id, ds.lastWorldX, ds.lastWorldY);
      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    }
  };

  return (
    <article
      className={`canvas-card ${selected ? 'selected' : ''} ${dragging ? 'dragging' : ''}`}
      data-version-id={version.artifact_version_id}
      data-canvas-card="true"
      title={hoverDetails}
      style={{
        left: version.canvas.x,
        top: version.canvas.y,
        width: version.canvas.width,
        height: version.canvas.height,
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onClick={(e) => {
        e.stopPropagation();
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          return;
        }
        if (e.ctrlKey) return;
        if (e.shiftKey) {
          onToggleSelect(version.artifact_version_id);
          return;
        }
        onOpen(version.artifact_version_id);
      }}
      tabIndex={0}
      role="button"
      aria-label={`Open ${version.title} version ${version.version_number}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen(version.artifact_version_id);
        }
      }}
    >
      <div className="canvas-card-thumb-wrap">
        <img
          className="canvas-card-thumb"
          src={`/api/versions/${encodeURIComponent(version.artifact_version_id)}/thumbnail?t=${encodeURIComponent(
            version.created_at
          )}`}
          alt={`Validated preview of ${version.title} v${version.version_number}`}
          draggable={false}
        />

        {version.pending_comment_count > 0 && (
          <span
            className="canvas-card-comment-badge"
            data-pending-comments={version.pending_comment_count}
            title={`${version.pending_comment_count} pending visual comment(s)`}
          >
            <MessageSquareText size={12} />
            {version.pending_comment_count}
          </span>
        )}
      </div>

      <div className="canvas-card-meta">
        <div className="canvas-card-title-row">
          <span className="canvas-card-title">{version.title}</span>
        </div>
        <div className="canvas-card-subrow">
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {version.chart_kind}
          </span>
        </div>
      </div>
    </article>
  );
};
