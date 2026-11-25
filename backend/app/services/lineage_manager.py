from __future__ import annotations

import json
from typing import Any

from ..agents.validation_agent import ValidationAgent
from ..config import RUNTIME_DIR
from ..db import loads, utc_now_iso
from ..models import LineageEdge, LineageGraph, LineageNode, SemanticDataFlow


def _inspect_analysis_unit_transform(
    ar: dict[str, Any],
    snapshot: dict[str, Any] | None,
    input_unit: str,
) -> dict[str, Any]:
    """
    Extracts or deterministically infers the Analysis unit conversion & output size
    directly from the persisted `result.json` produced in the Python sandbox.
    """
    in_count = int(snapshot.get("retained_record_count") or 0) if snapshot else 0
    result_ref = ar.get("result_ref") or ""
    result_data: dict[str, Any] = {}
    if result_ref:
        rp = RUNTIME_DIR / result_ref
        if rp.exists():
            try:
                loaded = json.loads(rp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    result_data = loaded
            except Exception:  # noqa: BLE001
                result_data = {}

    ut = result_data.get("unit_transform")
    if isinstance(ut, dict) and ut.get("output_count") is not None:
        out_count = int(ut.get("output_count") or 0)
        out_unit = str(ut.get("output_unit") or input_unit)
        in_u = str(ut.get("input_unit") or input_unit)
        in_c = int(ut.get("input_count") or in_count)
        unit_changed = bool(ut.get("unit_changed")) or (out_unit != in_u) or (out_count != in_c)
        expl = str(ut.get("conversion_explanation") or "").strip()
        if unit_changed and not expl:
            expl = f"Transformed {in_c} {in_u} into {out_count} {out_unit}."
        return {
            "input_count": in_c,
            "input_unit": in_u,
            "output_count": out_count,
            "output_unit": out_unit,
            "unit_changed": unit_changed,
            "conversion_explanation": expl,
        }

    data_val = result_data.get("data")
    algo = str(ar.get("analysis_algorithm") or "")

    if isinstance(data_val, dict):
        if isinstance(data_val.get("nodes"), list):
            n_cnt = len(data_val["nodes"])
            l_cnt = len(data_val.get("links") or data_val.get("edges") or [])
            return {
                "input_count": in_count,
                "input_unit": input_unit,
                "output_count": n_cnt,
                "output_unit": f"graph nodes ({l_cnt} links)",
                "unit_changed": True,
                "conversion_explanation": f"Aggregated {in_count} {input_unit} into {n_cnt} network entities and {l_cnt} relationship edges ({algo}).",
            }
        if isinstance(data_val.get("bundle_links"), list) or isinstance(data_val.get("hierarchy"), dict):
            b_cnt = len(data_val.get("bundle_links") or [])
            return {
                "input_count": in_count,
                "input_unit": input_unit,
                "output_count": max(b_cnt, 1),
                "output_unit": "hierarchical bundles",
                "unit_changed": True,
                "conversion_explanation": f"Grouped {in_count} {input_unit} into a hierarchical taxonomy with {b_cnt} bundled cross-links.",
            }
        if isinstance(data_val.get("cells"), list):
            c_cnt = len(data_val["cells"])
            return {
                "input_count": in_count,
                "input_unit": input_unit,
                "output_count": c_cnt,
                "output_unit": "matrix cells",
                "unit_changed": True,
                "conversion_explanation": f"Cross-tabulated {in_count} {input_unit} into {c_cnt} contingency matrix cells.",
            }
        if isinstance(data_val.get("series"), list):
            s_cnt = len(data_val["series"])
            return {
                "input_count": in_count,
                "input_unit": input_unit,
                "output_count": s_cnt,
                "output_unit": "aggregated bins",
                "unit_changed": True,
                "conversion_explanation": f"Aggregated {in_count} {input_unit} into {s_cnt} temporal/categorical summary buckets.",
            }
        for val in data_val.values():
            if isinstance(val, list):
                return {
                    "input_count": in_count,
                    "input_unit": input_unit,
                    "output_count": len(val),
                    "output_unit": "aggregated entries",
                    "unit_changed": len(val) != in_count,
                    "conversion_explanation": (
                        f"Aggregated {in_count} {input_unit} into {len(val)} structured entries ({algo})."
                        if len(val) != in_count
                        else f"Direct 1:1 analytical pass over {in_count} {input_unit}."
                    ),
                }

    if isinstance(data_val, list):
        out_cnt = len(data_val)
        changed = out_cnt != in_count
        return {
            "input_count": in_count,
            "input_unit": input_unit,
            "output_count": out_cnt,
            "output_unit": "aggregated groups" if changed else input_unit,
            "unit_changed": changed,
            "conversion_explanation": (
                f"Aggregated {in_count} {input_unit} into {out_cnt} analytical output rows ({algo})."
                if changed
                else f"Analyzed {in_count} {input_unit} in 1:1 correspondence ({algo})."
            ),
        }

    return {
        "input_count": in_count,
        "input_unit": input_unit,
        "output_count": in_count,
        "output_unit": input_unit,
        "unit_changed": False,
        "conversion_explanation": f"Analyzed {in_count} {input_unit} ({algo}).",
    }


def build_semantic_flow_payload(
    artifact_version_id: str,
    title: str,
    chart_kind: str,
    snapshots: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> SemanticDataFlow | None:
    """
    Assembles the 5-column Semantic Data Flow model:
      Data Sources -> Semantic Categories -> Processing Outcomes -> Processed Dataset -> Analysis
    strictly from persisted `semantic_profile_json`, snapshot records, and analysis outputs.
    Returns None when snapshots lack semantic profile data (historic artifacts),
    or `status='unavailable'` if tracking data is marked incomplete.
    """
    if not snapshots:
        return None

    primary_snap = snapshots[0]
    raw_prof = loads(primary_snap.get("semantic_profile_json"), {})
    if not isinstance(raw_prof, dict) or not raw_prof:
        return None

    if raw_prof.get("status") == "unavailable":
        return SemanticDataFlow.model_validate(
            {
                "status": "unavailable",
                "unavailable_reason": str(
                    raw_prof.get("unavailable_reason") or "追踪信息不可用"
                ),
                "record_unit": "records",
                "total_input_count": int(primary_snap.get("raw_record_count") or 0),
                "total_retained_count": int(primary_snap.get("retained_record_count") or 0),
                "total_excluded_count": max(
                    0,
                    int(primary_snap.get("raw_record_count") or 0)
                    - int(primary_snap.get("retained_record_count") or 0),
                ),
                "conservation_check": {
                    "passed": False,
                    "is_exact_conservation": False,
                    "unit_conversion_explained": True,
                    "input_count": int(primary_snap.get("raw_record_count") or 0),
                    "retained_count": int(primary_snap.get("retained_record_count") or 0),
                    "excluded_count": 0,
                    "delta": 0,
                    "notes": ["追踪信息不可用，已保留原有血缘节点。"],
                },
                "sources": [],
                "categories": [],
                "outcomes": [],
                "datasets": [],
                "analyses": [],
                "links": [],
            }
        )

    record_unit = str(raw_prof.get("record_unit") or "records")
    total_input = int(raw_prof.get("total_input_count") or 0)
    total_retained = int(raw_prof.get("total_retained_count") or 0)
    total_excluded = int(raw_prof.get("total_excluded_count") or 0)
    snapshot_output_count = int(
        raw_prof.get("total_snapshot_output_count")
        or primary_snap.get("retained_record_count")
        or total_retained
    )

    sources = list(raw_prof.get("sources") or [])
    categories = list(raw_prof.get("categories") or [])
    cat_by_id = {c["id"]: c for c in categories if isinstance(c, dict) and c.get("id")}

    links: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    # Col 0 -> Col 1: Data Sources -> Semantic Categories
    for flow in raw_prof.get("source_category_flows") or []:
        sid = flow.get("source_id")
        cid = flow.get("category_id")
        val = int(flow.get("value") or 0)
        if not sid or not cid or val <= 0:
            continue
        cat_obj = cat_by_id.get(cid, {})
        kind = "unclassified" if cat_obj.get("is_unclassified") else "retained"
        links.append(
            {
                "id": f"lnk_{sid}__{cid}",
                "source_id": sid,
                "target_id": cid,
                "source_col": 0,
                "target_col": 1,
                "value": val,
                "outcome_kind": kind,
                "category_id": cid,
                "label": f"{val:,} {record_unit}",
                "unit_changed": False,
                "conversion_note": "",
            }
        )

    # Col 3: Processed Dataset
    sid_str = primary_snap["snapshot_id"]
    ds_node_id = f"ds_{sid_str}"
    schema_obj = loads(primary_snap.get("schema_json"), {})
    transform_steps = [
        str(s) for s in loads(primary_snap.get("transform_steps_json"), [])
    ]
    datasets = [
        {
            "id": ds_node_id,
            "snapshot_id": sid_str,
            "label": f"Processed Snapshot ({sid_str})",
            "version_label": sid_str,
            "retained_count": snapshot_output_count,
            "record_unit": record_unit,
            "schema_field_count": len(schema_obj) if isinstance(schema_obj, dict) else 0,
            "transform_steps": transform_steps,
            "created_at": str(primary_snap.get("created_at") or ""),
        }
    ]

    # Col 2: Processing Outcomes (Retained + Excluded branches + optional Transformed)
    retained_cat_refs: list[dict[str, Any]] = []
    for c in categories:
        ret_cnt = int(c.get("retained_count") or 0)
        if ret_cnt > 0:
            retained_cat_refs.append(
                {
                    "category_id": c["id"],
                    "category_label": c["label"],
                    "count": ret_cnt,
                }
            )
            links.append(
                {
                    "id": f"lnk_{c['id']}__out_retained",
                    "source_id": c["id"],
                    "target_id": "out_retained",
                    "source_col": 1,
                    "target_col": 2,
                    "value": ret_cnt,
                    "outcome_kind": "unclassified" if c.get("is_unclassified") else "retained",
                    "category_id": c["id"],
                    "label": f"{ret_cnt:,} retained",
                    "unit_changed": False,
                    "conversion_note": "",
                }
            )

    if total_retained > 0 or retained_cat_refs:
        outcomes.append(
            {
                "id": "out_retained",
                "outcome_kind": "retained",
                "label": "Retained Clean Records",
                "count": total_retained,
                "stage": "Snapshot Materialization",
                "reason_code": "retained",
                "reason_label": "Passed all schema & scope filters",
                "filter_rule": "; ".join(transform_steps[:3]) or "Retained valid records",
                "affected_categories": retained_cat_refs,
            }
        )
        # Col 2 -> Col 3: Retained -> Processed Dataset
        unit_changed_ds = snapshot_output_count != total_retained
        links.append(
            {
                "id": f"lnk_out_retained__{ds_node_id}",
                "source_id": "out_retained",
                "target_id": ds_node_id,
                "source_col": 2,
                "target_col": 3,
                "value": snapshot_output_count,
                "outcome_kind": "transformed" if unit_changed_ds else "retained",
                "category_id": None,
                "label": f"{snapshot_output_count:,} {record_unit}",
                "unit_changed": unit_changed_ds,
                "conversion_note": (
                    f"Normalized {total_retained} input records into {snapshot_output_count} snapshot rows."
                    if unit_changed_ds
                    else ""
                ),
            }
        )

    # Excluded branches
    for ex_b in raw_prof.get("exclusion_branches") or []:
        ex_id = str(ex_b.get("id") or "")
        if not ex_id or int(ex_b.get("count") or 0) <= 0:
            continue
        outcomes.append(ex_b)
        for ref in ex_b.get("affected_categories") or []:
            cid = ref.get("category_id")
            cnt = int(ref.get("count") or 0)
            if not cid or cnt <= 0:
                continue
            links.append(
                {
                    "id": f"lnk_{cid}__{ex_id}",
                    "source_id": cid,
                    "target_id": ex_id,
                    "source_col": 1,
                    "target_col": 2,
                    "value": cnt,
                    "outcome_kind": "excluded",
                    "category_id": cid,
                    "label": f"{cnt:,} excluded ({ex_b.get('reason_label', '')})",
                    "unit_changed": False,
                    "conversion_note": "",
                }
            )

    # Col 4: Analysis Nodes & Col 3 -> Col 4 links
    flow_analyses: list[dict[str, Any]] = []
    for ar in analyses:
        arid = ar["analysis_run_id"]
        ar_node_id = f"an_{arid}"
        summary_obj = loads(ar.get("summary_json"), {})
        headline = str(summary_obj.get("headline") or "") if isinstance(summary_obj, dict) else ""
        key_metrics = summary_obj.get("key_metrics", {}) if isinstance(summary_obj, dict) and isinstance(summary_obj.get("key_metrics"), dict) else {}

        ut = _inspect_analysis_unit_transform(ar, primary_snap, record_unit)
        flow_analyses.append(
            {
                "id": ar_node_id,
                "analysis_run_id": arid,
                "label": str(ar.get("analysis_algorithm") or title),
                "algorithm": str(ar.get("analysis_algorithm") or chart_kind),
                "input_count": ut["input_count"],
                "input_unit": ut["input_unit"],
                "output_count": ut["output_count"],
                "output_unit": ut["output_unit"],
                "unit_changed": ut["unit_changed"],
                "conversion_explanation": ut["conversion_explanation"],
                "summary_headline": headline,
                "key_metrics": key_metrics,
                "artifact_version_id": artifact_version_id,
                "artifact_title": title,
                "chart_kind": chart_kind,
            }
        )
        links.append(
            {
                "id": f"lnk_{ds_node_id}__{ar_node_id}",
                "source_id": ds_node_id,
                "target_id": ar_node_id,
                "source_col": 3,
                "target_col": 4,
                "value": ut["input_count"] or snapshot_output_count,
                "outcome_kind": "transformed" if ut["unit_changed"] else "retained",
                "category_id": None,
                "label": (
                    f"{ut['input_count']:,} {ut['input_unit']} → {ut['output_count']:,} {ut['output_unit']}"
                    if ut["unit_changed"]
                    else f"{ut['input_count']:,} {ut['input_unit']}"
                ),
                "unit_changed": ut["unit_changed"],
                "conversion_note": ut["conversion_explanation"],
            }
        )

    flow_dict: dict[str, Any] = {
        "status": "complete",
        "unavailable_reason": None,
        "record_unit": record_unit,
        "total_input_count": total_input,
        "total_retained_count": total_retained,
        "total_excluded_count": total_excluded,
        "sources": sources,
        "categories": categories,
        "outcomes": outcomes,
        "datasets": datasets,
        "analyses": flow_analyses,
        "links": links,
    }

    conservation = ValidationAgent().validate_lineage_conservation(flow_dict)
    flow_dict["conservation_check"] = conservation
    return SemanticDataFlow.model_validate(flow_dict)


def build_and_persist_lineage(
    artifact_version_id: str,
    title: str,
    chart_kind: str,
    snapshot_ids: list[str],
    analysis_run_ids: list[str],
    validation_report: dict[str, Any],
    parent_version_id: str | None = None,
) -> LineageGraph:
    """
    Deterministically builds the 6-stage provenance DAG PLUS the Data Map v2
    Semantic Data Flow strictly from recorded tool/database execution facts
    (zero LLM fabrication).
    """
    from ..db import get_conn

    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    seen_node_ids: set[str] = set()

    def add_node(node: LineageNode) -> None:
        if node.node_id not in seen_node_ids:
            seen_node_ids.add(node.node_id)
            nodes.append(node)

    def add_edge(src: str, tgt: str, relation: str) -> None:
        eid = f"e_{src}__{tgt}"
        if not any(e.edge_id == eid for e in edges):
            edges.append(LineageEdge(edge_id=eid, source=src, target=tgt, relation=relation))

    snapshots: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []

    with get_conn() as conn:
        for sid in snapshot_ids:
            row = conn.execute(
                "SELECT * FROM dataset_snapshots WHERE snapshot_id = ?", (sid,)
            ).fetchone()
            if row:
                snapshots.append(dict(row))

        for arid in analysis_run_ids:
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE analysis_run_id = ?", (arid,)
            ).fetchone()
            if row:
                analyses.append(dict(row))

    if not snapshots:
        missing_id = f"missing_snapshot_{artifact_version_id}"
        add_node(
            LineageNode(
                node_id=missing_id,
                stage="dataset_snapshot",
                label="Missing Dataset Snapshot",
                subtitle="Source snapshot record not found",
                status="missing",
                metadata={"error": "No snapshot records linked to this artifact version."},
            )
        )

    for snap in snapshots:
        sid = snap["snapshot_id"]
        src_node_id = f"src_{sid}"
        col_node_id = f"col_{sid}"
        trn_node_id = f"trn_{sid}"
        ds_node_id = f"dsnode_{sid}"

        add_node(
            LineageNode(
                node_id=src_node_id,
                stage="source",
                label=snap["source_name"],
                subtitle=snap["source_url"],
                status="verified",
                metadata={
                    "source_name": snap["source_name"],
                    "source_url": snap["source_url"],
                    "upstream_dependencies": loads(snap.get("upstream_dependencies_json"), []),
                },
            )
        )
        add_node(
            LineageNode(
                node_id=col_node_id,
                stage="collection",
                label="Data Retrieval",
                subtitle=f"{snap['retrieval_mode']} ({snap['raw_record_count']} raw works)",
                status="verified",
                metadata={
                    "retrieval_mode": snap["retrieval_mode"],
                    "retrieval_time": snap["retrieval_time"],
                    "query_filter": snap["query_filter"],
                    "raw_record_count": snap["raw_record_count"],
                },
            )
        )
        transform_steps = loads(snap.get("transform_steps_json"), [])
        add_node(
            LineageNode(
                node_id=trn_node_id,
                stage="transformation",
                label="Filtering & Cleaning",
                subtitle=f"{len(transform_steps)} step(s) -> {snap['retained_record_count']} retained",
                status="verified",
                metadata={
                    "transform_steps": transform_steps,
                    "missingness_quality": loads(snap.get("missingness_json"), {}),
                    "script_text": snap.get("script_text", ""),
                },
            )
        )
        add_node(
            LineageNode(
                node_id=ds_node_id,
                stage="dataset_snapshot",
                label=f"Snapshot {sid}",
                subtitle=f"{snap['retained_record_count']} records",
                status="verified",
                metadata={
                    "snapshot_id": sid,
                    "retained_record_count": snap["retained_record_count"],
                    "schema": loads(snap.get("schema_json"), {}),
                    "records_ref": snap.get("records_ref"),
                    "created_at": snap["created_at"],
                },
            )
        )
        add_edge(src_node_id, col_node_id, "queried_from")
        add_edge(col_node_id, trn_node_id, "cleaned_by")
        add_edge(trn_node_id, ds_node_id, "materialized_as")

    artifact_node_id = f"vis_{artifact_version_id}"
    add_node(
        LineageNode(
            node_id=artifact_node_id,
            stage="visualization_artifact",
            label=title,
            subtitle=f"{artifact_version_id} · {chart_kind}",
            status="verified" if validation_report.get("passed") else "failed",
            metadata={
                "artifact_version_id": artifact_version_id,
                "parent_version_id": parent_version_id,
                "chart_kind": chart_kind,
                "validation_report": validation_report,
            },
        )
    )

    for ar in analyses:
        arid = ar["analysis_run_id"]
        ar_node_id = f"ar_{arid}"
        add_node(
            LineageNode(
                node_id=ar_node_id,
                stage="analysis_run",
                label=ar["analysis_algorithm"],
                subtitle=f"{arid} ({ar['status']})",
                status="verified" if ar["status"] == "completed" else "failed",
                metadata={
                    "analysis_run_id": arid,
                    "dataset_snapshot_id": ar["dataset_snapshot_id"],
                    "analysis_algorithm": ar["analysis_algorithm"],
                    "params": loads(ar.get("params_json"), {}),
                    "summary": loads(ar.get("summary_json"), {}),
                    "script_text": ar.get("script_text", ""),
                    "result_ref": ar.get("result_ref"),
                    "stdout": ar.get("stdout", ""),
                    "created_at": ar["created_at"],
                },
            )
        )
        ds_target = f"dsnode_{ar['dataset_snapshot_id']}"
        if ds_target in seen_node_ids:
            add_edge(ds_target, ar_node_id, "analyzed_by")
        add_edge(ar_node_id, artifact_node_id, "rendered_into")

    semantic_flow = build_semantic_flow_payload(
        artifact_version_id=artifact_version_id,
        title=title,
        chart_kind=chart_kind,
        snapshots=snapshots,
        analyses=analyses,
    )

    graph = LineageGraph(
        artifact_version_id=artifact_version_id,
        generated_at=utc_now_iso(),
        nodes=nodes,
        edges=edges,
        lineage_schema_version=2 if semantic_flow is not None else 1,
        semantic_flow=semantic_flow,
    )

    lineage_path = RUNTIME_DIR / "lineage" / f"{artifact_version_id}.json"
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    return graph


def load_lineage(artifact_version_id: str) -> LineageGraph | None:
    lineage_path = RUNTIME_DIR / "lineage" / f"{artifact_version_id}.json"
    if not lineage_path.exists():
        return None
    data = json.loads(lineage_path.read_text(encoding="utf-8"))
    return LineageGraph.model_validate(data)
