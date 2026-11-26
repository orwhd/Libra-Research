import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Download, FileText } from 'lucide-react';
import { getArtifactPreview } from '../api';
import type { ArtifactPreviewPayload, ReproduceArtifact } from '../types';

interface InChatDocumentViewerProps {
  artifact: ReproduceArtifact;
  onClose: () => void;
}

function parseCsvPreviewRows(rawText: string, maxRows = 60): { headers: string[]; rows: string[][] } {
  const lines = rawText
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return { headers: [], rows: [] };
  const sep = lines[0].includes('\t') ? '\t' : ',';
  const splitLine = (line: string) => line.split(sep).map((c) => c.replace(/^"|"$/g, '').trim());
  const headers = splitLine(lines[0]);
  const rows = lines.slice(1, maxRows + 1).map(splitLine);
  return { headers, rows };
}

/**
 * In-Chat Document Viewer (Frozen L1.5 §6 & §21):
 * Renders reports (.pdf, .docx outline), figures (.png), scripts (.py), and datasets (.csv, .json)
 * strictly inside the Left Chat container—never on the right Canvas.
 */
export const InChatDocumentViewer: React.FC<InChatDocumentViewerProps> = ({
  artifact,
  onClose,
}) => {
  const [payload, setPayload] = useState<ArtifactPreviewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const backBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    backBtnRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getArtifactPreview(artifact.artifact_id)
      .then((res) => {
        if (active) setPayload(res);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [artifact.artifact_id]);

  const parsedCsv = useMemo(() => {
    if (!payload || payload.preview_kind !== 'text' || !payload.text) return null;
    const fn = payload.filename.toLowerCase();
    if (fn.endsWith('.csv') || fn.endsWith('.tsv')) {
      return parseCsvPreviewRows(payload.text);
    }
    return null;
  }, [payload]);

  const downloadHref = artifact.download_url || `/api/artifacts/${artifact.artifact_id}/download`;

  return (
    <div
      className="rep-inchat-viewer"
      data-testid="inchat-document-viewer"
      aria-label={`Document preview: ${artifact.filename || artifact.artifact_id}`}
    >
      <div className="rep-inchat-viewer-bar">
        <button
          ref={backBtnRef}
          type="button"
          className="btn-secondary rep-viewer-back-btn"
          onClick={onClose}
        >
          <ArrowLeft size={14} aria-hidden="true" />
          <span>Back to Chat</span>
        </button>

        <div className="rep-viewer-meta">
          <FileText size={14} aria-hidden="true" />
          <span className="rep-viewer-filename" title={artifact.filename || artifact.artifact_id}>
            {artifact.filename || artifact.artifact_id}
          </span>
          <span className={`rep-val-badge ${artifact.validation_status}`}>
            {artifact.validation_status}
          </span>
        </div>

        <a
          href={downloadHref}
          download={artifact.filename || true}
          className="btn-primary rep-viewer-dl-btn"
        >
          <Download size={13} aria-hidden="true" />
          <span>Download</span>
        </a>
      </div>

      <div className="rep-inchat-viewer-body">
        {loading && <div className="rep-viewer-notice">Loading verified artifact preview…</div>}
        {error && <div className="rep-viewer-notice error">Preview unavailable: {error}</div>}

        {!loading && !error && payload && (
          <>
            {payload.preview_kind === 'image' && payload.inline_url && (
              <figure className="rep-viewer-figure">
                <img
                  src={payload.inline_url}
                  alt={artifact.description || payload.filename}
                  className="rep-viewer-img"
                />
                <figcaption>
                  {artifact.description || payload.filename} · SHA-256{' '}
                  <code>{artifact.sha256.slice(0, 16)}…</code>
                </figcaption>
              </figure>
            )}

            {payload.preview_kind === 'pdf' && payload.inline_url && (
              <iframe
                title={`PDF Report Preview: ${payload.filename}`}
                src={payload.inline_url}
                className="rep-viewer-pdf-frame"
              />
            )}

            {parsedCsv && parsedCsv.headers.length > 0 && (
              <div className="rep-viewer-table-wrap">
                <table className="rep-viewer-table">
                  <caption>{payload.filename} (first 60 rows)</caption>
                  <thead>
                    <tr>
                      {parsedCsv.headers.map((h, idx) => (
                        <th key={idx} scope="col">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {parsedCsv.rows.map((row, rIdx) => (
                      <tr key={rIdx}>
                        {row.map((cell, cIdx) => (
                          <td key={cIdx}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {!parsedCsv &&
              (payload.preview_kind === 'text' || payload.preview_kind === 'docx_outline') && (
                <div className="rep-viewer-code-wrap">
                  {payload.preview_kind === 'docx_outline' && (
                    <div className="rep-viewer-docx-banner">
                      Word (<code>.docx</code>) extracted text preview — use Download above for full formatted Word document with embedded figures.
                    </div>
                  )}
                  <pre className="rep-viewer-pre">{payload.text}</pre>
                </div>
              )}

            {payload.preview_kind === 'download_only' && (
              <div className="rep-viewer-notice">
                Binary artifact (<code>{payload.mime_type}</code>) — use Download above to inspect locally.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
