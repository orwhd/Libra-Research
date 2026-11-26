import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Brain, Check, ChevronDown, Settings2 } from 'lucide-react';
import type { LLMSettings, ThinkingEffort } from '../types';

interface ModelThinkingSelectorProps {
  settings: LLMSettings | null;
  disabled: boolean;
  onChangeSelection: (patch: {
    model?: string;
    thinking_effort?: ThinkingEffort;
  }) => Promise<void>;
  onOpenConfig: () => void;
}

const EFFORT_OPTIONS: { value: ThinkingEffort; label: string; shortLabel: string }[] = [
  { value: 'low', label: 'Low', shortLabel: 'Low' },
  { value: 'medium', label: 'Med', shortLabel: 'Med' },
  { value: 'high', label: 'High', shortLabel: 'High' },
];

const PROVIDER_SUGGESTIONS: Record<string, string[]> = {
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
  openai: ['gpt-4o-mini', 'gpt-4o', 'o3-mini'],
  gemini: ['gemini-2.5-flash', 'gemini-2.5-pro'],
};

export const ModelThinkingSelector: React.FC<ModelThinkingSelectorProps> = ({
  settings,
  disabled,
  onChangeSelection,
  onOpenConfig,
}) => {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [fallbackPos, setFallbackPos] = useState<{
    top: number;
    left: number;
    maxHeight: number;
  } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const popoverId = useId();
  const effortGroupName = useId();

  const supportsPopover =
    typeof HTMLElement !== 'undefined' && Object.hasOwn(HTMLElement.prototype, 'popover');
  const supportsAnchor =
    typeof CSS !== 'undefined' && CSS.supports?.('position-area: block-end');

  const closePopover = () => {
    const pop = popoverRef.current as (HTMLDivElement & { hidePopover?: () => void }) | null;
    if (supportsPopover && pop?.hidePopover) {
      try {
        if (pop.matches(':popover-open')) {
          pop.hidePopover();
        }
      } catch {
        /* ignore */
      }
    }
    setOpen(false);
  };

  useEffect(() => {
    const pop = popoverRef.current;
    if (!pop || !supportsPopover) return;
    const onToggle = (e: Event) => {
      const te = e as Event & { newState?: string };
      const nextOpen = te.newState === 'open' || pop.matches(':popover-open');
      setOpen(nextOpen);
      if (nextOpen) setFilterText('');
    };
    pop.addEventListener('toggle', onToggle);
    return () => pop.removeEventListener('toggle', onToggle);
  }, [supportsPopover]);

  // Fallback light-dismiss + Escape for browsers lacking native Popover API
  useEffect(() => {
    if (!open || supportsPopover) return;
    setFilterText('');
    const onPointerDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        requestAnimationFrame(() => triggerRef.current?.focus());
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, supportsPopover]);

  // Viewport-clamped fixed fallback only when native CSS anchor positioning (`position-area`) is unsupported
  useLayoutEffect(() => {
    if (!open || supportsAnchor) {
      setFallbackPos(null);
      return;
    }
    const updateFallbackPos = () => {
      const trig = triggerRef.current;
      if (!trig) return;
      const r = trig.getBoundingClientRect();
      const panelW = Math.min(304, Math.max(220, window.innerWidth - 20));
      const estH = 320;
      const margin = 10;
      const spaceBelow = window.innerHeight - r.bottom - margin;
      const spaceAbove = r.top - margin;
      const openAbove = spaceBelow < estH && spaceAbove > spaceBelow;
      const maxHeight = Math.max(160, Math.min(380, openAbove ? spaceAbove - 6 : spaceBelow - 6));
      const actualH = Math.min(estH, maxHeight);
      const top = openAbove
        ? Math.max(margin, r.top - actualH - 6)
        : Math.min(window.innerHeight - actualH - margin, r.bottom + 6);
      const left = Math.max(margin, Math.min(window.innerWidth - panelW - margin, r.left));
      setFallbackPos({ top, left, maxHeight });
    };
    updateFallbackPos();
    window.addEventListener('resize', updateFallbackPos);
    window.addEventListener('scroll', updateFallbackPos, true);
    return () => {
      window.removeEventListener('resize', updateFallbackPos);
      window.removeEventListener('scroll', updateFallbackPos, true);
    };
  }, [open, supportsAnchor]);

  const activeModel = settings?.model || 'deepseek-chat';
  const effort: ThinkingEffort = settings?.thinking_effort || 'medium';
  const effortMeta = EFFORT_OPTIONS.find((e) => e.value === effort) || EFFORT_OPTIONS[1];

  const modelList = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    const add = (m?: string) => {
      const cleaned = (m || '').trim();
      if (cleaned && !seen.has(cleaned)) {
        seen.add(cleaned);
        out.push(cleaned);
      }
    };
    for (const m of settings?.available_models || []) add(m);
    add(activeModel);
    if ((settings?.available_models?.length || 0) <= 1) {
      const hints = PROVIDER_SUGGESTIONS[settings?.provider || 'deepseek'] || [];
      for (const m of hints) add(m);
    }
    return out;
  }, [settings?.available_models, settings?.provider, activeModel]);

  const filteredModels = useMemo(() => {
    const q = filterText.trim().toLowerCase();
    if (!q) return modelList;
    return modelList.filter((m) => m.toLowerCase().includes(q));
  }, [modelList, filterText]);

  return (
    <div className="model-selector-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        popoverTarget={supportsPopover ? popoverId : undefined}
        className="model-selector-trigger"
        aria-expanded={open}
        aria-controls={popoverId}
        disabled={disabled}
        title={`Model: ${activeModel} · Thinking: ${effortMeta.label}`}
        onClick={() => {
          if (!supportsPopover) {
            setOpen((prev) => !prev);
          }
        }}
      >
        <Brain size={14} className="model-selector-icon active" aria-hidden="true" />
        <span className="model-selector-name">{activeModel}</span>
        <span className="model-effort-pill active">{effortMeta.shortLabel}</span>
        <ChevronDown size={13} className="model-selector-caret" aria-hidden="true" />
      </button>

      <div
        id={popoverId}
        ref={popoverRef}
        popover={supportsPopover ? 'auto' : undefined}
        className={`model-selector-popover ${!supportsPopover && open ? 'is-fallback-open' : ''}`}
        style={
          fallbackPos && !supportsAnchor
            ? {
                position: 'fixed',
                top: `${fallbackPos.top}px`,
                left: `${fallbackPos.left}px`,
                maxHeight: `${fallbackPos.maxHeight}px`,
                margin: 0,
              }
            : undefined
        }
      >
        <fieldset className="effort-fieldset">
          <legend className="model-pop-label">Thinking Effort</legend>
          <div className="effort-radio-grid">
            {EFFORT_OPTIONS.map((opt) => {
              const checked = opt.value === effort;
              return (
                <label
                  key={opt.value}
                  className={`effort-radio-option ${checked ? 'selected' : ''}`}
                >
                  <input
                    type="radio"
                    name={effortGroupName}
                    value={opt.value}
                    checked={checked}
                    className="sr-only"
                    onChange={() => void onChangeSelection({ thinking_effort: opt.value })}
                  />
                  <span>{opt.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <div className="model-pop-section">
          <div className="model-pop-head">
            <span className="model-pop-label">Model ({modelList.length})</span>
          </div>

          {modelList.length > 6 && (
            <input
              type="search"
              className="model-search-input"
              aria-label="Filter available models"
              placeholder="Filter models..."
              value={filterText}
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(e) => setFilterText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.preventDefault();
              }}
            />
          )}

          <div className="model-option-list">
            {filteredModels.length === 0 ? (
              <div className="model-option-empty">No matching models</div>
            ) : (
              filteredModels.map((m) => {
                const isCurrent = m === activeModel;
                return (
                  <button
                    key={m}
                    type="button"
                    className={`model-option-item ${isCurrent ? 'selected' : ''}`}
                    onClick={() => {
                      closePopover();
                      void onChangeSelection({ model: m });
                    }}
                  >
                    <span className="model-option-text">{m}</span>
                    {isCurrent && <Check size={13} aria-hidden="true" />}
                  </button>
                );
              })
            )}
          </div>
        </div>

        <button
          type="button"
          className="model-pop-config-btn"
          onClick={() => {
            closePopover();
            onOpenConfig();
          }}
        >
          <Settings2 size={13} aria-hidden="true" />
          <span>Fetch sk models / API Config</span>
        </button>
      </div>
    </div>
  );
};
