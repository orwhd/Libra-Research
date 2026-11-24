import React, { useEffect, useRef, useState } from 'react';
import {
  Eye,
  GitBranch,
  MessageSquarePlus,
  Code2,
  Download,
  Trash2,
} from 'lucide-react';
import type { DockTab } from '../types';

interface ArtifactDockProps {
  artifactVersionId: string;
  activeTab: DockTab;
  commentPinMode: boolean;
  pendingCommentsCount: number;
  onSelectTab: (tab: DockTab) => void;
  onToggleCommentMode: () => void;
  onDelete: () => void;
  onStatusAnnouncement?: (msg: string) => void;
}

export const ArtifactDock: React.FC<ArtifactDockProps> = ({
  artifactVersionId,
  activeTab,
  commentPinMode,
  pendingCommentsCount,
  onSelectTab,
  onToggleCommentMode,
  onDelete,
  onStatusAnnouncement,
}) => {
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [downloadOpen, setDownloadOpen] = useState(false);

  // Roving tabindex fallback when native CSS/HTML `focusgroup` is unsupported
  useEffect(() => {
    const bar = toolbarRef.current;
    if (!bar) return;
    if ('focusGroup' in HTMLElement.prototype) return;

    const buttons = Array.from(bar.querySelectorAll<HTMLButtonElement>('button'));
    buttons.forEach((b, i) => {
      b.tabIndex = i === 0 ? 0 : -1;
    });

    const onFocusIn = (e: FocusEvent) => {
      const target = e.target as HTMLButtonElement;
      if (buttons.includes(target)) {
        buttons.forEach((b) => {
          b.tabIndex = b === target ? 0 : -1;
        });
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) return;
      const curIdx = buttons.findIndex((b) => b === document.activeElement);
      if (curIdx === -1) return;
      e.preventDefault();
      let nextIdx = curIdx;
      if (e.key === 'ArrowRight') nextIdx = (curIdx + 1) % buttons.length;
      else if (e.key === 'ArrowLeft') nextIdx = (curIdx - 1 + buttons.length) % buttons.length;
      else if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = buttons.length - 1;
      buttons[nextIdx]?.focus();
    };

    bar.addEventListener('focusin', onFocusIn);
    bar.addEventListener('keydown', onKeyDown);
    return () => {
      bar.removeEventListener('focusin', onFocusIn);
      bar.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  return (
    <div className="artifact-dock-wrap">
      <div
        ref={toolbarRef}
        role="toolbar"
        {...({ focusgroup: 'toolbar wrap' } as Record<string, string>)}
        aria-label="Artifact Dock Controls"
        className="artifact-dock"
      >
        <div role="group" aria-label="Inspection Views">
          <button
            type="button"
            aria-label="Preview"
            aria-pressed={activeTab === 'preview'}
            onClick={() => onSelectTab('preview')}
          >
            <Eye size={14} aria-hidden="true" />
            <span className="dock-tab-label">Preview</span>
          </button>
          <button
            type="button"
            aria-label="Data Map"
            aria-pressed={activeTab === 'data_map'}
            onClick={() => onSelectTab('data_map')}
          >
            <GitBranch size={14} aria-hidden="true" />
            <span className="dock-tab-label">Data Map</span>
          </button>
        </div>

        <span role="separator" aria-orientation="vertical" />

        <div role="group" aria-label="Source and Export Actions">
          <button
            type="button"
            className="dock-icon-btn"
            aria-label={
              pendingCommentsCount > 0
                ? `Comment on preview (${pendingCommentsCount} pending)`
                : 'Comment on preview'
            }
            aria-pressed={commentPinMode}
            onClick={onToggleCommentMode}
          >
            <MessageSquarePlus size={15} aria-hidden="true" />
          </button>

          <button
            type="button"
            className="dock-icon-btn"
            aria-label="View Source"
            aria-pressed={activeTab === 'source'}
            onClick={() => onSelectTab('source')}
          >
            <Code2 size={15} aria-hidden="true" />
          </button>

          <div className="download-popover-wrap">
            <button
              type="button"
              className="dock-icon-btn"
              aria-label="Download"
              aria-haspopup="menu"
              aria-expanded={downloadOpen}
              onClick={() => setDownloadOpen((o) => !o)}
            >
              <Download size={15} aria-hidden="true" />
            </button>
            {downloadOpen && (
              <div className="download-menu" role="menu">
                <a
                  href={`/api/versions/${encodeURIComponent(artifactVersionId)}/download?format=html`}
                  download
                  role="menuitem"
                  onClick={() => {
                    setDownloadOpen(false);
                    onStatusAnnouncement?.('Standalone HTML download started.');
                  }}
                >
                  Standalone Interactive HTML (.html)
                </a>
                <a
                  href={`/api/versions/${encodeURIComponent(artifactVersionId)}/download?format=zip`}
                  download
                  role="menuitem"
                  onClick={() => {
                    setDownloadOpen(false);
                    onStatusAnnouncement?.('Full provenance bundle ZIP download started.');
                  }}
                >
                  Full Data &amp; Lineage Archive (.zip)
                </a>
              </div>
            )}
          </div>

          <button
            type="button"
            className="dock-icon-btn dock-delete-btn"
            aria-label="Delete artifact"
            onClick={onDelete}
          >
            <Trash2 size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
};
