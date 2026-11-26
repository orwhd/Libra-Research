from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..config import get_reproduce_db_path

_DB_LOCK = threading.RLock()

REPRODUCE_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS reproduce_runs (
    run_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    paper_id TEXT NOT NULL DEFAULT 'paper_A',
    status TEXT NOT NULL DEFAULT 'created',
    mode TEXT NOT NULL DEFAULT 'live',
    workspace_id TEXT,
    report_status TEXT NOT NULL DEFAULT 'not_started',
    report_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    missing_requirements_json TEXT NOT NULL DEFAULT '[]',
    resources_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reproduce_runs_ws ON reproduce_runs(workspace_id);

CREATE TABLE IF NOT EXISTS reproduce_commands (
    command_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    text TEXT NOT NULL,
    selected_context_json TEXT,
    attachment_ids_json TEXT NOT NULL DEFAULT '[]',
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, request_id),
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_id TEXT UNIQUE NOT NULL,
    occurred_at TEXT NOT NULL,
    type TEXT NOT NULL,
    actor_id TEXT,
    target_ids_json TEXT NOT NULL DEFAULT '[]',
    subject_json TEXT,
    status TEXT,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    mode TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_personas (
    run_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    role TEXT NOT NULL,
    label TEXT NOT NULL,
    runtime_agent_id TEXT,
    state TEXT NOT NULL DEFAULT 'idle',
    is_main INTEGER NOT NULL DEFAULT 0,
    task_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, persona_id),
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_task_id TEXT,
    persona_id TEXT NOT NULL,
    role TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 10,
    is_exploration INTEGER NOT NULL DEFAULT 0,
    budget_json TEXT NOT NULL DEFAULT '{}',
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_debates (
    debate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    participants_json TEXT NOT NULL DEFAULT '[]',
    turns_json TEXT NOT NULL DEFAULT '[]',
    proposed_experiments_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    resolution_summary TEXT NOT NULL DEFAULT '',
    updated_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_pods (
    pod_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    research_question TEXT NOT NULL,
    members_json TEXT NOT NULL DEFAULT '[]',
    budget_fraction REAL NOT NULL DEFAULT 0.10,
    status TEXT NOT NULL,
    active_tasks_json TEXT NOT NULL DEFAULT '[]',
    outcome_summary TEXT NOT NULL DEFAULT '',
    updated_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_collaborations (
    collaboration_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    topic TEXT NOT NULL,
    participant_persona_ids_json TEXT NOT NULL,
    ref_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    last_event_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, collaboration_id),
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_messages (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    related_refs_json TEXT NOT NULL DEFAULT '[]',
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    seq INTEGER,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reproduce_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    dataset_manifest_id TEXT NOT NULL,
    script_hash TEXT,
    result_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    reviewer_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES reproduce_runs(run_id) ON DELETE CASCADE
);
"""

_INITIALIZED_PATHS: set[Path] = set()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jdumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def jloads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    return json.loads(raw)


def init_reproduce_db(db_path: Path | None = None) -> Path:
    target = (db_path or get_reproduce_db_path()).resolve()
    with _DB_LOCK:
        if target in _INITIALIZED_PATHS and target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target, timeout=30.0) as conn:
            conn.executescript(REPRODUCE_SCHEMA_SQL)
            conn.commit()
        _INITIALIZED_PATHS.add(target)
        return target


@contextmanager
def get_reproduce_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = init_reproduce_db(db_path)
    with _DB_LOCK:
        conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
