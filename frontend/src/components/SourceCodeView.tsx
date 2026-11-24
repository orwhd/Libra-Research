import React, { useEffect, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { getVersionSource } from '../api';

interface SourceCodeViewProps {
  artifactVersionId: string;
  onStatusAnnouncement?: (msg: string) => void;
}

export const SourceCodeView: React.FC<SourceCodeViewProps> = ({
  artifactVersionId,
  onStatusAnnouncement,
}) => {
  const [source, setSource] = useState<{
    filename: string;
    html: string;
    external_dependencies: string[];
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    getVersionSource(artifactVersionId)
      .then((res) => {
        if (active) setSource(res);
      })
      .catch((err) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
    };
  }, [artifactVersionId]);

  const handleCopy = async () => {
    if (!source) return;
    try {
      await navigator.clipboard.writeText(source.html);
      setCopied(true);
      onStatusAnnouncement?.('Source HTML copied to clipboard.');
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      onStatusAnnouncement?.('Copy failed: clipboard access unavailable.');
    }
  };

  if (error) {
    return <div style={{ padding: 20, color: 'var(--crimson)' }}>Failed to load source: {error}</div>;
  }
  if (!source) {
    return <div style={{ padding: 20, color: 'var(--ink-muted)' }}>Loading artifact HTML source...</div>;
  }

  return (
    <div className="source-view" data-testid="source-view">
      <div className="source-view-bar">
        <span>
          <strong>{source.filename}</strong> · {source.external_dependencies.join(' ')}
        </span>
        <button
          type="button"
          className="btn-secondary"
          style={{ padding: '4px 10px', fontSize: 12 }}
          onClick={() => void handleCopy()}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? 'Copied' : 'Copy HTML'}
        </button>
      </div>
      <pre className="source-pre">
        <code>{source.html}</code>
      </pre>
    </div>
  );
};
