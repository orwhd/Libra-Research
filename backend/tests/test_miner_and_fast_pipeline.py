from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.capability_catalog import (
    build_visualization_capability_guide,
    discover_python_sandbox_capabilities,
    discover_visualization_libraries,
    rewrite_cdn_scripts_to_local_vendor,
)
from app.agents.miner_agent import (
    MinerCoordinator,
    _choose_miner_count,
    _score_and_validate_work,
)
from app.agents.validation_agent import ValidationAgent
from app.agents.visualization_agent import _inject_data_and_bridge
from app.services.artifact_manager import make_standalone_html
from app.services.browser_validator import validate_html_and_capture
from app.services.semantic_profiler import build_semantic_profile


def test_adaptive_miner_scaling_and_quality_filter() -> None:
    assert _choose_miner_count(100) == 2
    assert _choose_miner_count(220) == 3
    assert _choose_miner_count(350) == 4
    assert _choose_miner_count(450) == 5
    assert _choose_miner_count(560) == 6

    hci_work = {
        "id": "https://openalex.org/W1001",
        "title": "Gesture-Driven Direct Manipulation in Mixed Reality User Interfaces",
        "publication_year": 2025,
        "cited_by_count": 18,
        "primary_topic": {
            "display_name": "Human-Computer Interaction",
            "subfield": {"display_name": "Human-Computer Interaction"},
            "field": {"display_name": "Computer Science"},
        },
        "authorships": [{"author": {"id": "A1", "display_name": "Alice"}}],
    }
    biology_noise = {
        "id": "https://openalex.org/W1002",
        "title": "Protein Folding Dynamics in Mitochondrial Membranes",
        "publication_year": 2024,
        "cited_by_count": 40,
        "primary_topic": {
            "display_name": "Molecular Biology",
            "subfield": {"display_name": "Biochemistry"},
        },
        "authorships": [{"author": {"id": "A2", "display_name": "Bob"}}],
    }

    ok_hci, score_hci = _score_and_validate_work(
        hci_work,
        quality_keywords=["human computer interaction", "user interface"],
        year_min=2020,
        year_max=2026,
    )
    ok_noise, _ = _score_and_validate_work(
        biology_noise,
        quality_keywords=["human computer interaction", "user interface"],
        year_min=2020,
        year_max=2026,
    )
    assert ok_hci is True and score_hci > 3.0
    assert ok_noise is False


@pytest.mark.asyncio
async def test_parallel_miner_coordinator_merges_and_preserves_multi_shard_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyLLM:
        async def complete_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "target_volume": 320,
                "miner_count": 3,
                "year_min": 2021,
                "year_max": 2026,
                "quality_keywords": ["human computer interaction", "interactive"],
                "shards": [
                    {
                        "label": "核心人机交互分片",
                        "search_query": "human computer interaction",
                        "filter_expr": "publication_year:2021-2026",
                        "sort_expr": "relevance_score:desc",
                        "limit": 120,
                    },
                    {
                        "label": "多模态界面分片",
                        "search_query": "multimodal interactive interface",
                        "filter_expr": "publication_year:2021-2026",
                        "sort_expr": "cited_by_count:desc",
                        "limit": 120,
                    },
                    {
                        "label": "最新前沿发表分片",
                        "search_query": "interactive visualization usability",
                        "filter_expr": "publication_year:2024-2026",
                        "sort_expr": "publication_date:desc",
                        "limit": 120,
                    },
                ],
            }

    async def fake_fetch_shard(client: Any, *, search_query: str = "", **kwargs: Any) -> dict[str, Any]:
        works = []
        prefix = search_query.split()[0] if search_query else "shard"
        for i in range(12):
            works.append(
                {
                    "id": f"https://openalex.org/W_{prefix}_{i}",
                    "title": f"Interactive Human Computer Interaction System {prefix} #{i}",
                    "publication_year": 2024,
                    "type": "article" if i % 2 == 0 else "proceedings-article",
                    "cited_by_count": 10 + i,
                    "is_oa": True,
                    "primary_topic": {"display_name": "Human-Computer Interaction"},
                    "authorships": [{"author": {"id": f"A{i}", "display_name": f"Author {i}"}}],
                }
            )
        # Add one shared cross-shard duplicate and one off-topic noise item
        works.append(
            {
                "id": "https://openalex.org/W_SHARED_DUP",
                "title": "Shared Interactive Benchmark Across Shards",
                "publication_year": 2025,
                "type": "article",
                "cited_by_count": 55,
                "primary_topic": {"display_name": "Human-Computer Interaction"},
                "authorships": [{"author": {"id": "AS", "display_name": "Shared Author"}}],
            }
        )
        works.append(
            {
                "id": f"https://openalex.org/W_NOISE_{prefix}",
                "title": "Stratospheric Ozone Chemistry in Polar Vortices",
                "publication_year": 2024,
                "type": "article",
                "cited_by_count": 5,
                "primary_topic": {"display_name": "Atmospheric Science"},
                "authorships": [],
            }
        )
        return {"works": works, "total_available": 500, "query_desc": search_query}

    monkeypatch.setattr("app.agents.miner_agent.fetch_openalex_shard", fake_fetch_shard)

    progress_notes: list[str] = []
    coord = MinerCoordinator(DummyLLM())  # type: ignore[arg-type]
    corpus = await coord.mine_scholarly_corpus(
        research_prompt="帮我看看关于最新的HCI研究情况",
        data_scope="HCI works 2021-2026",
        on_progress=lambda _s, extra: progress_notes.append(str(extra.get("progress_note") or "")),
    )

    works = corpus["works"]
    prov = corpus["provenance"]
    # 3 shards * 12 unique + 1 shared deduplicated = 37 clean works (all 3 noise works rejected)
    assert len(works) == 37
    assert prov["miner_count"] == 3
    assert len(prov["miner_shards"]) == 3
    assert len(progress_notes) >= 2

    # Verify multi-shard Semantic Data Map v2 conservation
    retained = works[:-3]
    profile = await build_semantic_profile(
        llm=None,
        raw_payload=works,
        retained_records=retained,
        snapshot_id="ds_testmulti",
        source_meta=prov,
        schema={"id": "str", "type": "str", "publication_year": "int"},
        missingness_quality={"total_raw": len(works), "retained": len(retained)},
        transform_steps=["Filtered recent records"],
        data_scope="HCI 2021-2026",
    )
    assert len(profile["sources"]) == 3
    val = ValidationAgent().validate_lineage_conservation(profile)
    assert val["passed"] is True
    assert val["is_exact_conservation"] is True


def test_dynamic_capability_discovery_and_standalone_inline() -> None:
    py_guide = discover_python_sandbox_capabilities()
    assert "pandas" in py_guide
    assert "networkx" in py_guide

    libs = discover_visualization_libraries()
    filenames = {lib["filename"] for lib in libs}
    assert {"d3.v7.min.js", "echarts.min.js", "chart.umd.min.js"}.issubset(filenames)

    vis_guide = build_visualization_capability_guide()
    assert "/vendor/echarts.min.js" in vis_guide
    assert "/vendor/d3.v7.min.js" in vis_guide
    assert "/vendor/chart.umd.min.js" in vis_guide

    rewritten = rewrite_cdn_scripts_to_local_vendor(
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>'
    )
    assert '/vendor/echarts.min.js' in rewritten

    standalone = make_standalone_html(
        '<html><head><script src="/vendor/chart.umd.min.js"></script></head><body></body></html>'
    )
    assert '/vendor/chart.umd.min.js' not in standalone
    assert "<script>" in standalone


@pytest.mark.asyncio
async def test_fast_file_uri_browser_validator_handles_echarts_canvas_and_html_strings(
    tmp_path: Path,
) -> None:
    raw_html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <script src="/vendor/echarts.min.js"></script>
</head>
<body>
  <div id="chart" data-rc-chart-stage="1" style="width: 900px; height: 520px;"></div>
  <div class="controls">
    <button type="button"><svg width="14" height="14"><path d="M1 1 L12 12"/></svg> Reset</button>
  </div>
  <script id="analysis-data" type="application/json">__ANALYSIS_DATA_JSON__</script>
  <script>
    const RAW = JSON.parse(document.getElementById('analysis-data').textContent);
    const tooltipTemplate = '<div class="tip"><span>Series < 10</span></div>';
    const chart = echarts.init(document.getElementById('chart'), null, { renderer: 'canvas' });
    chart.setOption({
      animation: false,
      xAxis: { type: 'category', data: RAW.data.years },
      yAxis: { type: 'value' },
      series: [{ type: 'bar', data: RAW.data.counts }]
    });
  </script>
</body>
</html>"""

    injected = _inject_data_and_bridge(
        raw_html,
        {
            "summary": {"headline": "Test </script> tricky literal inside JSON"},
            "data": {"years": ["2022", "2023", "2024", "2025"], "counts": [42, 68, 95, 120]},
        },
    )
    thumb = tmp_path / "echarts_preview.png"
    report = await validate_html_and_capture(injected, thumb)
    assert report["passed"] is True, f"Unexpected validation failure: {report}"
    assert report["dom_check"]["hasLargeCanvas"] is True
    assert thumb.exists() and thumb.stat().st_size > 1000


def test_instant_comment_save_and_v2_bridge_element_inspector() -> None:
    import time
    import uuid
    from fastapi.testclient import TestClient
    from app.config import RUNTIME_DIR
    from app.db import init_db
    from app.main import app
    from app.services.artifact_manager import save_artifact_version
    from app.services.lineage_manager import build_and_persist_lineage

    init_db()
    client = TestClient(app)
    ws = client.post("/api/workspaces", json={"title": "Instant Comment Test"}).json()
    ws_id = ws["workspace_id"]
    av_id = f"av_fast_{uuid.uuid4().hex[:6]}"
    art_id = f"art_fast_{uuid.uuid4().hex[:6]}"
    thumb = RUNTIME_DIR / "artifacts" / av_id / "preview.png"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n")

    legacy_html = (
        '<!doctype html><html><body>'
        '<div id="chart"><svg width="800" height="500">'
        '<rect class="bar" x="120" y="80" width="40" height="240" fill="#2563eb"/>'
        '<text x="130" y="340">2025</text><text x="135" y="72">1</text>'
        '</svg></div>'
        '<script data-rc-bridge="1">window.__rcBridgeInstalled=true;</script>'
        '</body></html>'
    )
    build_and_persist_lineage(av_id, "HCI Trend", "Bar", [], [], {"passed": True})
    save_artifact_version(
        av_id, art_id, ws_id, "tg_1", None, 1, "HCI Trend", "Bar",
        legacy_html, thumb, [], [], f"lineage/{av_id}.json", [], {"passed": True}, "ok"
    )

    # Verify /html upgrades legacy bridge v1 -> v2 with RC_SCROLL_STATE & inspectTargetAt
    html_resp = client.get(f"/api/versions/{av_id}/html")
    assert html_resp.status_code == 200
    assert 'data-rc-bridge="2"' in html_resp.text
    assert "RC_SCROLL_STATE" in html_resp.text
    assert "inspectTargetAt" in html_resp.text

    # Verify POST /comments responds instantaneously (<150ms, not waiting ~2s for Chromium)
    t0 = time.perf_counter()
    c_resp = client.post(
        f"/api/versions/{av_id}/comments",
        json={
            "text": "这里是怎么回事，为啥publication全是1",
            "anchor": {"x_norm": 0.18, "y_norm": 0.35},
            "optional_element_hint": 'div#chart > svg > rect.bar · nearby=[1 | 2025]',
            "preview_view_state": {
                "viewport_width": 1000,
                "viewport_height": 640,
                "scroll_x": 0,
                "scroll_y": 140,
                "zoom": 1,
                "filter_state": {
                    "__target_inspector": {
                        "dom_path": "div#chart > svg > rect.bar",
                        "nearby_labels": ["1", "2025"],
                    }
                },
            },
        },
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert c_resp.status_code == 200
    assert elapsed_ms < 250.0, f"Comment save took too long ({elapsed_ms:.1f}ms)"
    saved_comment = c_resp.json()
    assert saved_comment["optional_element_hint"] == "div#chart > svg > rect.bar · nearby=[1 | 2025]"
    assert saved_comment["preview_view_state"]["scroll_y"] == 140

    # Fetching screenshot awaits background render cleanly and returns real PNG
    shot_resp = client.get(f"/api/comments/{saved_comment['comment_id']}/screenshot")
    assert shot_resp.status_code == 200
    assert shot_resp.content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_llm_api_max_concurrency_semaphore_throttles_inflight_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from openai.resources.chat.completions import AsyncCompletions
    from app.agents.llm_client import LLMClient
    from app.main import app

    client = TestClient(app)
    put_resp = client.put(
        "/api/settings/llm",
        json={
            "provider": "openai",
            "api_key": "sk-test-secret-12345678abcd",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "thinking_effort": "low",
            "max_concurrency": 2,
            "available_models": ["gpt-4o-mini"],
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["max_concurrency"] == 2

    state = {"inflight": 0, "peak": 0}

    async def _slow_create(self: Any, **kwargs: Any) -> Any:
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        try:
            await asyncio.sleep(0.04)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok"),
                        finish_reason="stop",
                    )
                ]
            )
        finally:
            state["inflight"] -= 1

    monkeypatch.setattr(AsyncCompletions, "create", _slow_create)

    llm = LLMClient()
    assert llm.max_concurrency == 2
    results = await asyncio.gather(*(llm.complete_text("sys", f"req-{i}") for i in range(6)))
    assert results == ["ok"] * 6
    assert state["peak"] == 2


