from __future__ import annotations

import uuid
from pathlib import Path
from fastapi.testclient import TestClient

from app.config import RUNTIME_DIR
from app.db import dumps, get_conn, init_db, utc_now_iso
from app.main import app
from app.services.artifact_manager import save_artifact_version
from app.services.lineage_manager import build_and_persist_lineage


def test_comment_saves_without_creating_version_and_layout_invariants() -> None:
    init_db()
    client = TestClient(app)
    ws = client.post("/api/workspaces", json={"title": "Comment Persistence Test"}).json()
    ws_id = ws["workspace_id"]

    av1_id = f"av_t1_{uuid.uuid4().hex[:6]}"
    av2_id = f"av_t2_{uuid.uuid4().hex[:6]}"
    av1_rev_id = f"av_t1r_{uuid.uuid4().hex[:6]}"
    art1_id = f"art_1_{uuid.uuid4().hex[:6]}"
    art2_id = f"art_2_{uuid.uuid4().hex[:6]}"

    html_sample = (
        '<!doctype html><html><body><svg width="800" height="500">'
        '<circle cx="200" cy="200" r="40" fill="#2563eb"/>'
        '<circle cx="320" cy="200" r="40" fill="#0ea5e9"/>'
        '<text x="200" y="260">Node A</text></svg></body></html>'
    )

    thumb1 = RUNTIME_DIR / "artifacts" / av1_id / "preview.png"
    thumb1.parent.mkdir(parents=True, exist_ok=True)
    thumb1.write_bytes(b"\x89PNG\r\n\x1a\n")

    thumb2 = RUNTIME_DIR / "artifacts" / av2_id / "preview.png"
    thumb2.parent.mkdir(parents=True, exist_ok=True)
    thumb2.write_bytes(b"\x89PNG\r\n\x1a\n")

    thumb1r = RUNTIME_DIR / "artifacts" / av1_rev_id / "preview.png"
    thumb1r.parent.mkdir(parents=True, exist_ok=True)
    thumb1r.write_bytes(b"\x89PNG\r\n\x1a\n")

    build_and_persist_lineage(av1_id, "Chart 1", "Bar", [], [], {"passed": True})
    av1 = save_artifact_version(
        av1_id, art1_id, ws_id, "task_a", None, 1, "Chart 1", "Bar",
        html_sample, thumb1, [], [], f"lineage/{av1_id}.json", [], {"passed": True}, "s1"
    )
    build_and_persist_lineage(av2_id, "Chart 2", "Network", [], [], {"passed": True})
    av2 = save_artifact_version(
        av2_id, art2_id, ws_id, "task_a", None, 1, "Chart 2", "Network",
        html_sample, thumb2, [], [], f"lineage/{av2_id}.json", [], {"passed": True}, "s2"
    )

    # Vertical invariant: av2 below av1, same column 0
    assert av2.canvas.revision_column == 0
    assert av2.canvas.y > av1.canvas.y

    # Save visual comment on av1
    resp = client.post(
        f"/api/versions/{av1_id}/comments",
        json={
            "text": "Increase node spacing",
            "anchor": {"x_norm": 0.45, "y_norm": 0.4},
            "preview_view_state": {"viewport_width": 800, "viewport_height": 500},
        },
    )
    assert resp.status_code == 200
    comment = resp.json()
    assert comment["status"] == "pending"

    # Verify NO new version created and pending_comment_count == 1
    canvas_after = client.get(f"/api/workspaces/{ws_id}/canvas").json()
    assert len(canvas_after["artifact_versions"]) == 2
    av1_fetched = next(v for v in canvas_after["artifact_versions"] if v["artifact_version_id"] == av1_id)
    assert av1_fetched["pending_comment_count"] == 1

    # Save revision av1_rev_id addressing that comment -> should appear to the RIGHT of av1
    build_and_persist_lineage(av1_rev_id, "Chart 1", "Bar", [], [], {"passed": True}, parent_version_id=av1_id)
    av1_rev = save_artifact_version(
        av1_rev_id, art1_id, ws_id, "task_a", av1_id, 2, "Chart 1", "Bar",
        html_sample, thumb1r, [], [], f"lineage/{av1_rev_id}.json", [comment["comment_id"]],
        {"passed": True}, "revised"
    )
    assert av1_rev.canvas.revision_column == 1
    assert av1_rev.canvas.x > av1.canvas.x

    # Old comment is now addressed and preserved on av1
    comments_v1 = client.get(f"/api/versions/{av1_id}/comments").json()
    assert comments_v1[0]["status"] == "addressed"
    assert comments_v1[0]["resolved_in_version_id"] == av1_rev_id

    # Dragging card updates and persists freeform Canvas coordinates
    move_resp = client.patch(f"/api/versions/{av1_id}/position", json={"x": 512.5, "y": 384.0})
    assert move_resp.status_code == 200
    moved = move_resp.json()
    assert moved["canvas"]["x"] == 512.5
    assert moved["canvas"]["y"] == 384.0
    canvas_reloaded = client.get(f"/api/workspaces/{ws_id}/canvas").json()
    av1_reloaded = next(v for v in canvas_reloaded["artifact_versions"] if v["artifact_version_id"] == av1_id)
    assert av1_reloaded["canvas"]["x"] == 512.5
    assert av1_reloaded["canvas"]["y"] == 384.0

    # File/Folder upload ingestion endpoint
    import base64, shutil
    csv_b64 = base64.b64encode(b"brand,share\nApple,55.2\nSamsung,24.1\n").decode("ascii")
    up_resp = client.post(
        "/api/uploads",
        json={
            "label": "market_data",
            "files": [
                {"relative_path": "market_data/phones.csv", "content_base64": csv_b64},
                {"relative_path": "market_data/.DS_Store", "content_base64": csv_b64},
            ],
        },
    )
    assert up_resp.status_code == 200
    up_data = up_resp.json()
    assert up_data["file_count"] == 1
    assert up_data["files"] == ["market_data/phones.csv"]
    shutil.rmtree(RUNTIME_DIR / "uploads" / up_data["upload_id"], ignore_errors=True)

    # Cleanup
    with get_conn() as conn:
        conn.execute("DELETE FROM visual_comments WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifact_versions WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifacts WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (ws_id,))
    for vid in (av1_id, av2_id, av1_rev_id):
        (RUNTIME_DIR / "lineage" / f"{vid}.json").unlink(missing_ok=True)
    shot = RUNTIME_DIR / comment["screenshot_ref"]
    shot.unlink(missing_ok=True)


def test_url_upload_validation_and_html_extraction() -> None:
    from app.api.routes import extract_html_tables_and_text

    client = TestClient(app)

    # SSRF & invalid scheme checks must return 400
    for bad_url in (
        "http://127.0.0.1:8000/api/health",
        "http://localhost:8000/api/health",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ):
        resp = client.post("/api/uploads/url", json={"url": bad_url})
        assert resp.status_code == 400, f"Expected 400 for {bad_url}, got {resp.status_code}"

    # HTML table & visible text extraction must ignore scripts/styles
    sample_html = """
    <!doctype html>
    <html>
      <head>
        <style>.hidden { display: none; }</style>
        <script>console.log("secret script payload");</script>
      </head>
      <body>
        <h1>Market Report</h1>
        <p>Annual smartphone share summary.</p>
        <table>
          <tr><th>Brand</th><th>Share</th></tr>
          <tr><td>Apple</td><td>55.2</td></tr>
          <tr><td>Samsung</td><td>24.1</td></tr>
        </table>
      </body>
    </html>
    """
    tables, page_text = extract_html_tables_and_text(sample_html)
    assert len(tables) == 1
    assert tables[0] == [["Brand", "Share"], ["Apple", "55.2"], ["Samsung", "24.1"]]
    assert "Market Report" in page_text
    assert "Annual smartphone share summary." in page_text
    assert "secret script payload" not in page_text
    assert ".hidden" not in page_text


def test_submitting_task_attaches_pending_comments_to_user_message(monkeypatch) -> None:
    import shutil
    from app.agents.orchestrator import ResearchOrchestrator

    init_db()
    client = TestClient(app)
    ws = client.post("/api/workspaces", json={"title": "Comment Attach Submit Test"}).json()
    ws_id = ws["workspace_id"]

    av_id = f"av_att_{uuid.uuid4().hex[:6]}"
    art_id = f"art_att_{uuid.uuid4().hex[:6]}"
    html_sample = '<!doctype html><html><body><svg width="400" height="300"></svg></body></html>'
    thumb = RUNTIME_DIR / "artifacts" / av_id / "preview.png"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n")

    build_and_persist_lineage(av_id, "Trend Chart", "Line", [], [], {"passed": True})
    save_artifact_version(
        av_id,
        art_id,
        ws_id,
        "task_att",
        None,
        1,
        "Trend Chart",
        "Line",
        html_sample,
        thumb,
        [],
        [],
        f"lineage/{av_id}.json",
        [],
        {"passed": True},
        "s1",
    )

    c_resp = client.post(
        f"/api/versions/{av_id}/comments",
        json={
            "text": "Make axis ticks clearer",
            "anchor": {"x_norm": 0.3, "y_norm": 0.5},
            "preview_view_state": {"viewport_width": 800, "viewport_height": 500},
        },
    )
    assert c_resp.status_code == 200
    comment = c_resp.json()

    captured: dict[str, object] = {}

    async def _noop_execute_run(self, run_id, workspace_id, task_group_id, req, submitted_comments=None):
        captured["prompt"] = req.prompt
        captured["submitted_comments"] = submitted_comments

    monkeypatch.setattr(ResearchOrchestrator, "execute_run", _noop_execute_run)

    # Comment-only submission (empty prompt) must succeed, consume pending comments,
    # and attach them onto the user's chat message immediately.
    task_resp = client.post(
        f"/api/workspaces/{ws_id}/tasks",
        json={"prompt": "", "apply_pending_comments": True},
    )
    assert task_resp.status_code == 200
    assert av_id in (captured.get("submitted_comments") or {})

    detail = client.get(f"/api/workspaces/{ws_id}").json()
    assert detail["artifact_versions"][0]["pending_comment_count"] == 0
    user_msgs = [m for m in detail["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == ""
    assert len(user_msgs[0]["attached_comments"]) == 1
    assert user_msgs[0]["attached_comments"][0]["comment_id"] == comment["comment_id"]
    assert user_msgs[0]["attached_comments"][0]["text"] == "Make axis ticks clearer"

    # Cleanup
    with get_conn() as conn:
        conn.execute("DELETE FROM run_events WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM task_runs WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM messages WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM visual_comments WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifact_versions WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM artifacts WHERE workspace_id = ?", (ws_id,))
        conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (ws_id,))
    (RUNTIME_DIR / "lineage" / f"{av_id}.json").unlink(missing_ok=True)
    shutil.rmtree(RUNTIME_DIR / "artifacts" / av_id, ignore_errors=True)
    (RUNTIME_DIR / comment["screenshot_ref"]).unlink(missing_ok=True)
