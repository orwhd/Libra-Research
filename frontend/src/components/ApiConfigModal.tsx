import React, { useEffect, useId, useRef, useState } from 'react';
import { CheckCircle2, Eye, EyeOff, KeyRound, RefreshCw, RotateCcw, X } from 'lucide-react';
import {
  fetchLLMModels,
  resetLLMSettings,
  testLLMSettings,
  updateLLMSettings,
} from '../api';
import type { LLMSettings } from '../types';

interface ApiConfigModalProps {
  open: boolean;
  settings: LLMSettings | null;
  onSaved: (next: LLMSettings) => void;
  onReset: (next: LLMSettings) => void;
  onClose: () => void;
}

const PROVIDER_PRESETS: {
  id: string;
  label: string;
  baseUrl: string;
  defaultModel: string;
}[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    defaultModel: 'deepseek-chat',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    defaultModel: 'gpt-4o-mini',
  },
  {
    id: 'gemini',
    label: 'Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    defaultModel: 'gemini-2.5-flash',
  },
  {
    id: 'custom',
    label: 'Custom',
    baseUrl: '',
    defaultModel: '',
  },
];

export const ApiConfigModal: React.FC<ApiConfigModalProps> = ({
  open,
  settings,
  onSaved,
  onReset,
  onClose,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const keyInputId = useId();
  const baseUrlInputId = useId();
  const modelInputId = useId();
  const modelDatalistId = useId();

  const [provider, setProvider] = useState('deepseek');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com');
  const [model, setModel] = useState('deepseek-chat');
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    setProvider(settings?.provider || 'deepseek');
    setApiKey('');
    setBaseUrl(settings?.base_url || 'https://api.deepseek.com');
    setModel(settings?.model || 'deepseek-chat');
    setAvailableModels(settings?.available_models || []);
    setShowKey(false);
    setStatusMsg(null);
  }, [open, settings]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) {
        dialog.showModal();
      }
    } else if (dialog.open) {
      dialog.close();
    }
  }, [open]);

  // Mandatory fallback for browsers without native <dialog closedby="any">
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || 'closedBy' in HTMLDialogElement.prototype) return;

    const isOutside = (e: PointerEvent) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };

    let pressedOutside = false;
    const onPointerDown = (e: PointerEvent) => {
      if (dialog.open) pressedOutside = isOutside(e);
    };
    const onPointerUp = (e: PointerEvent) => {
      if (dialog.open && pressedOutside && isOutside(e)) {
        dialog.close();
      }
      pressedOutside = false;
    };

    dialog.addEventListener('pointerdown', onPointerDown);
    dialog.addEventListener('pointerup', onPointerUp);
    return () => {
      dialog.removeEventListener('pointerdown', onPointerDown);
      dialog.removeEventListener('pointerup', onPointerUp);
    };
  }, []);

  const handleSelectPreset = (presetId: string) => {
    setProvider(presetId);
    setStatusMsg(null);
    const preset = PROVIDER_PRESETS.find((p) => p.id === presetId);
    if (preset && preset.baseUrl) {
      setBaseUrl(preset.baseUrl);
      setModel(preset.defaultModel);
    }
  };

  const handleFetchModels = async () => {
    setStatusMsg(null);
    setFetchingModels(true);
    try {
      const res = await fetchLLMModels({
        api_key: apiKey.trim(),
        base_url: baseUrl.trim(),
      });
      setAvailableModels(res.models);
      if (res.models.length > 0 && !model.trim()) {
        setModel(res.models[0]);
      }
      setStatusMsg({
        kind: 'ok',
        text: `Loaded ${res.count} models from your API key.`,
      });
    } catch (err) {
      setStatusMsg({
        kind: 'err',
        text: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setFetchingModels(false);
    }
  };

  const handleTest = async () => {
    setStatusMsg(null);
    setTesting(true);
    try {
      const res = await testLLMSettings({
        provider,
        api_key: apiKey.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        thinking_effort: settings?.thinking_effort || 'medium',
        available_models: availableModels,
      });
      setStatusMsg({ kind: 'ok', text: `Connected cleanly to ${res.model}.` });
    } catch (err) {
      setStatusMsg({
        kind: 'err',
        text: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTesting(false);
    }
  };

  const handleReset = async () => {
    setStatusMsg(null);
    setSaving(true);
    try {
      const cleared = await resetLLMSettings();
      onReset(cleared);
    } catch (err) {
      setStatusMsg({
        kind: 'err',
        text: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    if (!apiKey.trim() && !settings?.configured) {
      setStatusMsg({ kind: 'err', text: 'Please enter an API Key before saving.' });
      return;
    }
    setSaving(true);
    try {
      const saved = await updateLLMSettings({
        provider,
        api_key: apiKey.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        thinking_effort: settings?.thinking_effort || 'medium',
        available_models: availableModels,
      });
      onSaved(saved);
    } catch (err) {
      setStatusMsg({
        kind: 'err',
        text: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || testing || fetchingModels;

  return (
    <dialog
      ref={dialogRef}
      className="api-config-dialog"
      aria-labelledby={titleId}
      {...({ closedby: 'any' } as Record<string, string>)}
      onClose={() => {
        if (dialogRef.current) dialogRef.current.returnValue = '';
        onClose();
      }}
    >
      <div className="api-config-panel">
        <div className="api-config-header">
          <div className="api-config-title-group">
            <span className="api-config-badge-icon" aria-hidden="true">
              <KeyRound size={15} />
            </span>
            <h2 id={titleId} className="api-config-title">
              Model API Configuration
            </h2>
          </div>
          <button
            type="button"
            className="api-config-close"
            aria-label="Close dialog"
            onClick={() => dialogRef.current?.close()}
          >
            <X size={16} />
          </button>
        </div>

        <form className="api-config-form" onSubmit={handleSave}>
          <div className="api-preset-row" role="group" aria-label="Provider presets">
            {PROVIDER_PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`api-preset-pill ${provider === p.id ? 'active' : ''}`}
                disabled={busy}
                onClick={() => handleSelectPreset(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="api-field">
            <label htmlFor={keyInputId} className="api-field-label">
              API Key
            </label>
            <div className="api-key-input-wrap">
              <input
                id={keyInputId}
                name="api_key"
                type={showKey ? 'text' : 'password'}
                className="api-input"
                autoFocus
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder={
                  settings?.configured && settings.api_key_masked
                    ? `Saved (${settings.api_key_masked}) — enter new key to replace`
                    : 'sk-...'
                }
                value={apiKey}
                disabled={busy}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  if (statusMsg) setStatusMsg(null);
                }}
              />
              <button
                type="button"
                className="api-key-toggle"
                aria-pressed={showKey}
                aria-label={showKey ? 'Hide API key' : 'Show API key'}
                onClick={() => setShowKey((prev) => !prev)}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="api-field">
            <label htmlFor={baseUrlInputId} className="api-field-label">
              API Base URL
            </label>
            <input
              id={baseUrlInputId}
              name="base_url"
              type="url"
              required
              className="api-input"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="https://api.deepseek.com"
              value={baseUrl}
              disabled={busy}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                if (statusMsg) setStatusMsg(null);
              }}
            />
          </div>

          <div className="api-field">
            <div className="api-field-head">
              <label htmlFor={modelInputId} className="api-field-label">
                Model Name
              </label>
              <button
                type="button"
                className="api-fetch-models-btn"
                disabled={busy || !baseUrl.trim()}
                onClick={() => void handleFetchModels()}
              >
                <RefreshCw size={12} className={fetchingModels ? 'spin-icon' : ''} />
                {fetchingModels ? 'Fetching...' : 'Fetch Models'}
              </button>
            </div>
            <input
              id={modelInputId}
              name="model"
              type="text"
              required
              list={modelDatalistId}
              className="api-input"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="deepseek-chat"
              value={model}
              disabled={busy}
              onChange={(e) => {
                setModel(e.target.value);
                if (statusMsg) setStatusMsg(null);
              }}
            />
            <datalist id={modelDatalistId}>
              {availableModels.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>

            {availableModels.length > 0 && (
              <div className="api-fetched-models-box">
                <span className="api-fetched-models-count">
                  Fetched models ({availableModels.length})
                </span>
                <div className="api-fetched-models-list">
                  {availableModels.map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={`api-model-chip ${model.trim() === m ? 'active' : ''}`}
                      onClick={() => {
                        setModel(m);
                        if (statusMsg) setStatusMsg(null);
                      }}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {statusMsg && (
            <div
              className={`api-status-banner ${statusMsg.kind}`}
              role="status"
              aria-live="polite"
            >
              {statusMsg.kind === 'ok' && <CheckCircle2 size={14} aria-hidden="true" />}
              <span>{statusMsg.text}</span>
            </div>
          )}

          <div className="api-config-footer">
            <div className="api-footer-left">
              <button
                type="button"
                className="btn-secondary api-action-btn"
                disabled={busy}
                onClick={() => void handleTest()}
              >
                {testing ? 'Testing...' : 'Test Connection'}
              </button>
              {settings?.configured && (
                <button
                  type="button"
                  className="btn-secondary api-action-btn"
                  disabled={busy}
                  title="Clear custom API config"
                  onClick={() => void handleReset()}
                >
                  <RotateCcw size={13} />
                  Reset
                </button>
              )}
            </div>

            <div className="api-footer-right">
              <button
                type="button"
                className="btn-secondary api-action-btn"
                disabled={busy}
                onClick={() => dialogRef.current?.close()}
              >
                Cancel
              </button>
              <button type="submit" className="btn-primary api-save-btn" disabled={busy}>
                {saving ? 'Saving...' : 'Save Config'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </dialog>
  );
};
