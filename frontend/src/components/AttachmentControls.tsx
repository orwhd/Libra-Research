import React, { useEffect, useId, useRef, useState } from 'react';
import { FileUp, FolderUp, Link2, Paperclip, X } from 'lucide-react';
import { attachDatasetUrl, uploadDatasetFiles } from '../api';

export interface AttachedDataset {
  upload_id: string;
  label: string;
  file_count: number;
  kind?: 'files' | 'url';
}

export function useAttachmentController(submitting: boolean) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [urlInputOpen, setUrlInputOpen] = useState(false);
  const [urlValue, setUrlValue] = useState('');
  const [attached, setAttached] = useState<AttachedDataset | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const attachWrapRef = useRef<HTMLDivElement>(null);
  const attachTriggerRef = useRef<HTMLButtonElement>(null);
  const attachGroupRef = useRef<HTMLDivElement>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);

  const attachTriggerId = useId();
  const attachMenuId = useId();
  const urlInputId = useId();
  const supportsFocusGroup =
    typeof HTMLElement !== 'undefined' && 'focusGroup' in HTMLElement.prototype;

  useEffect(() => {
    if (!attachMenuOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (attachWrapRef.current && !attachWrapRef.current.contains(e.target as Node)) {
        setAttachMenuOpen(false);
      }
    };
    const onFocusIn = (e: FocusEvent) => {
      if (attachWrapRef.current && !attachWrapRef.current.contains(e.target as Node)) {
        setAttachMenuOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('focusin', onFocusIn);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('focusin', onFocusIn);
    };
  }, [attachMenuOpen]);

  const handleFilesSelected = async (files: FileList | null, isFolder: boolean) => {
    if (!files || files.length === 0) return;
    setUploadError(null);
    setUploading(true);
    try {
      const firstRel =
        (files[0] as File & { webkitRelativePath?: string }).webkitRelativePath || files[0].name;
      const label = isFolder
        ? firstRel.split('/')[0] || 'Folder'
        : files.length === 1
          ? files[0].name
          : `${files.length} files`;
      const res = await uploadDatasetFiles(label, files);
      setAttached({
        upload_id: res.upload_id,
        label: res.label,
        file_count: res.file_count,
        kind: 'files',
      });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleUrlAttach = async () => {
    const trimmed = urlValue.trim();
    if (!trimmed || uploading || submitting) return;
    setUploadError(null);
    setUploading(true);
    try {
      const res = await attachDatasetUrl(trimmed);
      setAttached({
        upload_id: res.upload_id,
        label: res.label,
        file_count: res.file_count,
        kind: 'url',
      });
      setUrlValue('');
      setUrlInputOpen(false);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleMenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setAttachMenuOpen(false);
      requestAnimationFrame(() => attachTriggerRef.current?.focus());
      return;
    }
    if (e.key === 'Tab') {
      setAttachMenuOpen(false);
      return;
    }
    if (!supportsFocusGroup && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      const group = attachGroupRef.current;
      if (!group) return;
      const items = Array.from(group.querySelectorAll<HTMLButtonElement>('button')).filter(
        (item) => !item.disabled && item.getAttribute('aria-disabled') !== 'true'
      );
      if (items.length === 0) return;
      const current = items.indexOf(document.activeElement as HTMLButtonElement);
      const next =
        e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? items.length - 1
            : current < 0
              ? 0
              : (current + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
      e.preventDefault();
      items[next].focus({ preventScroll: true });
    }
  };

  return {
    uploading,
    uploadError,
    setUploadError,
    attachMenuOpen,
    setAttachMenuOpen,
    urlInputOpen,
    setUrlInputOpen,
    urlValue,
    setUrlValue,
    attached,
    setAttached,
    fileInputRef,
    folderInputRef,
    attachWrapRef,
    attachTriggerRef,
    attachGroupRef,
    urlInputRef,
    attachTriggerId,
    attachMenuId,
    urlInputId,
    supportsFocusGroup,
    handleFilesSelected,
    handleUrlAttach,
    handleMenuKeyDown,
  };
}

export type AttachmentController = ReturnType<typeof useAttachmentController>;

export const AttachmentHiddenInputs: React.FC<{ controller: AttachmentController }> = ({
  controller,
}) => (
  <>
    <input
      ref={controller.fileInputRef}
      type="file"
      multiple
      className="sr-only"
      aria-label="Attach files"
      onChange={(e) => {
        void controller.handleFilesSelected(e.target.files, false);
        e.target.value = '';
      }}
    />
    <input
      ref={controller.folderInputRef}
      type="file"
      multiple
      {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
      className="sr-only"
      aria-label="Attach folder"
      onChange={(e) => {
        void controller.handleFilesSelected(e.target.files, true);
        e.target.value = '';
      }}
    />
  </>
);

export const AttachmentInlineRows: React.FC<{
  controller: AttachmentController;
  disabled: boolean;
}> = ({ controller, disabled }) => (
  <>
    {controller.urlInputOpen && (
      <div className="url-attach-row">
        <label htmlFor={controller.urlInputId} className="sr-only">
          Dataset or webpage URL
        </label>
        <Link2 size={15} className="url-attach-icon" aria-hidden="true" />
        <input
          ref={controller.urlInputRef}
          id={controller.urlInputId}
          type="url"
          name="source_url"
          className="url-attach-input"
          placeholder="https://example.com/data.csv or webpage URL..."
          value={controller.urlValue}
          autoCorrect="off"
          autoCapitalize="none"
          spellCheck={false}
          disabled={controller.uploading || disabled}
          onChange={(e) => {
            controller.setUrlValue(e.target.value);
            if (controller.uploadError) controller.setUploadError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void controller.handleUrlAttach();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              controller.setUrlInputOpen(false);
            }
          }}
        />
        <button
          type="button"
          className="btn-url-confirm"
          disabled={controller.uploading || disabled || !controller.urlValue.trim()}
          onClick={() => void controller.handleUrlAttach()}
        >
          {controller.uploading ? 'Fetching...' : 'Attach'}
        </button>
        <button
          type="button"
          className="btn-url-cancel"
          aria-label="Cancel URL attachment"
          onClick={() => {
            controller.setUrlInputOpen(false);
            controller.setUploadError(null);
          }}
        >
          <X size={14} />
        </button>
      </div>
    )}

    {(controller.uploading || controller.attached || controller.uploadError) && (
      <div className="attach-feedback-row" role="status" aria-live="polite">
        {controller.uploading && <span className="attach-status">Attaching data source...</span>}

        {controller.attached && !controller.uploading && (
          <span className="attach-pill" title={`Upload ID: ${controller.attached.upload_id}`}>
            {controller.attached.kind === 'url' ? (
              <>
                <Link2 size={12} />
                {controller.attached.label}
              </>
            ) : (
              <>
                {controller.attached.label} ({controller.attached.file_count} file
                {controller.attached.file_count > 1 ? 's' : ''})
              </>
            )}
            <button
              type="button"
              className="attach-pill-clear"
              aria-label="Remove attached data source"
              onClick={() => controller.setAttached(null)}
            >
              <X size={12} />
            </button>
          </span>
        )}

        {controller.uploadError && <span className="attach-error">{controller.uploadError}</span>}
      </div>
    )}
  </>
);

export const AttachmentMenuButton: React.FC<{
  controller: AttachmentController;
  disabled: boolean;
  dropDirection?: 'down' | 'up';
}> = ({ controller, disabled, dropDirection = 'down' }) => (
  <div className="attach-menu-wrap" ref={controller.attachWrapRef}>
    <button
      ref={controller.attachTriggerRef}
      id={controller.attachTriggerId}
      type="button"
      className="btn-attach-icon"
      aria-label="Attach File, Folder, or URL"
      aria-haspopup="menu"
      aria-expanded={controller.attachMenuOpen}
      aria-controls={controller.attachMenuId}
      title="Attach File, Folder, or URL"
      disabled={disabled}
      onClick={() => controller.setAttachMenuOpen((prev) => !prev)}
      onKeyDown={(e) => {
        if (e.key === 'ArrowDown' || (e.key === 'ArrowUp' && dropDirection === 'up')) {
          e.preventDefault();
          controller.setAttachMenuOpen(true);
          requestAnimationFrame(() => {
            const buttons = controller.attachGroupRef.current?.querySelectorAll<HTMLButtonElement>(
              'button:not(:disabled)'
            );
            if (!buttons || buttons.length === 0) return;
            const targetIdx = e.key === 'ArrowUp' ? buttons.length - 1 : 0;
            buttons[targetIdx]?.focus();
          });
        }
      }}
    >
      <Paperclip size={18} />
    </button>

    {controller.attachMenuOpen && (
      <div
        id={controller.attachMenuId}
        className={`attach-dropdown ${dropDirection === 'up' ? 'drop-up' : ''}`}
      >
        <div
          ref={controller.attachGroupRef}
          className="attach-menu-group"
          {...({ focusgroup: 'menu nomemory' } as Record<string, string>)}
          role={controller.supportsFocusGroup ? undefined : 'menu'}
          aria-labelledby={controller.attachTriggerId}
          onKeyDown={controller.handleMenuKeyDown}
        >
          <button
            type="button"
            className="attach-dropdown-item"
            role={controller.supportsFocusGroup ? undefined : 'menuitem'}
            onClick={() => {
              controller.setAttachMenuOpen(false);
              controller.fileInputRef.current?.click();
            }}
          >
            <FileUp size={15} />
            <span>File</span>
          </button>
          <button
            type="button"
            className="attach-dropdown-item"
            role={controller.supportsFocusGroup ? undefined : 'menuitem'}
            onClick={() => {
              controller.setAttachMenuOpen(false);
              controller.folderInputRef.current?.click();
            }}
          >
            <FolderUp size={15} />
            <span>Folder</span>
          </button>
          <button
            type="button"
            className="attach-dropdown-item"
            role={controller.supportsFocusGroup ? undefined : 'menuitem'}
            onClick={() => {
              controller.setAttachMenuOpen(false);
              controller.setUploadError(null);
              controller.setUrlInputOpen(true);
              requestAnimationFrame(() => controller.urlInputRef.current?.focus());
            }}
          >
            <Link2 size={15} />
            <span>URL</span>
          </button>
        </div>
      </div>
    )}
  </div>
);
