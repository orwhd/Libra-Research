import React, { useEffect, useState } from 'react';
import {
  Database,
  DownloadCloud,
  Filter,
  Layers,
  Cpu,
  BarChart3,
  ArrowDown,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import type { LineageGraph, LineageNode } from '../types';
import { getVersionLineage } from '../api';

interface DataMapViewProps {
  artifactVersionId: string;
}

const STAGE_ORDER: Record<LineageNode['stage'], number> = {
  source: 0,
  collection: 1,
  transformation: 2,
  dataset_snapshot: 3,
  analysis_run: 4,
  visualization_artifact: 5,
};

const SPECIAL_METADATA_KEYS = new Set([
  'script_text',
  'summary',
  'schema',
  'transform_steps',
  'missingness_quality',
  'params',
  'upstream_dependencies',
  'validation_report',
]);

const ACRONYMS: Record<string, string> = {
  id: 'ID',
  url: 'URL',
  oa: 'OA',
  svg: 'SVG',
  dom: 'DOM',
  nan: 'NaN',
  cagr: 'CAGR',
  yoy: 'YoY',
  api: 'API',
};

function humanizeKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((part) => {
      const lower = part.toLowerCase();
      if (ACRONYMS[lower]) return ACRONYMS[lower];
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ');
}

function isPlainRecord(val: unknown): val is Record<string, unknown> {
  return Boolean(val) && typeof val === 'object' && !Array.isArray(val);
}

function isPrimitive(val: unknown): boolean {
  return (
    val === null ||
    val === undefined ||
    typeof val === 'string' ||
    typeof val === 'number' ||
    typeof val === 'boolean'
  );
}

function formatScalar(key: string, val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'boolean') return val ? 'Yes' : 'No';
  if (typeof val === 'number') {
    if (!Number.isFinite(val)) return '—';
    if (/year/i.test(key) && Number.isInteger(val) && val >= 1800 && val <= 2200) {
      return String(val);
    }
    if (/(rate|share|cagr|growth)/i.test(key) && Math.abs(val) <= 2 && !/abs/i.test(key)) {
      return `${(val * 100).toFixed(1)}%`;
    }
    if (Number.isInteger(val)) return val.toLocaleString();
    return val.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  const str = String(val).trim();
  if (!str) return '—';
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(str)) {
    const dt = new Date(str);
    if (!Number.isNaN(dt.getTime())) return dt.toLocaleString();
  }
  return str;
}

function formatListValue(key: string, arr: unknown[]): string {
  if (arr.length === 0) return 'None';
  if (arr.every(isPrimitive)) {
    return arr.map((item) => formatScalar(key, item)).join(', ');
  }
  return `${arr.length} item(s)`;
}

function splitSchemaSpec(rawSpec: unknown): { typeLabel: string; description: string } {
  const str = String(rawSpec ?? '').trim();
  const match = str.match(/^(.+?)\s+[-–—]\s+([\s\S]+)$/);
  if (match) {
    return { typeLabel: match[1].trim(), description: match[2].trim() };
  }
  return { typeLabel: str || 'value', description: '' };
}

function StageIcon({ stage }: { stage: LineageNode['stage'] }) {
  switch (stage) {
    case 'source':
      return <Database size={16} color="var(--accent)" />;
    case 'collection':
      return <DownloadCloud size={16} color="var(--accent)" />;
    case 'transformation':
      return <Filter size={16} color="var(--amber)" />;
    case 'dataset_snapshot':
      return <Layers size={16} color="var(--emerald)" />;
    case 'analysis_run':
      return <Cpu size={16} color="#7c3aed" />;
    case 'visualization_artifact':
      return <BarChart3 size={16} color="var(--emerald)" />;
  }
}

export const DataMapView: React.FC<DataMapViewProps> = ({ artifactVersionId }) => {
  const [graph, setGraph] = useState<LineageGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    getVersionLineage(artifactVersionId)
      .then((data) => {
        if (!active) return;
        const sorted = [...data.nodes].sort(
          (a, b) => (STAGE_ORDER[a.stage] ?? 99) - (STAGE_ORDER[b.stage] ?? 99)
        );
        setGraph({ ...data, nodes: sorted });
        setSelectedNodeId(sorted[3]?.node_id || sorted[0]?.node_id || null);
      })
      .catch((err) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
    };
  }, [artifactVersionId]);

  if (error) {
    return <div style={{ padding: 20, color: 'var(--crimson)' }}>Failed to load Data Map: {error}</div>;
  }
  if (!graph) {
    return <div style={{ padding: 20, color: 'var(--ink-muted)' }}>Loading provenance Data Map...</div>;
  }

  const activeNode = graph.nodes.find((n) => n.node_id === selectedNodeId) || graph.nodes[0];
  const meta = activeNode?.metadata || {};
  const scriptText = typeof meta.script_text === 'string' ? meta.script_text : null;

  const summary = isPlainRecord(meta.summary) ? meta.summary : null;
  const summaryHeadline =
    summary && typeof summary.headline === 'string' ? summary.headline.trim() : '';
  const summaryMetrics =
    summary && isPlainRecord(summary.key_metrics)
      ? Object.entries(summary.key_metrics).filter(([, v]) => v !== null && v !== undefined)
      : [];

  const scalarOverview = Object.entries(meta).filter(
    ([k, v]) => !SPECIAL_METADATA_KEYS.has(k) && (isPrimitive(v) || (Array.isArray(v) && v.every(isPrimitive)))
  );

  const extraStructured = Object.entries(meta).filter(
    ([k, v]) =>
      !SPECIAL_METADATA_KEYS.has(k) &&
      !isPrimitive(v) &&
      !(Array.isArray(v) && v.every(isPrimitive))
  );

  const transformSteps = Array.isArray(meta.transform_steps)
    ? meta.transform_steps.map((s) => String(s))
    : [];

  const missingness = isPlainRecord(meta.missingness_quality)
    ? Object.entries(meta.missingness_quality)
    : [];

  const paramsEntries = isPlainRecord(meta.params) ? Object.entries(meta.params) : [];

  const upstreamDeps = Array.isArray(meta.upstream_dependencies)
    ? meta.upstream_dependencies.filter(isPlainRecord)
    : [];

  const validationReport = isPlainRecord(meta.validation_report) ? meta.validation_report : null;
  const domCheck =
    validationReport && isPlainRecord(validationReport.dom_check) ? validationReport.dom_check : null;

  const schemaEntries = isPlainRecord(meta.schema) ? Object.entries(meta.schema) : [];

  const renderValue = (key: string, val: unknown) => {
    if (Array.isArray(val)) {
      return <span className="evidence-kv-val">{formatListValue(key, val)}</span>;
    }
    const text = formatScalar(key, val);
    if (typeof val === 'string' && /^https?:\/\//i.test(val)) {
      return (
        <a
          className="evidence-link"
          href={val}
          target="_blank"
          rel="noopener noreferrer"
        >
          {text}
        </a>
      );
    }
    if (/(id|_ref|path|mode|algorithm)$/i.test(key) && text !== '—') {
      return <code className="evidence-code-val">{text}</code>;
    }
    return <span className="evidence-kv-val">{text}</span>;
  };

  return (
    <div className="datamap-split" data-testid="datamap-view">
      <div className="datamap-graph-pane">
        <div className="datamap-stage-chain">
          {graph.nodes.map((node, idx) => (
            <React.Fragment key={node.node_id}>
              <button
                type="button"
                className={`datamap-node-btn ${activeNode?.node_id === node.node_id ? 'active' : ''}`}
                data-stage={node.stage}
                onClick={() => setSelectedNodeId(node.node_id)}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                  <StageIcon stage={node.stage} />
                  <span>
                    <div
                      style={{
                        fontSize: 10.5,
                        textTransform: 'uppercase',
                        color: 'var(--ink-muted)',
                        fontWeight: 700,
                      }}
                    >
                      {node.stage.replace('_', ' ')}
                    </div>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>{node.label}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--ink-secondary)' }}>
                      {node.subtitle}
                    </div>
                  </span>
                </span>
              </button>
              {idx < graph.nodes.length - 1 && (
                <div className="datamap-arrow" aria-hidden="true">
                  <ArrowDown size={14} />
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      <aside className="datamap-inspector" aria-label="Lineage Node Evidence">
        {activeNode && (
          <div className="evidence-stack">
            <header className="evidence-head">
              <div className="evidence-stage-kicker">{activeNode.stage.replace('_', ' ')}</div>
              <h3 className="evidence-title">{activeNode.label}</h3>
              {activeNode.subtitle && <p className="evidence-subtitle">{activeNode.subtitle}</p>}
            </header>

            {(summaryHeadline || summaryMetrics.length > 0) && (
              <section className="evidence-section">
                <div className="evidence-section-title">Key Findings</div>
                {summaryHeadline && <div className="evidence-callout">{summaryHeadline}</div>}
                {summaryMetrics.length > 0 && (
                  <div className="evidence-metrics-grid">
                    {summaryMetrics.map(([k, v]) => (
                      <div key={k} className="evidence-metric-card">
                        <span className="evidence-metric-label">{humanizeKey(k)}</span>
                        <span className="evidence-metric-value">
                          {Array.isArray(v) ? formatListValue(k, v) : formatScalar(k, v)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}

            {scalarOverview.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">Stage Properties</div>
                <div className="evidence-kv-list">
                  {scalarOverview.map(([k, v]) => (
                    <div key={k} className="evidence-kv-row">
                      <span className="evidence-kv-key">{humanizeKey(k)}</span>
                      {renderValue(k, v)}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {transformSteps.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">
                  Transformation Pipeline ({transformSteps.length})
                </div>
                <ol className="evidence-steps-list">
                  {transformSteps.map((step, i) => (
                    <li key={i} className="evidence-step-item">
                      {step}
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {missingness.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">Data Quality</div>
                <div className="evidence-metrics-grid">
                  {missingness.map(([k, v]) => (
                    <div key={k} className="evidence-metric-card">
                      <span className="evidence-metric-label">{humanizeKey(k)}</span>
                      <span className="evidence-metric-value">{formatScalar(k, v)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {paramsEntries.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">Execution Parameters</div>
                <div className="evidence-kv-list">
                  {paramsEntries.map(([k, v]) => (
                    <div key={k} className="evidence-kv-row">
                      <span className="evidence-kv-key">{humanizeKey(k)}</span>
                      {renderValue(k, v)}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {validationReport && (
              <section className="evidence-section">
                <div className="evidence-section-title">Browser Validation</div>
                <div
                  className={`evidence-status-banner ${
                    validationReport.passed ? 'passed' : 'failed'
                  }`}
                >
                  {validationReport.passed ? (
                    <CheckCircle2 size={14} />
                  ) : (
                    <AlertTriangle size={14} />
                  )}
                  <span>
                    {validationReport.passed
                      ? 'Headless Chromium QA passed'
                      : 'Validation flagged issues'}
                  </span>
                </div>
                <div className="evidence-metrics-grid">
                  {domCheck && (
                    <>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">SVG Elements</span>
                        <span className="evidence-metric-value">
                          {formatScalar('svgCount', domCheck.svgCount)}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Rendered Marks</span>
                        <span className="evidence-metric-value">
                          {formatScalar('markCount', domCheck.markCount)}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">Controls</span>
                        <span className="evidence-metric-value">
                          {formatScalar('interactiveControls', domCheck.interactiveControls)}
                        </span>
                      </div>
                      <div className="evidence-metric-card">
                        <span className="evidence-metric-label">NaN Attributes</span>
                        <span className="evidence-metric-value">
                          {formatScalar('nanAttrs', domCheck.nanAttrs)}
                        </span>
                      </div>
                    </>
                  )}
                  <div className="evidence-metric-card">
                    <span className="evidence-metric-label">Interaction Tested</span>
                    <span className="evidence-metric-value">
                      {formatScalar('interaction_tested', validationReport.interaction_tested)}
                    </span>
                  </div>
                  <div className="evidence-metric-card">
                    <span className="evidence-metric-label">Preview Captured</span>
                    <span className="evidence-metric-value">
                      {formatScalar('thumbnail_saved', validationReport.thumbnail_saved)}
                    </span>
                  </div>
                </div>
              </section>
            )}

            {upstreamDeps.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">Upstream Sources</div>
                <div className="evidence-source-list">
                  {upstreamDeps.map((dep, i) => (
                    <div key={i} className="evidence-source-card">
                      <div className="evidence-source-name">
                        {formatScalar('name', dep.name)}
                      </div>
                      {typeof dep.url === 'string' && dep.url && (
                        <a
                          className="evidence-link"
                          href={dep.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {dep.url}
                        </a>
                      )}
                      {dep.api_total_matching_works !== undefined && (
                        <div className="evidence-source-meta">
                          Matching catalog records:{' '}
                          <strong>
                            {formatScalar('count', dep.api_total_matching_works)}
                          </strong>
                        </div>
                      )}
                      {typeof dep.fallback_reason === 'string' && dep.fallback_reason && (
                        <p className="evidence-source-note">{dep.fallback_reason}</p>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {schemaEntries.length > 0 && (
              <section className="evidence-section">
                <div className="evidence-section-title">
                  Dataset Schema ({schemaEntries.length} fields)
                </div>
                <div className="evidence-schema-list">
                  {schemaEntries.map(([fieldName, rawSpec]) => {
                    const { typeLabel, description } = splitSchemaSpec(rawSpec);
                    return (
                      <div key={fieldName} className="evidence-schema-row">
                        <div className="evidence-schema-head">
                          <code className="evidence-field-name">{fieldName}</code>
                          <span className="evidence-field-type">{typeLabel}</span>
                        </div>
                        {description && (
                          <div className="evidence-field-desc">{description}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {extraStructured.map(([sectionKey, sectionVal]) => (
              <section key={sectionKey} className="evidence-section">
                <div className="evidence-section-title">{humanizeKey(sectionKey)}</div>
                {isPlainRecord(sectionVal) ? (
                  <div className="evidence-kv-list">
                    {Object.entries(sectionVal).map(([subK, subV]) => (
                      <div key={subK} className="evidence-kv-row">
                        <span className="evidence-kv-key">{humanizeKey(subK)}</span>
                        {renderValue(subK, subV)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="evidence-kv-list">
                    {(Array.isArray(sectionVal) ? sectionVal : [sectionVal]).map((item, idx) => (
                      <div key={idx} className="evidence-kv-row">
                        <span className="evidence-kv-key">#{idx + 1}</span>
                        {isPlainRecord(item) ? (
                          <span className="evidence-kv-val">
                            {Object.entries(item)
                              .map(([ik, iv]) => `${humanizeKey(ik)}: ${formatScalar(ik, iv)}`)
                              .join(' · ')}
                          </span>
                        ) : (
                          renderValue(sectionKey, item)
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            ))}

            {scriptText && (
              <section className="evidence-section">
                <details className="evidence-script-details">
                  <summary className="evidence-script-summary">
                    Executed Python Script (Sandbox)
                  </summary>
                  <pre className="code-block">{scriptText}</pre>
                </details>
              </section>
            )}
          </div>
        )}
      </aside>
    </div>
  );
};
