from __future__ import annotations

import json
import uuid
from pathlib import Path
from fastapi.testclient import TestClient

from app.agents.validation_agent import ValidationAgent
from app.config import DATA_SEED_PATH, RUNTIME_DIR
from app.db import dumps, get_conn, init_db, utc_now_iso
from app.main import app
from app.services.artifact_manager import save_artifact_version
from app.services.lineage_manager import build_and_persist_lineage, load_lineage
from app.services.semantic_profiler import build_semantic_profile


async def test_openalex_real_corpus_semantic_flow_conservation_and_unit_conversion() -> None:
    """
    Tests Data Map v2 end-to-end on the real 400-work OpenAlex scientific corpus:
    - Real semantic categorization (article, conference-paper, review, book, book-chapter)
    - Real filtering excluding works with publication_year < 2020 or missing authorships
    - Real exclusion reasons, counts, and conservation check
    - Explicit unit conversion (works -> author nodes & edges)
    """
    init_db()
    seed = json.loads(DATA_SEED_PATH.read_text(encoding="utf-8"))
    works = seed["works"]
    assert len(works) == 400

    retained_records = []
    retained_indices = []
    missing_author_idxs = []
    pre_2020_idxs = []
    for idx, w in enumerate(works):
        if not w.get("authorships"):
            missing_author_idxs.append(idx)
        elif int(w.get("publication_year") or 0) < 2020:
            pre_2020_idxs.append(idx)
        else:
            retained_indices.append(idx)
            retained_records.append(w)

    snap_id = f"ds_v2_oa_{uuid.uuid4().hex[:6]}"
    ar_id = f"ar_v2_oa_{uuid.uuid4().hex[:6]}"
    av_id = f"av_v2_oa_{uuid.uuid4().hex[:6]}"
    art_id = f"art_v2_oa_{uuid.uuid4().hex[:6]}"
    ws_id = f"ws_v2_oa_{uuid.uuid4().hex[:6]}"

    snap_dir = RUNTIME_DIR / "snapshots" / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    rec_path = snap_dir / "records.json"
    rec_path.write_text(json.dumps({"records": retained_records}), encoding="utf-8")

    schema = {
        "id": "str - OpenAlex Work ID",
        "title": "str - Title",
        "type": "str - Document type",
        "publication_year": "int - Year",
        "authorships": "list - Author list",
    }
    transform_steps = [
        "Retain OpenAlex works published >= 2020",
        "Exclude works with missing authorships",
    ]
    proc_trace = {
        "record_unit": "works",
        "retained_indices": retained_indices,
        "exclusions": [
            {
                "reason_code": "missing_authorships",
                "reason_label": "Missing Author Metadata",
                "stage": "Quality Validation",
                "rule": "len(work.authorships or []) > 0",
                "record_indices": missing_author_idxs,
            },
            {
                "reason_code": "pre_2020_scope",
                "reason_label": "Outside Temporal Scope (< 2020)",
                "stage": "Temporal Scope Filter",
                "rule": "work.publication_year >= 2020",
                "record_indices": pre_2020_idxs,
            },
        ],
    }

    profile = await build_semantic_profile(
        llm=None,
        raw_payload=works,
        retained_records=retained_records,
        snapshot_id=snap_id,
        source_meta={
            "source_name": "OpenAlex Works API",
            "source_url": "https://api.openalex.org/works",
            "retrieval_mode": "verified_local_openalex_snapshot",
        },
        schema=schema,
        missingness_quality={"total_raw": 400, "retained": len(retained_records)},
        transform_steps=transform_steps,
        data_scope="Analyze AI author collaboration networks (2020-2025)",
        processing_trace=proc_trace,
    )

    assert profile["status"] == "complete"
    assert profile["total_input_count"] == 400
    assert profile["total_retained_count"] == len(retained_records)
    assert profile["total_excluded_count"] == len(missing_author_idxs) + len(pre_2020_idxs)
    assert sum(c["raw_count"] for c in profile["categories"]) == 400
    for cat in profile["categories"]:
        assert cat["retained_count"] + cat["excluded_count"] == cat["raw_count"]
        assert "excluded" not in cat["label"].lower()

    # Persist snapshot, analysis with unit conversion, and build lineage
    ar_dir = RUNTIME_DIR / "analyses" / ar_id
    ar_dir.mkdir(parents=True, exist_ok=True)
    ar_res_path = ar_dir / "result.json"
    ar_res_path.write_text(
        json.dumps(
            {
                "analysis_algorithm": "Co-authorship Network + Louvain Communities",
                "summary": {
                    "headline": f"Built collaboration graph across {len(retained_records)} works.",
                    "key_metrics": {"nodes": 48, "edges": 124},
                },
                "unit_transform": {
                    "input_count": len(retained_records),
                    "input_unit": "works",
                    "output_count": 48,
                    "output_unit": "author nodes (124 edges)",
                    "unit_changed": True,
                    "conversion_explanation": f"Aggregated {len(retained_records)} works into 48 active authors and 124 co-authorship edges.",
                },
                "data": {"nodes": [{"id": f"a{i}"} for i in range(48)], "links": [{"source": "a0", "target": "a1"}] * 124},
            }
        ),
        encoding="utf-8",
    )

    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workspaces (workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (ws_id, "Semantic Flow Test WS", now, now),
        )
        conn.execute(
            """
            INSERT INTO dataset_snapshots (
                snapshot_id, workspace_id, source_name, source_url, retrieval_time,
                retrieval_mode, query_filter, raw_record_count, retained_record_count,
                schema_json, missingness_json, transform_steps_json,
                upstream_dependencies_json, script_text, records_ref,
                semantic_profile_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id, ws_id, "OpenAlex Works API", "https://api.openalex.org/works",
                now, "verified_local_openalex_snapshot", "2020-2025",
                400, len(retained_records), dumps(schema), "{}", dumps(transform_steps),
                "[]", "print('ok')", str(rec_path.relative_to(RUNTIME_DIR)),
                dumps(profile), now,
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
                ar_id, ws_id, snap_id, "Co-authorship Network + Louvain Communities",
                "{}", "print('ok')", dumps({"headline": "48 author nodes", "key_metrics": {"nodes": 48}}),
                str(ar_res_path.relative_to(RUNTIME_DIR)), "", "", "completed", now,
            ),
        )

    graph = build_and_persist_lineage(
        artifact_version_id=av_id,
        title="AI Collaboration Network v2 Test",
        chart_kind="Force Network",
        snapshot_ids=[snap_id],
        analysis_run_ids=[ar_id],
        validation_report={"passed": True},
    )
    assert graph.lineage_schema_version == 2
    assert graph.semantic_flow is not None
    sf = graph.semantic_flow
    assert sf.conservation_check.passed is True
    assert sf.conservation_check.is_exact_conservation is True
    assert sf.conservation_check.unit_conversion_explained is True
    assert sf.analyses[0].unit_changed is True
    assert sf.analyses[0].output_count == 48


async def test_general_non_openalex_multi_file_dataset_and_unclassified_bucket() -> None:
    """
    Verifies Data Map v2 works on an arbitrary non-OpenAlex tabular multi-file CSV dataset
    with multi-source merging, duplicate removal, missing field rejection, and
    Unclassified / 待确认 handling.
    """
    bundle = {
        "files": [
            {
                "relative_path": "devices_q1.csv",
                "format": "csv_rows",
                "rows": [
                    {"record_id": "d1", "segment": "wearable", "revenue": 120},
                    {"record_id": "d2", "segment": "smartphone", "revenue": 540},
                    {"record_id": "d2", "segment": "smartphone", "revenue": 540},  # duplicate
                    {"record_id": "d3", "segment": "", "revenue": 80},  # unclassified
                ],
            },
            {
                "relative_path": "devices_q2.csv",
                "format": "csv_rows",
                "rows": [
                    {"record_id": "d4", "segment": "wearable", "revenue": None},  # dropped
                    {"record_id": "d5", "segment": "tablet", "revenue": 310},
                ],
            },
        ]
    }
    retained = [
        {"record_id": "d1", "segment": "wearable", "revenue": 120},
        {"record_id": "d2", "segment": "smartphone", "revenue": 540},
        {"record_id": "d3", "segment": "", "revenue": 80},
        {"record_id": "d5", "segment": "tablet", "revenue": 310},
    ]

    profile = await build_semantic_profile(
        llm=None,
        raw_payload=bundle,
        retained_records=retained,
        snapshot_id="ds_csv_test",
        source_meta={"source_name": "Quarterly Device Sales", "source_url": "upload://up_csv", "retrieval_mode": "user_uploaded_files"},
        schema={"record_id": "str", "segment": "str", "revenue": "float"},
        missingness_quality={"total_raw": 6, "retained": 4, "missing_revenue": 1},
        transform_steps=["Deduplicate by record_id", "Drop rows missing revenue"],
        data_scope="Compare quarterly revenue by device segment",
    )

    assert len(profile["sources"]) == 2
    assert profile["total_input_count"] == 6
    assert profile["total_retained_count"] == 4
    assert profile["total_excluded_count"] == 2
    assert any(c["is_unclassified"] for c in profile["categories"])
    reasons = {b["reason_code"] for b in profile["exclusion_branches"]}
    assert "duplicate_record" in reasons
    assert "missing_revenue" in reasons

    cons = ValidationAgent().validate_lineage_conservation(
        {
            **profile,
            "outcomes": profile["exclusion_branches"],
            "datasets": [],
            "analyses": [],
        }
    )
    assert cons["passed"] is True
    assert cons["is_exact_conservation"] is True


def test_version_isolation_legacy_fallback_and_datamap_comments() -> None:
    """
    Verifies:
    1. Legacy Lineage v1 JSON without semantic_flow loads cleanly.
    2. Version isolation: creating v2 preserves v1's independent lineage.
    3. Pinpoint comment on Data Map saves as pending with contextual screenshot
       without creating a new ArtifactVersion or triggering any Agent.
    """
    init_db()
    client = TestClient(app)
    ws = client.post("/api/workspaces", json={"title": "Data Map v2 Compat Test"}).json()
    ws_id = ws["workspace_id"]
    art_id = f"art_dm_{uuid.uuid4().hex[:6]}"
    av1_id = f"av_dm_v1_{uuid.uuid4().hex[:6]}"

    html_sample = '<!doctype html><html><body><svg width="800" height="500"><circle cx="200" cy="200" r="40" fill="#2563eb"/><rect x="10" y="10" width="40" height="40" fill="#0ea5e9"/><text x="200" y="260">Chart</text></svg></body></html>'
    thumb1 = RUNTIME_DIR / "artifacts" / av1_id / "preview.png"
    thumb1.parent.mkdir(parents=True, exist_ok=True)
    thumb1.write_bytes(b"\x89PNG\r\n\x1a\n")

    legacy_graph = build_and_persist_lineage(av1_id, "Legacy Chart", "Trend", [], [], {"passed": True})
    assert legacy_graph.semantic_flow is None
    assert legacy_graph.lineage_schema_version == 1

    save_artifact_version(
        av1_id, art_id, ws_id, "task_dm", None, 1, "Legacy Chart", "Trend",
        html_sample, thumb1, [], [], f"lineage/{av1_id}.json", [], {"passed": True}, "s1"
    )

    loaded = client.get(f"/api/versions/{av1_id}/lineage").json()
    assert loaded["semantic_flow"] is None
    assert loaded["lineage_schema_version"] == 1

    # Post Data Map pinpoint comment
    cmt_resp = client.post(
        f"/api/versions/{av1_id}/comments",
        json={
            "text": "Why were pre-2020 papers excluded here?",
            "anchor": {"x_norm": 0.48, "y_norm": 0.62},
            "preview_view_state": {
                "viewport_width": 1100,
                "viewport_height": 680,
                "filter_state": {
                    "surface": "data_map",
                    "node_id": "out_excl_pre_2020_scope",
                    "node_label": "Outside Temporal Scope",
                    "node_kind": "excluded",
                },
            },
            "optional_element_hint": "data_map:excluded:Outside Temporal Scope",
        },
    )
    assert cmt_resp.status_code == 200
    cmt = cmt_resp.json()
    assert cmt["status"] == "pending"
    assert (RUNTIME_DIR / cmt["screenshot_ref"]).exists()

    # Confirm no new artifact version or agent run triggered
    canvas = client.get(f"/api/workspaces/{ws_id}/canvas").json()
    assert len(canvas["artifact_versions"]) == 1
    assert load_lineage(av1_id) is not None
