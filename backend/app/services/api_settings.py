from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_CONCURRENCY,
    LLM_MODEL,
    RUNTIME_DIR,
)

SETTINGS_FILE: Path = RUNTIME_DIR / "user_api_config.json"
_VALID_EFFORTS = frozenset({"low", "medium", "high"})


def _normalize_concurrency(val: Any) -> int:
    try:
        n = int(val)
    except (TypeError, ValueError):
        return LLM_MAX_CONCURRENCY
    return max(1, min(16, n))


def mask_api_key(key: str) -> str:
    cleaned = key.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "••••••••"
    return f"{cleaned[:3]}••••{cleaned[-4:]}"


def _normalize_effort(val: Any) -> str:
    s = str(val or "medium").strip().lower()
    return s if s in _VALID_EFFORTS else "medium"


def _normalize_models(models: list[str] | None, current_model: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in models or []:
        m = str(raw or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    cur = current_model.strip()
    if cur and cur not in seen:
        out.insert(0, cur)
    return out


def load_raw_user_llm_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def get_llm_settings_public() -> dict[str, Any]:
    saved = load_raw_user_llm_settings()
    raw_key = str(saved.get("api_key") or "").strip()
    configured = bool(saved.get("configured") and raw_key)
    model = str(saved.get("model") or LLM_MODEL or "deepseek-chat").strip()
    effort = _normalize_effort(saved.get("thinking_effort"))
    raw_models = saved.get("available_models")
    models = _normalize_models(raw_models if isinstance(raw_models, list) else [], model)
    return {
        "configured": configured,
        "provider": str(saved.get("provider") or "deepseek"),
        "base_url": str(
            saved.get("base_url") or LLM_BASE_URL or "https://api.deepseek.com"
        ),
        "model": model,
        "thinking_effort": effort,
        "max_concurrency": _normalize_concurrency(saved.get("max_concurrency")),
        "available_models": models,
        "api_key_masked": mask_api_key(raw_key) if configured else "",
        "has_env_fallback": bool(LLM_API_KEY),
    }


def _write_settings_file(payload: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass


def save_user_llm_settings(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    thinking_effort: str = "medium",
    available_models: list[str] | None = None,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    existing = load_raw_user_llm_settings()
    final_key = api_key.strip() or str(existing.get("api_key") or "").strip()
    if not final_key:
        raise ValueError("API Key is required.")

    cleaned_url = base_url.strip().rstrip("/")
    if not (cleaned_url.startswith("http://") or cleaned_url.startswith("https://")):
        raise ValueError("API Base URL must start with http:// or https://.")

    cleaned_model = model.strip()
    if not cleaned_model:
        raise ValueError("Model name is required.")

    existing_models = existing.get("available_models")
    source_models = (
        available_models
        if available_models
        else (existing_models if isinstance(existing_models, list) else [])
    )
    resolved_concurrency = (
        _normalize_concurrency(max_concurrency)
        if max_concurrency is not None
        else _normalize_concurrency(existing.get("max_concurrency"))
    )
    payload = {
        "configured": True,
        "provider": provider.strip() or "custom",
        "api_key": final_key,
        "base_url": cleaned_url,
        "model": cleaned_model,
        "thinking_effort": _normalize_effort(thinking_effort),
        "max_concurrency": resolved_concurrency,
        "available_models": _normalize_models(source_models, cleaned_model),
    }
    _write_settings_file(payload)
    return get_llm_settings_public()


def update_user_llm_selection(
    model: str | None = None,
    thinking_effort: str | None = None,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    existing = load_raw_user_llm_settings()
    raw_key = str(existing.get("api_key") or "").strip()
    configured = bool(existing.get("configured") and raw_key)
    cur_model = str(existing.get("model") or LLM_MODEL or "deepseek-chat").strip()
    next_model = model.strip() if (model is not None and model.strip()) else cur_model
    next_effort = (
        _normalize_effort(thinking_effort)
        if thinking_effort is not None
        else _normalize_effort(existing.get("thinking_effort"))
    )
    next_concurrency = (
        _normalize_concurrency(max_concurrency)
        if max_concurrency is not None
        else _normalize_concurrency(existing.get("max_concurrency"))
    )
    existing_models = existing.get("available_models")
    payload = {
        "configured": configured,
        "provider": str(existing.get("provider") or "deepseek"),
        "api_key": raw_key,
        "base_url": str(
            existing.get("base_url") or LLM_BASE_URL or "https://api.deepseek.com"
        ).strip(),
        "model": next_model,
        "thinking_effort": next_effort,
        "max_concurrency": next_concurrency,
        "available_models": _normalize_models(
            existing_models if isinstance(existing_models, list) else [],
            next_model,
        ),
    }
    _write_settings_file(payload)
    return get_llm_settings_public()


def clear_user_llm_settings() -> dict[str, Any]:
    SETTINGS_FILE.unlink(missing_ok=True)
    return get_llm_settings_public()


def get_effective_llm_credentials() -> tuple[str, str, str, str]:
    saved = load_raw_user_llm_settings()
    raw_key = str(saved.get("api_key") or "").strip()
    effort = _normalize_effort(saved.get("thinking_effort"))
    if saved.get("configured") and raw_key:
        base_url = str(saved.get("base_url") or LLM_BASE_URL or "https://api.deepseek.com").strip()
        model = str(saved.get("model") or LLM_MODEL or "deepseek-chat").strip()
        return raw_key, base_url, model, effort
    saved_model = str(saved.get("model") or LLM_MODEL or "deepseek-chat").strip()
    return LLM_API_KEY, LLM_BASE_URL, saved_model, effort


def get_effective_llm_concurrency() -> int:
    saved = load_raw_user_llm_settings()
    return _normalize_concurrency(saved.get("max_concurrency"))
