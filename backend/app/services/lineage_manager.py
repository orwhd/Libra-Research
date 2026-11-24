from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..models import LineageEdge, LineageGraph, LineageNode


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
    Deterministically builds the 6-stage provenance DAG:
      Source -> Collection -> Transformation -> Dataset Snapshot -> Analysis Run -> Visualization Artifact
    strictly from recorded tool/database execution facts (zero LLM fabrication).
    """
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

    graph = LineageGraph(
        artifact_version_id=artifact_version_id,
        generated_at=utc_now_iso(),
        nodes=nodes,
        edges=edges,
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
