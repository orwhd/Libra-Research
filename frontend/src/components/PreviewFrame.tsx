import React, { useCallback, useEffect, useRef } from 'react';
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
  onScrollStateChange?: (scrollX: number, scrollY: number) => void;
}

export const PreviewFrame: React.FC<PreviewFrameProps> = ({
  version,
  iframeRef: externalRef,
  onViewStateReceived,
  onScrollStateChange,
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
        scroll_x?: number;
        scroll_y?: number;
      };
      if (data?.type === 'RC_VIEW_STATE' && data.preview_view_state && onViewStateReceived) {
        onViewStateReceived(
          data.preview_view_state,
          data.element_hint ?? null,
          data.request_id ?? null
        );
        if (onScrollStateChange) {
          onScrollStateChange(
            Number(data.preview_view_state.scroll_x) || 0,
            Number(data.preview_view_state.scroll_y) || 0
          );
        }
      } else if (data?.type === 'RC_SCROLL_STATE' && onScrollStateChange) {
        onScrollStateChange(Number(data.scroll_x) || 0, Number(data.scroll_y) || 0);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [iframeRef, onViewStateReceived, onScrollStateChange]);

  const handleLoad = useCallback(() => {
    try {
      iframeRef.current?.contentWindow?.postMessage({ type: 'RC_QUERY_SCROLL' }, '*');
    } catch {
      /* ignore if sandboxed iframe not ready */
    }
  }, [iframeRef]);

  return (
    <iframe
      ref={iframeRef}
      className="preview-iframe"
      sandbox="allow-scripts"
      src={`/api/versions/${encodeURIComponent(version.artifact_version_id)}/html`}
      title={`Interactive visualization: ${version.title} (v${version.version_number})`}
      onLoad={handleLoad}
    />
  );
};
