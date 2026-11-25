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


def test_llm_api_settings_crud_and_masking(monkeypatch) -> None:
    from types import SimpleNamespace
    from openai.resources.models import AsyncModels
    from app.services.api_settings import SETTINGS_FILE, get_effective_llm_credentials

    client = TestClient(app)
    had_existing = SETTINGS_FILE.exists()
    backup_bytes = SETTINGS_FILE.read_bytes() if had_existing else b""

    try:
        SETTINGS_FILE.unlink(missing_ok=True)

        initial = client.get("/api/settings/llm")
        assert initial.status_code == 200
        init_data = initial.json()
        assert init_data["configured"] is False
        assert init_data["api_key_masked"] == ""
        assert init_data["thinking_effort"] == "medium"

        saved = client.put(
            "/api/settings/llm",
            json={
                "provider": "openai",
                "api_key": "sk-test-secret-12345678abcd",
                "base_url": "https://api.openai.com/v1/",
                "model": "gpt-4o-mini",
                "thinking_effort": "medium",
                "available_models": ["gpt-4o-mini", "gpt-4o", "o3-mini"],
            },
        )
        assert saved.status_code == 200
        saved_data = saved.json()
        assert saved_data["configured"] is True
        assert saved_data["provider"] == "openai"
        assert saved_data["base_url"] == "https://api.openai.com/v1"
        assert saved_data["model"] == "gpt-4o-mini"
        assert saved_data["thinking_effort"] == "medium"
        assert saved_data["available_models"] == ["gpt-4o-mini", "gpt-4o", "o3-mini"]
        assert "12345678abcd" not in saved_data["api_key_masked"]
        assert saved_data["api_key_masked"].startswith("sk-")
        assert saved_data["api_key_masked"].endswith("abcd")

        key, base_url, model, effort = get_effective_llm_credentials()
        assert key == "sk-test-secret-12345678abcd"
        assert base_url == "https://api.openai.com/v1"
        assert model == "gpt-4o-mini"
        assert effort == "medium"

        # Quick selection PATCH updates model and thinking_effort without touching API key
        quick = client.patch(
            "/api/settings/llm/selection",
            json={"model": "o3-mini", "thinking_effort": "high"},
        )
        assert quick.status_code == 200
        quick_data = quick.json()
        assert quick_data["configured"] is True
        assert quick_data["model"] == "o3-mini"
        assert quick_data["thinking_effort"] == "high"
        assert get_effective_llm_credentials() == (
            "sk-test-secret-12345678abcd",
            "https://api.openai.com/v1",
            "o3-mini",
            "high",
        )

        # Updating model without re-entering API key preserves existing saved key & models
        updated = client.put(
            "/api/settings/llm",
            json={
                "provider": "openai",
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "thinking_effort": "low",
                "available_models": [],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["configured"] is True
        assert updated.json()["model"] == "gpt-4o"
        assert updated.json()["thinking_effort"] == "low"
        assert "gpt-4o-mini" in updated.json()["available_models"]
        assert get_effective_llm_credentials()[0] == "sk-test-secret-12345678abcd"

        # Fetching sk model list returns sorted, deduplicated model IDs
        async def _fake_list(self, **kwargs):
            return SimpleNamespace(
                data=[
                    SimpleNamespace(id="gpt-4o"),
                    SimpleNamespace(id="deepseek-chat"),
                    SimpleNamespace(id="gpt-4o"),
                    SimpleNamespace(id="claude-3-7-sonnet"),
                ]
            )

        monkeypatch.setattr(AsyncModels, "list", _fake_list)
        fetched = client.post(
            "/api/settings/llm/models",
            json={"api_key": "", "base_url": "https://api.openai.com/v1"},
        )
        assert fetched.status_code == 200
        assert fetched.json() == {
            "models": ["claude-3-7-sonnet", "deepseek-chat", "gpt-4o"],
            "count": 3,
        }

        cleared = client.delete("/api/settings/llm")
        assert cleared.status_code == 200
        assert cleared.json()["configured"] is False
        assert cleared.json()["api_key_masked"] == ""
    finally:
        if had_existing:
            SETTINGS_FILE.write_bytes(backup_bytes)
        else:
            SETTINGS_FILE.unlink(missing_ok=True)


def test_weekly_trending_charts_cache_and_svg_sanitization(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    import json

    from app.services import weekly_trending_charts as wtc

    raw_xss_svg = (
        '<svg viewBox="0 0 100 100" onclick="alert(1)">'
        '<defs><linearGradient id="g1"><stop offset="0%" stop-color="#2563eb"/></linearGradient></defs>'
        '<script>alert("xss")</script>'
        '<rect x="1" y="1" width="20" height="20" fill="url(#g1)" onload="evil()"/>'
        '<circle cx="10" cy="10" r="4" fill="#2563eb"/>'
        '<circle cx="20" cy="20" r="4" fill="#0ea5e9"/>'
        '<line x1="1" y1="1" x2="20" y2="20" stroke="#0f172a"/>'
        '<a href="javascript:alert(1)"><text x="10" y="10">bad</text></a>'
        '</svg>'
    )
    sanitized = wtc.sanitize_schematic_svg(raw_xss_svg, "wk_test_1")
    assert "<script" not in sanitized
    assert "onclick" not in sanitized
    assert "onload" not in sanitized
    assert "javascript:" not in sanitized
    assert 'id="wk_test_1_g1"' in sanitized
    assert 'fill="url(#wk_test_1_g1)"' in sanitized
    assert 'viewBox="0 0 280 124"' in sanitized

    had_existing = wtc.TRENDING_CACHE_FILE.exists()
    backup_bytes = wtc.TRENDING_CACHE_FILE.read_bytes() if had_existing else b""
    wtc.TRENDING_CACHE_FILE.unlink(missing_ok=True)

    fetch_calls = 0

    async def _fake_fetch():
        nonlocal fetch_calls
        fetch_calls += 1
        return {
            "github": [
                {
                    "name": "d3/d3",
                    "description": "Bring data to life",
                    "stars": 110000,
                    "topics": ["d3", "visualization"],
                    "url": "https://github.com/d3/d3",
                    "pushed_at": "2026-11-20",
                }
            ],
            "openalex": [
                {
                    "title": "Interactive Visual Analytics Survey",
                    "year": 2026,
                    "citations": 85,
                    "concepts": ["Visualization"],
                    "url": "https://openalex.org/W12345",
                }
            ],
            "sources": ["GitHub", "OpenAlex"],
        }

    async def _fake_synth(signals, iso_week):
        valid_svg = (
            '<svg viewBox="0 0 280 124" fill="none">'
            '<rect x="10" y="10" width="80" height="40" fill="#2563eb"/>'
            '<circle cx="50" cy="50" r="12" fill="#0ea5e9"/>'
            '<line x1="10" y1="80" x2="200" y2="80" stroke="#0f172a"/>'
            '<path d="M 20 90 L 100 30" stroke="#6366f1"/>'
            "</svg>"
        )
        return [
            {
                "id": "trend-1",
                "title": "D3 Multi-Scale Stream",
                "icon_kind": "trend",
                "summary": "Real-time stream trend from GitHub & OpenAlex.",
                "interactions": ["Hover Crosshair", "Brush Zoom", "Metric Toggle"],
                "prompt": "Build an interactive trend stream chart.",
                "source_label": "GitHub · d3/d3 ★110k",
                "source_url": "https://github.com/d3/d3",
                "schematic_svg": wtc.sanitize_schematic_svg(valid_svg, "t1"),
            },
            {
                "id": "flow-2",
                "title": "Alluvial Cohort Flow",
                "icon_kind": "flow",
                "summary": "Cohort transition flow diagram.",
                "interactions": ["Path Highlight", "Filter", "Tooltip"],
                "prompt": "Build an alluvial flow chart.",
                "source_label": "OpenAlex · Survey",
                "source_url": "https://openalex.org/W12345",
                "schematic_svg": "",
            },
            {
                "id": "network-3",
                "title": "Citation Force Cluster",
                "icon_kind": "network",
                "summary": "Community clustering force graph.",
                "interactions": ["Ego Focus", "Drag", "Tie Filter"],
                "prompt": "Build a citation force graph.",
                "source_label": "GitHub · d3/d3 ★110k",
                "source_url": "https://github.com/d3/d3",
                "schematic_svg": "",
            },
            {
                "id": "heatmap-4",
                "title": "Cohort Density Matrix",
                "icon_kind": "heatmap",
                "summary": "Sortable matrix heatmap.",
                "interactions": ["Sort", "Inspect", "Bars"],
                "prompt": "Build a matrix heatmap.",
                "source_label": "OpenAlex · Survey",
                "source_url": "https://openalex.org/W12345",
                "schematic_svg": "",
            },
        ]

    monkeypatch.setattr(wtc, "fetch_live_trending_signals", _fake_fetch)
    monkeypatch.setattr(wtc, "_synthesize_charts_with_llm", _fake_synth)

    try:
        client = TestClient(app)

        first = client.get("/api/trending-charts")
        assert first.status_code == 200
        d1 = first.json()
        assert d1["cached"] is False
        assert len(d1["items"]) == 4
        assert fetch_calls == 1
        assert wtc.TRENDING_CACHE_FILE.exists()

        # Second call within 7 days serves strictly from local cache without re-fetching
        second = client.get("/api/trending-charts")
        assert second.status_code == 200
        d2 = second.json()
        assert d2["cached"] is True
        assert d2["items"][0]["title"] == "D3 Multi-Scale Stream"
        assert fetch_calls == 1

        # Simulate >7 days elapsed -> triggers refresh once
        cached_raw = json.loads(wtc.TRENDING_CACHE_FILE.read_text(encoding="utf-8"))
        cached_raw["fetched_at"] = (
            datetime.now(timezone.utc) - timedelta(days=8)
        ).isoformat()
        wtc.TRENDING_CACHE_FILE.write_text(
            json.dumps(cached_raw, ensure_ascii=False), encoding="utf-8"
        )

        third = client.get("/api/trending-charts")
        assert third.status_code == 200
        d3 = third.json()
        assert d3["cached"] is False
        assert fetch_calls == 2
    finally:
        if had_existing:
            wtc.TRENDING_CACHE_FILE.write_bytes(backup_bytes)
        else:
            wtc.TRENDING_CACHE_FILE.unlink(missing_ok=True)


def test_viz_agent_chart_first_and_bottom_dock_safe_zone() -> None:
    from app.agents.capability_catalog import D3_V7_CAPABILITY_GUIDE
    from app.agents.visualization_agent import _inject_data_and_bridge

    assert "FIRST in DOM and visual order" in D3_V7_CAPABILITY_GUIDE
    assert "<canvas>" in D3_V7_CAPABILITY_GUIDE
    assert "data-rc-chart-stage" in D3_V7_CAPABILITY_GUIDE
    assert "padding-bottom: 112px" in D3_V7_CAPABILITY_GUIDE
    assert "position: fixed" in D3_V7_CAPABILITY_GUIDE

    sample_html = (
        "<!doctype html><html><head><title>Viz</title></head>"
        '<body><div class="controls">Filter</div><canvas id="main-chart" width="800" height="500"></canvas></body></html>'
    )
    injected = _inject_data_and_bridge(sample_html, {"summary": {"headline": "OK"}, "data": [1, 2]})
    assert "padding-bottom: 112px !important;" in injected
    assert 'data-rc-chart-first="1"' in injected
    assert "canvas" in injected
    assert "data-rc-chart-stage" in injected
    assert "order: 1 !important;" in injected
    assert "data-rc-bridge" in injected


async def test_llm_client_retries_when_high_reasoning_returns_empty_length_content(
    monkeypatch,
) -> None:
    from types import SimpleNamespace
    from openai.resources.chat.completions import AsyncCompletions
    import app.agents.llm_client as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "get_effective_llm_credentials",
        lambda: ("sk-test", "https://api.example.com/v1", "deepseek-chat", "high"),
    )

    seen_efforts: list[str | None] = []

    async def _fake_create(self, **kwargs):
        effort = kwargs.get("reasoning_effort")
        seen_efforts.append(effort)
        user_msg = str(kwargs["messages"][-1]["content"])
        if effort == "high":
            partial = "```html\n<!doctype html><html><body><script>const x =" if "html" in user_msg.lower() else ""
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=partial),
                        finish_reason="length",
                    )
                ]
            )
        reply = '{"ok": true}' if "json" in user_msg.lower() else "<html>ok</html>"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=reply),
                    finish_reason="stop",
                )
            ]
        )

    monkeypatch.setattr(AsyncCompletions, "create", _fake_create)

    client = llm_mod.LLMClient()
    json_res = await client.complete_json("sys", "return json", max_tokens=1024)
    assert json_res == {"ok": True}
    text_res = await client.complete_text("sys", "return html", max_tokens=2048)
    assert text_res == "<html>ok</html>"
    assert llm_mod.extract_code_block("```html\n<div>hi</div>") == "<div>hi</div>"
    assert seen_efforts == ["high", "low", "high", "low"]
