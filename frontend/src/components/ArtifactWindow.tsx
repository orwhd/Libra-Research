import React, { useState } from 'react';
import { X } from 'lucide-react';
import type { ArtifactVersion, DockTab, VisualComment } from '../types';
import { ArtifactDock } from './ArtifactDock';
import { DataMapView } from './DataMapView';
import { CommentsOverlay } from './CommentsOverlay';
import { SourceCodeView } from './SourceCodeView';

interface ArtifactWindowProps {
  version: ArtifactVersion;
  comments: VisualComment[];
  headingId?: string;
  isComparePane?: boolean;
  onCommentChanged: () => Promise<void>;
  onDelete: () => Promise<void> | void;
  onMinimize: () => void;
}

export const ArtifactWindow: React.FC<ArtifactWindowProps> = ({
  version,
  comments,
  headingId,
  onCommentChanged,
  onDelete,
  onMinimize,
}) => {
  const [activeTab, setActiveTab] = useState<DockTab>('preview');
  const [commentPinMode, setCommentPinMode] = useState(false);
  const [liveStatus, setLiveStatus] = useState('');

  const pendingCount = comments.filter((c) => c.status === 'pending').length;

  return (
    <section
      className="artifact-window"
      data-testid={`artifact-window-${version.artifact_version_id}`}
    >
      <header className="artifact-window-header">
        <div className="artifact-window-title-group">
          <h1 id={headingId} className="artifact-window-title">
            {version.title}
          </h1>
        </div>
        <div className="artifact-window-actions">
          <button
            type="button"
            className="artifact-close-btn"
            aria-label="Close"
            onClick={onMinimize}
          >
            <X size={16} />
          </button>
        </div>
      </header>

      <div className="artifact-window-body">
        {activeTab === 'preview' && (
          <CommentsOverlay
            version={version}
            comments={comments}
            pinMode={commentPinMode}
            onExitPinMode={() => setCommentPinMode(false)}
            onCommentChanged={onCommentChanged}
          />
        )}
        {activeTab === 'data_map' && (
          <DataMapView
            artifactVersionId={version.artifact_version_id}
            comments={comments}
            pinMode={commentPinMode}
            onExitPinMode={() => setCommentPinMode(false)}
            onCommentChanged={onCommentChanged}
          />
        )}
        {activeTab === 'source' && (
          <SourceCodeView
            artifactVersionId={version.artifact_version_id}
            onStatusAnnouncement={setLiveStatus}
          />
        )}
      </div>

      <ArtifactDock
        artifactVersionId={version.artifact_version_id}
        activeTab={activeTab}
        commentPinMode={(activeTab === 'preview' || activeTab === 'data_map') && commentPinMode}
        pendingCommentsCount={pendingCount}
        onSelectTab={(tab) => {
          setActiveTab(tab);
          setCommentPinMode(false);
        }}
        onToggleCommentMode={() => {
          if (activeTab !== 'preview' && activeTab !== 'data_map') {
            setActiveTab('preview');
            setCommentPinMode(true);
          } else {
            setCommentPinMode((prev) => !prev);
          }
        }}
        onDelete={() => void onDelete()}
        onStatusAnnouncement={setLiveStatus}
      />

      <p role="status" className="sr-only">
        {liveStatus}
      </p>
    </section>
  );
};
