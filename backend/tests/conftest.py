from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_REAL_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_USER_CFG = _REAL_REPO_ROOT / "data" / "runtime" / "user_api_config.json"

_TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="libra_pytest_runtime_"))
os.environ["RUNTIME_DIR"] = str(_TEST_RUNTIME_DIR)
os.environ["DB_PATH"] = str(_TEST_RUNTIME_DIR / "test_research_canvas.sqlite")

# Copy user API config into isolated test runtime so live-LLM tests can read credentials
# without ever writing test workspaces into the user's production SQLite database.
if _REAL_USER_CFG.exists():
    shutil.copy2(_REAL_USER_CFG, _TEST_RUNTIME_DIR / "user_api_config.json")
