from __future__ import annotations

import uuid
import pytest

from app.agents.orchestrator import ResearchOrchestrator
from app.config import LLM_API_KEY, RUNTIME_DIR
from app.db import get_conn, init_db, utc_now_iso
from app.models import CreateTaskRequest
from app.services.artifact_manager import list_workspace_artifact_versions
from app.services.lineage_manager import load_lineage


@pytest.mark.skipif(not LLM_API_KEY, reason="LLM_API_KEY not configured in environment")
async def test_live_harness_unseen_heatmap_and_fault_retry_s10_s11() -> None:
    """
    Live integration test verifying:
    - S10 Generalizability: unseen 4th chart type (topic-by-year heatmap) executes end-to-end
      through Orchestrator -> DataAgent -> AnalysisAgent -> VisualizationAgent -> ValidationAgent.
    - S11 Fault Injection & Bounded Retry: `inject_validation_fault_once=True` injects a real
      runtime JS error on attempt 1, ValidationAgent catches it in Chromium, emits a retry event,
      and VisualizationAgent repairs & validates on attempt 2.
    """
    init_db()
    ws_id = f"ws_live_{uuid.uuid4().hex[:8]}"
    run_id = f"run_live_{uuid.uuid4().hex[:8]}"
    task_group_id = f"task_live_{uuid.uuid4().hex[:8]}"
    now = utc_now_iso()
    prompt = "Create an interactive topic-by-year heatmap with hover explanations and year filtering."

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workspaces (workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (ws_id, "Live Harness S10/S11 Test", now, now),
        )
        conn.execute(
            """
            INSERT INTO task_runs (
                run_id, workspace_id, task_group_id, prompt, status, stage,
                produced_version_ids_json, error_message, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', 'created', '[]', NULL, 0, ?, ?)
            """,
            (run_id, ws_id, task_group_id, prompt, now, now),
        )

    req = CreateTaskRequest(prompt=prompt, inject_validation_fault_once=True)
    orchestrator = ResearchOrchestrator()
    await orchestrator.execute_run(run_id, ws_id, task_group_id, req)

    with get_conn() as conn:
        run_row = dict(conn.execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,)).fetchone())
        event_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
            ).fetchall()
        ]

    assert run_row["status"] == "completed", f"Run failed: {run_row}"
    assert run_row["retry_count"] >= 1, "Expected at least 1 retry after injected fault"
    stages = [e["stage"] for e in event_rows]
    assert "retrying" in stages, f"Expected retrying stage in events: {stages}"

    versions = list_workspace_artifact_versions(ws_id)
    assert len(versions) == 1
    av = versions[0]
    assert av.status == "validated"
    assert (RUNTIME_DIR / av.thumbnail_ref).exists()
    assert (RUNTIME_DIR / av.html_ref).exists()
    lineage = load_lineage(av.artifact_version_id)
    assert lineage is not None
    assert {n.stage for n in lineage.nodes} == {
        "source",
        "collection",
        "transformation",
        "dataset_snapshot",
        "analysis_run",
        "visualization_artifact",
    }

    # Cleanup live test records so default workspace stays clean
    with get_conn() as conn:
        conn.execute("DELETE FROM task_runs WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM messages WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifact_versions WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifacts WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM analysis_runs WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM dataset_snapshots WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (ws_id,))
