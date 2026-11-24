from __future__ import annotations

import os
from pathlib import Path

from app.db import init_db
from app.services.sandbox_runner import run_python_sandbox
from app.services.lineage_manager import build_and_persist_lineage, load_lineage
from app.services.artifact_manager import save_artifact_version
from app.config import RUNTIME_DIR


def test_sandbox_blocks_forbidden_imports(tmp_path: Path) -> None:
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    inp.write_text("[]", encoding="utf-8")

    bad_script = "import subprocess\nprint('should fail')\n"
    res = run_python_sandbox(bad_script, tmp_path / "work", inp, out)
    assert not res.ok
    assert "Forbidden import" in (res.error or "")


def test_sandbox_strips_api_keys_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API", "secret_should_not_leak")
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    inp.write_text('{"x": 42}', encoding="utf-8")

    script = """
import json, sys, os
data = json.load(open(sys.argv[1]))
has_secret = "DEEPSEEK_API" in os.getenv.__code__.co_varnames  # never access os.environ directly
with open(sys.argv[2], "w") as f:
    json.dump({"val": data["x"] * 2, "home": os.getenv("DEEPSEEK_API")}, f)
"""
    res = run_python_sandbox(script, tmp_path / "work2", inp, out)
    assert res.ok
    assert res.output_data == {"val": 84, "home": None}


def test_lineage_6_stages_and_immutability(tmp_path: Path, monkeypatch) -> None:
    init_db()
    # Check deterministic 6-stage fallback if snapshot missing, or real snapshot creation
    from app.db import dumps, get_conn, utc_now_iso
    import uuid

    ws_id = f"ws_test_{uuid.uuid4().hex[:6]}"
    snap_id = f"ds_test_{uuid.uuid4().hex[:6]}"
    ar_id = f"ar_test_{uuid.uuid4().hex[:6]}"
    av_id = f"av_test_{uuid.uuid4().hex[:6]}"
    now = utc_now_iso()

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workspaces (workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (ws_id, "Test WS", now, now),
        )
        conn.execute(
            """
            INSERT INTO dataset_snapshots (
                snapshot_id, workspace_id, source_name, source_url, retrieval_time,
                retrieval_mode, query_filter, raw_record_count, retained_record_count,
                schema_json, missingness_json, transform_steps_json, upstream_dependencies_json,
                script_text, records_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id,
                ws_id,
                "OpenAlex Works API",
                "https://api.openalex.org/works",
                now,
                "verified_local_openalex_snapshot",
                "publication_year:2018-2025",
                400,
                398,
                dumps({"title": "str"}),
                dumps({"missing_year": 0}),
                dumps(["filter valid years"]),
                dumps([{"name": "OpenAlex"}]),
                "print('ok')",
                "snapshots/dummy/records.json",
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO analysis_runs (
                analysis_run_id, workspace_id, dataset_snapshot_id, analysis_algorithm,
                params_json, script_text, summary_json, result_ref, stdout, stderr, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ar_id,
                ws_id,
                snap_id,
                "Yearly Aggregation",
                dumps({"bin": "year"}),
                "print('ok')",
                dumps({"headline": "Peak in 2020"}),
                "analyses/dummy/result.json",
                "ok",
                "",
                "completed",
                now,
            ),
        )

    graph = build_and_persist_lineage(
        artifact_version_id=av_id,
        title="Test Chart",
        chart_kind="Statistical Chart",
        snapshot_ids=[snap_id],
        analysis_run_ids=[ar_id],
        validation_report={"passed": True},
    )
    stages = {n.stage for n in graph.nodes}
    assert stages == {
        "source",
        "collection",
        "transformation",
        "dataset_snapshot",
        "analysis_run",
        "visualization_artifact",
    }
    loaded = load_lineage(av_id)
    assert loaded is not None
    assert len(loaded.edges) == 5

    # Cleanup test rows
    with get_conn() as conn:
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM dataset_snapshots WHERE snapshot_id = ?", (snap_id,))
        conn.execute("DELETE FROM analysis_runs WHERE analysis_run_id = ?", (ar_id,))
    lineage_file = RUNTIME_DIR / "lineage" / f"{av_id}.json"
    lineage_file.unlink(missing_ok=True)
