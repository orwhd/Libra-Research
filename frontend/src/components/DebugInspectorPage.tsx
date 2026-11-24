import React, { useEffect, useState } from 'react';
import { ArrowLeft, Columns2, ExternalLink, LayoutTemplate } from 'lucide-react';
import type { Workspace, WorkspaceDetailResponse } from '../types';
import { ArtifactWindow } from './ArtifactWindow';

interface DebugInspectorPageProps {
  workspaces: Workspace[];
  detail: WorkspaceDetailResponse | null;
  initialVersionId: string | null;
  initialCompareIds: [string, string] | null;
  onSelectWorkspace: (wsId: string) => Promise<void>;
  onCommentChanged: () => Promise<void>;
  onDeleteVersion: (versionId: string) => Promise<void>;
  onExitDebug: () => void;
}

export const DebugInspectorPage: React.FC<DebugInspectorPageProps> = ({
  workspaces,
  detail,
  initialVersionId,
  initialCompareIds,
  onSelectWorkspace,
  onCommentChanged,
  onDeleteVersion,
  onExitDebug,
}) => {
  const versions = detail?.artifact_versions || [];
  const [viewMode, setViewMode] = useState<'single' | 'compare'>(
    initialCompareIds && versions.length >= 2 ? 'compare' : 'single'
  );
  const [primaryVersionId, setPrimaryVersionId] = useState<string>(
    initialVersionId || initialCompareIds?.[0] || versions[0]?.artifact_version_id || ''
  );
  const [secondaryVersionId, setSecondaryVersionId] = useState<string>(
    initialCompareIds?.[1] || versions[1]?.artifact_version_id || versions[0]?.artifact_version_id || ''
  );

  // Keep selected versions synchronized when workspace changes or loads
  useEffect(() => {
    if (versions.length === 0) {
      setPrimaryVersionId('');
      setSecondaryVersionId('');
      setViewMode('single');
      return;
    }
    if (!versions.some((v) => v.artifact_version_id === primaryVersionId)) {
      const preferred =
        (initialVersionId && versions.some((v) => v.artifact_version_id === initialVersionId)
          ? initialVersionId
          : versions[0].artifact_version_id);
      setPrimaryVersionId(preferred);
    }
    if (!versions.some((v) => v.artifact_version_id === secondaryVersionId)) {
      const preferredRight =
        (initialCompareIds?.[1] &&
        versions.some((v) => v.artifact_version_id === initialCompareIds[1])
          ? initialCompareIds[1]
          : versions[1]?.artifact_version_id || versions[0].artifact_version_id);
      setSecondaryVersionId(preferredRight);
    }
  }, [detail?.workspace.workspace_id, versions, primaryVersionId, secondaryVersionId, initialVersionId, initialCompareIds]);

  const primaryVersion =
    versions.find((v) => v.artifact_version_id === primaryVersionId) || versions[0];
  const secondaryVersion =
    versions.find((v) => v.artifact_version_id === secondaryVersionId) ||
    versions[1] ||
    versions[0];

  const commentsByVersion = detail?.comments_by_version || {};

  return (
    <div className="debug-page-shell" data-testid="debug-direct-page">
      <header className="debug-topbar">
        <div className="debug-topbar-left">
          <button type="button" className="btn-secondary debug-btn" onClick={onExitDebug}>
            <ArrowLeft size={14} />
            Exit Debug
          </button>

          <label className="debug-field">
            <span className="debug-field-label">Workspace</span>
            <select
              className="debug-select"
              aria-label="Debug Workspace Selector"
              value={detail?.workspace.workspace_id || ''}
              onChange={(e) => {
                if (e.target.value) void onSelectWorkspace(e.target.value);
              }}
            >
              {workspaces.map((ws) => (
                <option key={ws.workspace_id} value={ws.workspace_id}>
                  {ws.title}
                </option>
              ))}
            </select>
          </label>

          {versions.length > 0 && (
            <label className="debug-field">
              <span className="debug-field-label">Artifact</span>
              <select
                className="debug-select"
                aria-label="Debug Primary Artifact Selector"
                value={primaryVersion?.artifact_version_id || ''}
                onChange={(e) => setPrimaryVersionId(e.target.value)}
              >
                {versions.map((v) => (
                  <option key={v.artifact_version_id} value={v.artifact_version_id}>
                    {v.title} (v{v.version_number})
                  </option>
                ))}
              </select>
            </label>
          )}

          {viewMode === 'compare' && versions.length >= 2 && (
            <label className="debug-field">
              <span className="debug-field-label">Compare With</span>
              <select
                className="debug-select"
                aria-label="Debug Secondary Artifact Selector"
                value={secondaryVersion?.artifact_version_id || ''}
                onChange={(e) => setSecondaryVersionId(e.target.value)}
              >
                {versions.map((v) => (
                  <option key={v.artifact_version_id} value={v.artifact_version_id}>
                    {v.title} (v{v.version_number})
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="debug-topbar-right">
          <button
            type="button"
            className={`debug-mode-btn ${viewMode === 'single' ? 'active' : ''}`}
            onClick={() => setViewMode('single')}
          >
            <LayoutTemplate size={13} />
            Inspector View
          </button>

          <button
            type="button"
            className={`debug-mode-btn ${viewMode === 'compare' ? 'active' : ''}`}
            disabled={versions.length < 2}
            onClick={() => setViewMode('compare')}
          >
            <Columns2 size={13} />
            Compare (2-Pane)
          </button>

          {primaryVersion && (
            <a
              className="debug-mode-btn"
              href={`/api/versions/${encodeURIComponent(primaryVersion.artifact_version_id)}/html?debug_back=1`}
            >
              <ExternalLink size={13} />
              Raw Chart DOM
            </a>
          )}
        </div>
      </header>

      <main className="debug-stage">
        {!primaryVersion ? (
          <div className="debug-empty">
            No artifacts exist in this workspace yet. Switch workspace above or exit debug to run a query.
          </div>
        ) : viewMode === 'compare' && secondaryVersion ? (
          <div className="debug-compare-grid">
            <ArtifactWindow
              key={`dbg_left_${primaryVersion.artifact_version_id}`}
              version={primaryVersion}
              comments={commentsByVersion[primaryVersion.artifact_version_id] || []}
              isComparePane
              onCommentChanged={onCommentChanged}
              onDelete={() => onDeleteVersion(primaryVersion.artifact_version_id)}
              onMinimize={onExitDebug}
            />
            <ArtifactWindow
              key={`dbg_right_${secondaryVersion.artifact_version_id}`}
              version={secondaryVersion}
              comments={commentsByVersion[secondaryVersion.artifact_version_id] || []}
              isComparePane
              onCommentChanged={onCommentChanged}
              onDelete={() => onDeleteVersion(secondaryVersion.artifact_version_id)}
              onMinimize={onExitDebug}
            />
          </div>
        ) : (
          <div className="debug-single-wrap">
            <ArtifactWindow
              key={`dbg_${primaryVersion.artifact_version_id}`}
              version={primaryVersion}
              comments={commentsByVersion[primaryVersion.artifact_version_id] || []}
              onCommentChanged={onCommentChanged}
              onDelete={() => onDeleteVersion(primaryVersion.artifact_version_id)}
              onMinimize={onExitDebug}
            />
          </div>
        )}
      </main>
    </div>
  );
};
