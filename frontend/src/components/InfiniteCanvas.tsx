import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, RotateCcw, Columns2, Eye, Trash2, Check } from 'lucide-react';
import type { ArtifactVersion } from '../types';
import { CanvasCard } from './CanvasCard';

interface InfiniteCanvasProps {
  versions: ArtifactVersion[];
  selectedIds: string[];
  onChangeSelection: (ids: string[]) => void;
  onOpenSingle: (versionId: string) => void;
  onOpenCompare: (leftId: string, rightId: string) => void;
  onMoveVersion: (versionId: string, x: number, y: number, commit: boolean) => void;
  onDeleteVersion?: (versionId: string) => void;
}

const MOVE_THRESHOLD_PX = 5;

function computeAdaptiveEdgePath(parent: ArtifactVersion, child: ArtifactVersion): string {
  const pcx = parent.canvas.x + parent.canvas.width / 2;
  const pcy = parent.canvas.y + parent.canvas.height / 2;
  const ccx = child.canvas.x + child.canvas.width / 2;
  const ccy = child.canvas.y + child.canvas.height / 2;
  const dx = ccx - pcx;
  const dy = ccy - pcy;

  if (Math.abs(dx) >= Math.abs(dy)) {
    if (dx >= 0) {
      const x1 = parent.canvas.x + parent.canvas.width;
      const y1 = pcy;
      const x2 = child.canvas.x - 4;
      const y2 = ccy;
      const mx = (x1 + x2) / 2;
      return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    }
    const x1 = parent.canvas.x;
    const y1 = pcy;
    const x2 = child.canvas.x + child.canvas.width + 4;
    const y2 = ccy;
    const mx = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
  }

  if (dy >= 0) {
    const x1 = pcx;
    const y1 = parent.canvas.y + parent.canvas.height;
    const x2 = ccx;
    const y2 = child.canvas.y - 4;
    const my = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
  }

  const x1 = pcx;
  const y1 = parent.canvas.y;
  const x2 = ccx;
  const y2 = child.canvas.y + child.canvas.height + 4;
  const my = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
}

export const InfiniteCanvas: React.FC<InfiniteCanvasProps> = ({
  versions,
  selectedIds,
  onChangeSelection,
  onOpenSingle,
  onOpenCompare,
  onMoveVersion,
  onDeleteVersion,
}) => {
  const rootRef = useRef<HTMLDivElement>(null);

  const [transform, setTransform] = useState({ x: 30, y: 30, scale: 0.92 });
  const [marquee, setMarquee] = useState<{
    startX: number;
    startY: number;
    curX: number;
    curY: number;
  } | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    cardId: string | null;
  } | null>(null);

  const interactionRef = useRef<{
    mode: 'idle' | 'left_pending' | 'panning' | 'right_pending' | 'marquee';
    startClientX: number;
    startClientY: number;
    startPanX: number;
    startPanY: number;
    pointerId: number | null;
    clickedCardId: string | null;
    lastMarqueeEndTs: number;
    lastMenuOpenTs: number;
  }>({
    mode: 'idle',
    startClientX: 0,
    startClientY: 0,
    startPanX: 0,
    startPanY: 0,
    pointerId: null,
    clickedCardId: null,
    lastMarqueeEndTs: 0,
    lastMenuOpenTs: 0,
  });

  const openMenuAt = useCallback((clientX: number, clientY: number, cardId: string | null) => {
    const x = Math.max(8, Math.min(clientX, window.innerWidth - 228));
    const y = Math.max(8, Math.min(clientY, window.innerHeight - 196));
    interactionRef.current.lastMenuOpenTs = Date.now();
    setContextMenu({ x, y, cardId });
  }, []);

  const fitAll = useCallback(() => {
    const el = rootRef.current;
    if (!el || versions.length === 0) {
      setTransform({ x: 32, y: 32, scale: 0.92 });
      return;
    }
    const minX = Math.min(...versions.map((v) => v.canvas.x));
    const minY = Math.min(...versions.map((v) => v.canvas.y));
    const maxX = Math.max(...versions.map((v) => v.canvas.x + v.canvas.width));
    const maxY = Math.max(...versions.map((v) => v.canvas.y + v.canvas.height));
    const pad = 64;
    const contentW = Math.max(400, maxX - minX + pad * 2);
    const contentH = Math.max(300, maxY - minY + pad * 2);
    const rect = el.getBoundingClientRect();
    const scale = Math.max(0.32, Math.min(1.05, Math.min(rect.width / contentW, rect.height / contentH)));
    setTransform({
      x: (rect.width - (maxX - minX) * scale) / 2 - minX * scale,
      y: Math.max(36, (rect.height - (maxY - minY) * scale) / 2 - minY * scale),
      scale,
    });
  }, [versions]);

  // Fit newly arrived first artifacts cleanly without overriding user's manual navigation later
  const prevCountRef = useRef(0);
  useEffect(() => {
    if (versions.length > 0 && prevCountRef.current === 0) {
      fitAll();
    }
    prevCountRef.current = versions.length;
  }, [versions.length, fitAll]);

  // Esc key & outside pointerdown close context menu / clear selection
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (contextMenu) {
          setContextMenu(null);
        } else if (marquee) {
          setMarquee(null);
        } else if (selectedIds.length > 0) {
          onChangeSelection([]);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [contextMenu, marquee, selectedIds.length, onChangeSelection]);

  useEffect(() => {
    if (!contextMenu) return;
    const onOutsidePointerDown = (e: PointerEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest('.rc-context-menu-floating')) return;
      setContextMenu(null);
    };
    window.addEventListener('pointerdown', onOutsidePointerDown);
    return () => window.removeEventListener('pointerdown', onOutsidePointerDown);
  }, [contextMenu]);

  const computeMarqueeIntersections = useCallback(
    (box: { startX: number; startY: number; curX: number; curY: number }) => {
      const root = rootRef.current;
      if (!root) return [];
      const rect = root.getBoundingClientRect();
      const left = Math.min(box.startX, box.curX) - rect.left;
      const right = Math.max(box.startX, box.curX) - rect.left;
      const top = Math.min(box.startY, box.curY) - rect.top;
      const bottom = Math.max(box.startY, box.curY) - rect.top;

      // Convert screen rect to world coordinates
      const wLeft = (left - transform.x) / transform.scale;
      const wRight = (right - transform.x) / transform.scale;
      const wTop = (top - transform.y) / transform.scale;
      const wBottom = (bottom - transform.y) / transform.scale;

      return versions
        .filter((v) => {
          const vx1 = v.canvas.x;
          const vx2 = v.canvas.x + v.canvas.width;
          const vy1 = v.canvas.y;
          const vy2 = v.canvas.y + v.canvas.height;
          return !(vx2 < wLeft || vx1 > wRight || vy2 < wTop || vy1 > wBottom);
        })
        .map((v) => v.artifact_version_id);
    },
    [transform, versions]
  );

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const targetEl = e.target as HTMLElement;
    if (
      targetEl.closest('.canvas-hud-top') ||
      targetEl.closest('.rc-context-menu-floating')
    ) {
      return;
    }

    setContextMenu(null);

    const isRightButton = e.button === 2 || (e.button === 0 && e.ctrlKey);
    const st = interactionRef.current;

    if (isRightButton) {
      const cardEl = targetEl.closest('[data-canvas-card="true"]');
      st.mode = 'right_pending';
      st.startClientX = e.clientX;
      st.startClientY = e.clientY;
      st.pointerId = e.pointerId;
      st.clickedCardId = cardEl?.getAttribute('data-version-id') || null;
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* ignore if synthetic event */
      }
      return;
    }

    if (e.button !== 0 || targetEl.closest('[data-canvas-card="true"]')) {
      return;
    }

    st.startClientX = e.clientX;
    st.startClientY = e.clientY;
    st.startPanX = transform.x;
    st.startPanY = transform.y;
    st.pointerId = e.pointerId;
    st.clickedCardId = null;

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* ignore if synthetic event */
    }

    if (e.shiftKey) {
      st.mode = 'marquee';
      setMarquee({
        startX: e.clientX,
        startY: e.clientY,
        curX: e.clientX,
        curY: e.clientY,
      });
      return;
    }

    st.mode = 'left_pending';
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = interactionRef.current;
    if (st.mode === 'idle') return;

    const dx = e.clientX - st.startClientX;
    const dy = e.clientY - st.startClientY;
    const dist = Math.hypot(dx, dy);

    if (st.mode === 'left_pending') {
      if (dist >= MOVE_THRESHOLD_PX) {
        st.mode = 'panning';
      } else {
        return;
      }
    } else if (st.mode === 'right_pending') {
      if (dist >= MOVE_THRESHOLD_PX) {
        st.mode = 'marquee';
        setContextMenu(null);
      } else {
        return;
      }
    }

    if (st.mode === 'panning') {
      setTransform((prev) => ({
        ...prev,
        x: st.startPanX + dx,
        y: st.startPanY + dy,
      }));
    } else if (st.mode === 'marquee') {
      const nextBox = {
        startX: st.startClientX,
        startY: st.startClientY,
        curX: e.clientX,
        curY: e.clientY,
      };
      setMarquee(nextBox);
      onChangeSelection(computeMarqueeIntersections(nextBox));
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = interactionRef.current;
    if (st.pointerId !== null) {
      try {
        if (e.currentTarget.hasPointerCapture(st.pointerId)) {
          e.currentTarget.releasePointerCapture(st.pointerId);
        }
      } catch {
        /* ignore */
      }
    }

    if (st.mode === 'left_pending') {
      onChangeSelection([]);
    } else if (st.mode === 'right_pending') {
      openMenuAt(e.clientX, e.clientY, st.clickedCardId);
    } else if (st.mode === 'marquee') {
      st.lastMarqueeEndTs = Date.now();
    }

    st.mode = 'idle';
    st.pointerId = null;
    st.clickedCardId = null;
    setMarquee(null);
  };

  const handleContextMenu = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    const st = interactionRef.current;
    const now = Date.now();
    if (st.mode === 'idle' && now - st.lastMarqueeEndTs > 140 && now - st.lastMenuOpenTs > 80) {
      const targetEl = e.target as HTMLElement;
      if (
        targetEl.closest('.rc-context-menu-floating') ||
        targetEl.closest('.canvas-hud-top')
      ) {
        return;
      }
      const cardEl = targetEl.closest('[data-canvas-card="true"]');
      const cardId = cardEl?.getAttribute('data-version-id') || null;
      openMenuAt(e.clientX, e.clientY, cardId);
    }
  };

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = root.getBoundingClientRect();
      const pointerX = e.clientX - rect.left;
      const pointerY = e.clientY - rect.top;

      if (e.ctrlKey || e.metaKey) {
        // Cmd / Ctrl + wheel (or trackpad pinch) zooms around pointer
        const factor = e.deltaY < 0 ? 1.08 : 0.925;
        setTransform((prev) => {
          const nextScale = Math.max(0.25, Math.min(2.5, prev.scale * factor));
          const ratio = nextScale / prev.scale;
          return {
            scale: nextScale,
            x: pointerX - (pointerX - prev.x) * ratio,
            y: pointerY - (pointerY - prev.y) * ratio,
          };
        });
      } else {
        // Standard scroll wheel / two-finger swipe scrolls/pans the canvas
        const dx = e.shiftKey && e.deltaX === 0 ? e.deltaY : e.deltaX;
        const dy = e.shiftKey && e.deltaX === 0 ? 0 : e.deltaY;
        setTransform((prev) => ({
          ...prev,
          x: prev.x - dx,
          y: prev.y - dy,
        }));
      }
    };

    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, []);

  // Directed parent -> child edges
  const versionById = useMemo(
    () => new Map(versions.map((v) => [v.artifact_version_id, v])),
    [versions]
  );

  const compareDisabled = selectedIds.length < 2;
  const compareReason =
    selectedIds.length === 0
      ? 'Right-drag to box-select artifacts'
      : selectedIds.length === 1
        ? 'Select at least 2 artifacts to Compare'
        : 'Open selected artifacts side-by-side';

  const gridSize = 24 * transform.scale;

  return (
    <div
      ref={rootRef}
      className="canvas-root"
      data-testid="infinite-canvas"
      style={{
        backgroundPosition: `${transform.x}px ${transform.y}px`,
        backgroundSize: `${gridSize}px ${gridSize}px`,
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onContextMenu={handleContextMenu}
    >
      <div className="canvas-hud-top">
        {selectedIds.length > 0 && (
          <div className="canvas-hud-pill">
            <strong>{selectedIds.length} selected</strong>
            <button
              type="button"
              className="btn-secondary"
              style={{ padding: '3px 8px', fontSize: 11.5 }}
              aria-disabled={compareDisabled ? 'true' : undefined}
              onClick={() => {
                if (selectedIds.length >= 2) {
                  onOpenCompare(selectedIds[0], selectedIds[1]);
                }
              }}
              title={compareReason}
            >
              <Columns2 size={13} />
              Compare
            </button>
          </div>
        )}
        <div className="canvas-hud-pill">
          <span>{Math.round(transform.scale * 100)}%</span>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '3px 8px', fontSize: 11.5 }}
            onClick={fitAll}
          >
            <Maximize2 size={12} />
            Fit All
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '3px 8px', fontSize: 11.5 }}
            onClick={() => setTransform({ x: 32, y: 32, scale: 1 })}
          >
            <RotateCcw size={12} />
            100%
          </button>
        </div>
      </div>

      <div className="canvas-world" style={{ transform: `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})` }}>
        <div className="canvas-origin-marker" aria-hidden="true" />
        <svg className="canvas-edges-svg" aria-hidden="true">
          <defs>
            <marker
              id="rc-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="6"
              refY="4"
              orient="auto"
            >
              <path d="M0,1 L7,4 L0,7 Z" fill="#2563eb" />
            </marker>
          </defs>
          {versions.map((v) => {
            if (!v.parent_version_id) return null;
            const parent = versionById.get(v.parent_version_id);
            if (!parent) return null;
            return (
              <path
                key={`edge_${parent.artifact_version_id}_${v.artifact_version_id}`}
                d={computeAdaptiveEdgePath(parent, v)}
                fill="none"
                stroke="#2563eb"
                strokeWidth="2"
                strokeDasharray="5 3"
                markerEnd="url(#rc-arrow)"
              />
            );
          })}
        </svg>

        {versions.map((v) => (
          <CanvasCard
            key={v.artifact_version_id}
            version={v}
            selected={selectedIds.includes(v.artifact_version_id)}
            canvasScale={transform.scale}
            onOpen={onOpenSingle}
            onToggleSelect={(vid) =>
              onChangeSelection(
                selectedIds.includes(vid)
                  ? selectedIds.filter((id) => id !== vid)
                  : [...selectedIds, vid]
              )
            }
            onDragMove={(vid, x, y) => onMoveVersion(vid, x, y, false)}
            onDragEnd={(vid, x, y) => onMoveVersion(vid, x, y, true)}
          />
        ))}
      </div>

      {marquee && (
        <div
          className="canvas-marquee-box"
          style={{
            left: Math.min(marquee.startX, marquee.curX),
            top: Math.min(marquee.startY, marquee.curY),
            width: Math.abs(marquee.curX - marquee.startX),
            height: Math.abs(marquee.curY - marquee.startY),
          }}
        />
      )}

      <div className="canvas-hint-bottom">
        Drag or scroll to pan · ⌘/Ctrl + Wheel to zoom · Right-drag to box-select · Right-click for menu
      </div>

      {contextMenu && (() => {
        const cardId = contextMenu.cardId;
        const canComparePair = selectedIds.length >= 2;
        const canCompareWithCard =
          Boolean(cardId) && selectedIds.length === 1 && selectedIds[0] !== cardId;
        const canCompare = canComparePair || canCompareWithCard;
        const isCardSelected = cardId ? selectedIds.includes(cardId) : false;

        return (
          <div
            role="menu"
            aria-label="Canvas Context Menu"
            className="rc-context-menu-floating"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onPointerDown={(e) => e.stopPropagation()}
            onPointerUp={(e) => e.stopPropagation()}
            onContextMenu={(e) => e.preventDefault()}
          >
            <div className="rc-context-menu-list">
              <button
                type="button"
                role="menuitem"
                className="rc-context-item"
                aria-disabled={!canCompare ? 'true' : undefined}
                onClick={() => {
                  if (!canCompare) return;
                  setContextMenu(null);
                  if (canComparePair) {
                    onOpenCompare(selectedIds[0], selectedIds[1]);
                  } else if (cardId) {
                    onOpenCompare(selectedIds[0], cardId);
                  }
                }}
              >
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    color: canCompare ? 'var(--accent)' : 'inherit',
                    fontWeight: canCompare ? 700 : 500,
                  }}
                >
                  <Columns2 size={14} />
                  {canComparePair
                    ? `Compare (${selectedIds.length} selected)`
                    : canCompareWithCard
                      ? 'Compare with Selected'
                      : 'Compare'}
                </span>
                {!canCompare && (
                  <span className="rc-context-sub">Right-drag to box-select 2+ cards</span>
                )}
              </button>

              {cardId && (
                <>
                  <div className="rc-context-sep" role="separator" />
                  <button
                    type="button"
                    role="menuitem"
                    className="rc-context-item"
                    onClick={() => {
                      setContextMenu(null);
                      onOpenSingle(cardId);
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <Eye size={14} />
                      Open Artifact
                    </span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="rc-context-item"
                    onClick={() => {
                      setContextMenu(null);
                      onChangeSelection(
                        isCardSelected
                          ? selectedIds.filter((id) => id !== cardId)
                          : [...selectedIds, cardId]
                      );
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <Check size={14} />
                      {isCardSelected ? 'Deselect Card' : 'Select Card'}
                    </span>
                  </button>
                  {onDeleteVersion && (
                    <button
                      type="button"
                      role="menuitem"
                      className="rc-context-item rc-context-danger"
                      onClick={() => {
                        setContextMenu(null);
                        onDeleteVersion(cardId);
                      }}
                    >
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <Trash2 size={14} />
                        Delete Artifact
                      </span>
                    </button>
                  )}
                </>
              )}

              <div className="rc-context-sep" role="separator" />

              {selectedIds.length > 0 && (
                <button
                  type="button"
                  role="menuitem"
                  className="rc-context-item"
                  onClick={() => {
                    setContextMenu(null);
                    onChangeSelection([]);
                  }}
                >
                  <span>Clear Selection ({selectedIds.length})</span>
                </button>
              )}

              <button
                type="button"
                role="menuitem"
                className="rc-context-item"
                onClick={() => {
                  setContextMenu(null);
                  fitAll();
                }}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <Maximize2 size={14} />
                  Fit All
                </span>
              </button>

              <button
                type="button"
                role="menuitem"
                className="rc-context-item"
                onClick={() => {
                  setContextMenu(null);
                  setTransform({ x: 32, y: 32, scale: 1 });
                }}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <RotateCcw size={14} />
                  Reset Zoom (100%)
                </span>
              </button>
            </div>
          </div>
        );
      })()}
    </div>
  );
};
