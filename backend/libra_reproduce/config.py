from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
REPO_ROOT = BACKEND_DIR.parent if BACKEND_DIR.name == "backend" else BACKEND_DIR

load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(BACKEND_DIR / ".env", override=False)


@dataclass(frozen=True)
class HarnessResourceLimits:
    max_active_agent_tasks: int = 4
    max_parallel_experiments: int = 2
    max_subagent_depth: int = 2
    max_debate_turns: int = 6
    exploration_budget_fraction: float = 0.20
    max_task_retries: int = 2
    experiment_timeout_seconds: int = 180


DEFAULT_LIMITS = HarnessResourceLimits(
    max_active_agent_tasks=max(1, int(os.getenv("REPRODUCE_MAX_ACTIVE_AGENT_TASKS", "4"))),
    max_parallel_experiments=max(1, int(os.getenv("REPRODUCE_MAX_PARALLEL_EXPERIMENTS", "2"))),
    max_subagent_depth=max(1, int(os.getenv("REPRODUCE_MAX_SUBAGENT_DEPTH", "2"))),
    max_debate_turns=max(1, int(os.getenv("REPRODUCE_MAX_DEBATE_TURNS", "6"))),
    exploration_budget_fraction=min(
        0.50, max(0.05, float(os.getenv("REPRODUCE_EXPLORATION_BUDGET_FRACTION", "0.20")))
    ),
    max_task_retries=max(0, int(os.getenv("REPRODUCE_MAX_TASK_RETRIES", "2"))),
    experiment_timeout_seconds=max(15, int(os.getenv("REPRODUCE_EXPERIMENT_TIMEOUT_SECONDS", "180"))),
)


def get_runtime_dir() -> Path:
    env_override = os.getenv("REPRODUCE_RUNTIME_DIR") or os.getenv("RUNTIME_DIR")
    if env_override:
        return Path(env_override).resolve()
    return (REPO_ROOT / "data" / "runtime").resolve()


def get_reproduce_root() -> Path:
    p = get_runtime_dir() / "reproduce"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_reproduce_db_path() -> Path:
    env_db = os.getenv("REPRODUCE_DB_PATH")
    if env_db:
        p = Path(env_db).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return get_reproduce_root() / "reproduce_v2.sqlite"


def get_bundled_samples_dir() -> Path:
    candidates = [
        REPO_ROOT / "data" / "samples",
        BACKEND_DIR / "data" / "samples",
        PACKAGE_DIR / "samples",
    ]
    for c in candidates:
        if c.exists():
            return c
    out = REPO_ROOT / "data" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_llm_runtime_config() -> dict[str, Any]:
    """
    Resolve available live LLM / Agent SDK credentials without ever logging secrets.
    Supports:
    1. Claude Agent SDK (via ANTHROPIC_API_KEY or logged-in `claude` CLI OAuth)
    2. OpenAI-compatible API (via .env LLM_API_KEY/DEEPSEEK_API/OPENAI_API_KEY or runtime user_api_config.json)
    """
    forced_provider = (os.getenv("REPRODUCE_SDK_PROVIDER") or "").strip().lower()
    settings_path = get_runtime_dir() / "user_api_config.json"
    saved: dict[str, Any] = {}
    if settings_path.exists():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                saved = raw
        except Exception:
            saved = {}

    ui_key = str(saved.get("api_key") or "").strip() if saved.get("configured") else ""
    env_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    openai_api_key = ui_key or env_key
    openai_base_url = str(
        (saved.get("base_url") if ui_key else None)
        or os.getenv("LLM_BASE_URL")
        or os.getenv("DEEPSEEK_URL")
        or "https://api.deepseek.com"
    ).strip()
    openai_model = str(
        (saved.get("model") if ui_key else None)
        or os.getenv("LLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-flash"
    ).strip()
    try:
        max_concurrency = max(
            1,
            min(
                16,
                int(saved.get("max_concurrency") or os.getenv("LLM_MAX_CONCURRENCY", "4")),
            ),
        )
    except (TypeError, ValueError):
        max_concurrency = 4

    thinking_effort = str(
        saved.get("thinking_effort")
        or os.getenv("REPRODUCE_THINKING_EFFORT")
        or "high"
    ).strip().lower()
    if thinking_effort not in ("low", "medium", "high"):
        thinking_effort = "high"

    has_claude_cli = bool(shutil.which("claude"))
    has_anthropic_key = bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())

    return {
        "forced_provider": forced_provider,
        "has_claude_cli": has_claude_cli,
        "has_anthropic_key": has_anthropic_key,
        "has_openai_compatible": bool(openai_api_key),
        "has_ui_openai_config": bool(ui_key),
        "openai_api_key": openai_api_key,
        "openai_base_url": openai_base_url,
        "openai_model": openai_model,
        "max_concurrency": max_concurrency,
        "thinking_effort": thinking_effort,
        "openalex_email": (os.getenv("OPENALEX_EMAIL") or "").strip(),
        "openalex_api_key": (os.getenv("OPENALEX_API_KEY") or "").strip(),
    }
