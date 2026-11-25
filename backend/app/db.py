from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH, RUNTIME_DIR


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_INIT_LOCK = threading.Lock()
_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id TEXT,
    artifact_version_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_group_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    produced_version_ids_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    concise_summary TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    optional_artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    optional_error TEXT,
    FOREIGN KEY (run_id) REFERENCES task_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dataset_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieval_time TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    query_filter TEXT NOT NULL,
    raw_record_count INTEGER NOT NULL,
    retained_record_count INTEGER NOT NULL,
    schema_json TEXT NOT NULL,
    missingness_json TEXT NOT NULL,
    transform_steps_json TEXT NOT NULL,
    upstream_dependencies_json TEXT NOT NULL,
    script_text TEXT NOT NULL,
    records_ref TEXT NOT NULL,
    semantic_profile_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    dataset_snapshot_id TEXT NOT NULL,
    analysis_algorithm TEXT NOT NULL,
    params_json TEXT NOT NULL,
    script_text TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    result_ref TEXT NOT NULL,
    stdout TEXT NOT NULL,
    stderr TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    chart_kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    artifact_version_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_group_id TEXT NOT NULL,
    parent_version_id TEXT,
    version_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    chart_kind TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    html_ref TEXT NOT NULL,
    thumbnail_ref TEXT NOT NULL,
    dataset_snapshot_ids_json TEXT NOT NULL,
    analysis_run_ids_json TEXT NOT NULL,
    lineage_ref TEXT NOT NULL,
    addressed_comment_ids_json TEXT NOT NULL DEFAULT '[]',
    canvas_json TEXT NOT NULL,
    validation_report_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visual_comments (
    comment_id TEXT PRIMARY KEY,
    artifact_version_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor_json TEXT NOT NULL,
    screenshot_ref TEXT NOT NULL,
    preview_view_state_json TEXT NOT NULL,
    optional_element_hint TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_in_version_id TEXT,
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with _INIT_LOCK:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.executescript(_SCHEMA_SQL)
            msg_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "metadata_json" not in msg_cols:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
            ds_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(dataset_snapshots)").fetchall()
            }
            if "semantic_profile_json" not in ds_cols:
                conn.execute(
                    "ALTER TABLE dataset_snapshots ADD COLUMN semantic_profile_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    return json.loads(raw)
