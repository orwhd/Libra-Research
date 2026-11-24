from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# Load local .env if present (never overwriting explicit process env vars)
load_dotenv(REPO_ROOT / ".env", override=False)

DATA_SEED_PATH = Path(
    os.getenv(
        "DATA_SEED_PATH",
        str(REPO_ROOT / "data" / "seed" / "openalex_ai_works_2018_2025.json"),
    )
)
RUNTIME_DIR = Path(
    os.getenv("RUNTIME_DIR", str(REPO_ROOT / "data" / "runtime"))
)
DB_PATH = Path(
    os.getenv("DB_PATH", str(RUNTIME_DIR / "research_canvas.sqlite"))
)
VENDOR_DIR = BACKEND_DIR / "app" / "static" / "vendor"

LLM_API_KEY = (
    os.getenv("LLM_API_KEY")
    or os.getenv("DEEPSEEK_API")
    or os.getenv("OPENAI_API_KEY")
    or ""
).strip()
LLM_BASE_URL = (
    os.getenv("LLM_BASE_URL")
    or os.getenv("DEEPSEEK_URL")
    or "https://api.deepseek.com"
).strip()
LLM_MODEL = (
    os.getenv("LLM_MODEL")
    or os.getenv("DEEPSEEK_MODEL")
    or "deepseek-flash"
).strip()

OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "").strip()

CARD_WIDTH = 340
CARD_HEIGHT = 246
CARD_GAP_X = 96
CARD_GAP_Y = 44
GROUP_GAP_Y = 88
CANVAS_MARGIN_X = 64
CANVAS_MARGIN_Y = 64
MAX_RETRIES = 2
SANDBOX_TIMEOUT_SECONDS = 25
