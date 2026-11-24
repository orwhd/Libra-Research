import React, { useEffect, useRef } from 'react';
import type { ArtifactVersion, PreviewViewState } from '../types';
import { SandboxedIframeOriginPin } from '../utils/validateOrigin';

interface PreviewFrameProps {
  version: ArtifactVersion;
  iframeRef?: React.RefObject<HTMLIFrameElement | null>;
  onViewStateReceived?: (
    viewState: PreviewViewState,
    elementHint: string | null,
    requestId: string | null
  ) => void;
}

export const PreviewFrame: React.FC<PreviewFrameProps> = ({
  version,
  iframeRef: externalRef,
  onViewStateReceived,
}) => {
  const internalRef = useRef<HTMLIFrameElement>(null);
  const iframeRef = externalRef || internalRef;
  const originPinRef = useRef(new SandboxedIframeOriginPin());

  useEffect(() => {
    originPinRef.current.reset();
  }, [version.artifact_version_id]);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (!originPinRef.current.verify(event, iframeRef.current?.contentWindow || null)) {
        return;
      }
      const data = event.data as {
        type?: string;
        preview_view_state?: PreviewViewState;
        element_hint?: string | null;
        request_id?: string | null;
      };
      if (data?.type === 'RC_VIEW_STATE' && data.preview_view_state && onViewStateReceived) {
        onViewStateReceived(
          data.preview_view_state,
          data.element_hint ?? null,
          data.request_id ?? null
        );
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [iframeRef, onViewStateReceived]);

  return (
    <iframe
      ref={iframeRef}
      className="preview-iframe"
      sandbox="allow-scripts"
      src={`/api/versions/${encodeURIComponent(version.artifact_version_id)}/html`}
      title={`Interactive visualization: ${version.title} (v${version.version_number})`}
    />
  );
};
