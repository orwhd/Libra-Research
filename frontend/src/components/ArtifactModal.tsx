import React, { useEffect, useId, useRef } from 'react';
import type { ArtifactVersion, VisualComment } from '../types';
import { ArtifactWindow } from './ArtifactWindow';

export type ModalState =
  | { mode: 'closed' }
  | { mode: 'single'; versionId: string }
  | { mode: 'compare'; leftId: string; rightId: string };

interface ArtifactModalProps {
  modalState: ModalState;
  versionsById: Map<string, ArtifactVersion>;
  commentsByVersion: Record<string, VisualComment[]>;
  onCommentChanged: () => Promise<void>;
  onDeleteVersion: (versionId: string) => Promise<void>;
  onUpdateModal: (next: ModalState) => void;
}

export const ArtifactModal: React.FC<ArtifactModalProps> = ({
  modalState,
  versionsById,
  commentsByVersion,
  onCommentChanged,
  onDeleteVersion,
  onUpdateModal,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (modalState.mode !== 'closed') {
      if (!dialog.open) {
        dialog.showModal();
      }
    } else if (dialog.open) {
      dialog.close();
    }
  }, [modalState]);

  const handleClose = () => {
    const dialog = dialogRef.current;
    if (dialog) dialog.returnValue = '';
    onUpdateModal({ mode: 'closed' });
  };

  const singleVersion =
    modalState.mode === 'single' ? versionsById.get(modalState.versionId) : undefined;
  const leftVersion =
    modalState.mode === 'compare' ? versionsById.get(modalState.leftId) : undefined;
  const rightVersion =
    modalState.mode === 'compare' ? versionsById.get(modalState.rightId) : undefined;

  return (
    <dialog
      ref={dialogRef}
      className="artifact-dialog"
      {...({ closedby: 'closerequest' } as Record<string, string>)}
      aria-labelledby={headingId}
      onClose={handleClose}
    >
      {modalState.mode === 'single' && singleVersion && (
        <div className="artifact-modal-single" data-testid="modal-single">
          <ArtifactWindow
            key={singleVersion.artifact_version_id}
            version={singleVersion}
            comments={commentsByVersion[singleVersion.artifact_version_id] || []}
            headingId={headingId}
            onCommentChanged={onCommentChanged}
            onDelete={() => onDeleteVersion(singleVersion.artifact_version_id)}
            onMinimize={() => onUpdateModal({ mode: 'closed' })}
          />
        </div>
      )}

      {modalState.mode === 'compare' && leftVersion && rightVersion && (
        <div className="artifact-modal-compare" data-testid="modal-compare">
          <ArtifactWindow
            key={`left_${leftVersion.artifact_version_id}`}
            version={leftVersion}
            comments={commentsByVersion[leftVersion.artifact_version_id] || []}
            headingId={headingId}
            isComparePane
            onCommentChanged={onCommentChanged}
            onDelete={() => onDeleteVersion(leftVersion.artifact_version_id)}
            onMinimize={() =>
              onUpdateModal({ mode: 'single', versionId: rightVersion.artifact_version_id })
            }
          />
          <ArtifactWindow
            key={`right_${rightVersion.artifact_version_id}`}
            version={rightVersion}
            comments={commentsByVersion[rightVersion.artifact_version_id] || []}
            headingId={headingId}
            isComparePane
            onCommentChanged={onCommentChanged}
            onDelete={() => onDeleteVersion(rightVersion.artifact_version_id)}
            onMinimize={() =>
              onUpdateModal({ mode: 'single', versionId: leftVersion.artifact_version_id })
            }
          />
        </div>
      )}
    </dialog>
  );
};
