from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from ..config import (
    CANVAS_MARGIN_X,
    CANVAS_MARGIN_Y,
    CARD_GAP_X,
    CARD_GAP_Y,
    CARD_HEIGHT,
    CARD_WIDTH,
    GROUP_GAP_Y,
    RUNTIME_DIR,
    VENDOR_DIR,
)
from ..db import dumps, get_conn, loads, utc_now_iso
from ..models import ArtifactVersion, CanvasPosition


def make_standalone_html(html_text: str) -> str:
    """Inline any local `/vendor/*.js` script tags so downloaded HTML opens standalone offline."""
    if not VENDOR_DIR.exists():
        return html_text
    for js_file in sorted(VENDOR_DIR.glob("*.js")):
        src_path = f"/vendor/{js_file.name}"
        if src_path not in html_text:
            continue
        js_code = js_file.read_text(encoding="utf-8").replace("</script>", "<\\/script>")
        for ref in (
            f'<script src="{src_path}"></script>',
            f"<script src='{src_path}'></script>",
        ):
            if ref in html_text:
                html_text = html_text.replace(ref, f"<script>\n{js_code}\n</script>")
    return html_text


def _boxes_overlap(x1: float, y1: float, x2: float, y2: float) -> bool:
    return not (
        x1 + CARD_WIDTH + 16 <= x2
        or x2 + CARD_WIDTH + 16 <= x1
        or y1 + CARD_HEIGHT + 16 <= y2
        or y2 + CARD_HEIGHT + 16 <= y1
    )


def compute_canvas_position(
    workspace_id: str,
    task_group_id: str,
    parent_version_id: str | None = None,
    target_row: int | None = None,
) -> CanvasPosition:
    """
    Layout invariant (§3.2):
    - New artifacts from a TaskGroup stack VERTICALLY (`revision_column = 0`, `row = 0, 1, 2...`).
    - Regenerated revisions are placed IMMEDIATELY TO THE RIGHT of their parent (`revision_column = parent.col + 1`).
    - Multiple sibling revisions branching from the same parent shift downward cleanly with zero overlap.
    - Different TaskGroups are separated vertically by `GROUP_GAP_Y`.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT artifact_version_id, task_group_id, parent_version_id, canvas_json "
            "FROM artifact_versions WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()

    existing: list[tuple[str, str, str | None, CanvasPosition]] = []
    for r in rows:
        pos = CanvasPosition.model_validate(loads(r["canvas_json"], {}))
        existing.append((r["artifact_version_id"], r["task_group_id"], r["parent_version_id"], pos))

    if parent_version_id:
        parent_match = next((item for item in existing if item[0] == parent_version_id), None)
        if parent_match:
            _, p_group, _, p_pos = parent_match
            target_col = p_pos.revision_column + 1
            target_x = p_pos.x + CARD_WIDTH + CARD_GAP_X
            target_y = p_pos.y
            computed_row = p_pos.row

            # Prevent overlap if another branch already occupies right-adjacent slot
            while any(_boxes_overlap(target_x, target_y, item[3].x, item[3].y) for item in existing):
                target_y += CARD_HEIGHT + CARD_GAP_Y
                computed_row += 1

            return CanvasPosition(
                group_id=p_group,
                row=computed_row,
                revision_column=target_col,
                x=target_x,
                y=target_y,
                width=CARD_WIDTH,
                height=CARD_HEIGHT,
            )

    # Initial task output: determine group base Y from earlier task groups (never counting current group)
    other_groups = [item for item in existing if item[1] != task_group_id]
    if other_groups:
        group_base_y = max(item[3].y + CARD_HEIGHT for item in other_groups) + GROUP_GAP_Y
    else:
        group_base_y = float(CANVAS_MARGIN_Y)

    if target_row is not None:
        return CanvasPosition(
            group_id=task_group_id,
            row=target_row,
            revision_column=0,
            x=float(CANVAS_MARGIN_X),
            y=float(group_base_y + target_row * (CARD_HEIGHT + CARD_GAP_Y)),
            width=CARD_WIDTH,
            height=CARD_HEIGHT,
        )

    group_items = [item for item in existing if item[1] == task_group_id and item[3].revision_column == 0]
    if group_items:
        max_row = max(item[3].row for item in group_items)
        max_y = max(item[3].y for item in group_items)
        return CanvasPosition(
            group_id=task_group_id,
            row=max_row + 1,
            revision_column=0,
            x=float(CANVAS_MARGIN_X),
            y=float(max_y + CARD_HEIGHT + CARD_GAP_Y),
            width=CARD_WIDTH,
            height=CARD_HEIGHT,
        )

    return CanvasPosition(
        group_id=task_group_id,
        row=0,
        revision_column=0,
        x=float(CANVAS_MARGIN_X),
        y=float(group_base_y),
        width=CARD_WIDTH,
        height=CARD_HEIGHT,
    )


def save_artifact_version(
    artifact_version_id: str,
    artifact_id: str,
    workspace_id: str,
    task_group_id: str,
    parent_version_id: str | None,
    version_number: int,
    title: str,
    chart_kind: str,
    html_text: str,
    thumbnail_path: Path,
    dataset_snapshot_ids: list[str],
    analysis_run_ids: list[str],
    lineage_ref: str,
    addressed_comment_ids: list[str],
    validation_report: dict[str, Any],
    summary: str,
    target_row: int | None = None,
) -> ArtifactVersion:
    art_dir = RUNTIME_DIR / "artifacts" / artifact_version_id
    art_dir.mkdir(parents=True, exist_ok=True)
    html_file = art_dir / "index.html"
    html_file.write_text(html_text, encoding="utf-8")

    canvas_pos = compute_canvas_position(
        workspace_id, task_group_id, parent_version_id, target_row=target_row
    )
    now = utc_now_iso()

    av = ArtifactVersion(
        artifact_version_id=artifact_version_id,
        artifact_id=artifact_id,
        workspace_id=workspace_id,
        task_group_id=task_group_id,
        parent_version_id=parent_version_id,
        version_number=version_number,
        title=title,
        chart_kind=chart_kind,
        kind="interactive_html",
        status="validated",
        html_ref=str(html_file.relative_to(RUNTIME_DIR)),
        thumbnail_ref=str(thumbnail_path.relative_to(RUNTIME_DIR)),
        dataset_snapshot_ids=dataset_snapshot_ids,
        analysis_run_ids=analysis_run_ids,
        lineage_ref=lineage_ref,
        pending_comment_count=0,
        addressed_comment_count=0,
        addressed_comment_ids=addressed_comment_ids,
        canvas=canvas_pos,
        validation_report=validation_report,
        summary=summary,
        created_at=now,
    )

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO artifacts (artifact_id, workspace_id, title, chart_kind, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (artifact_id, workspace_id, title, chart_kind, now),
        )
        conn.execute(
            """
            INSERT INTO artifact_versions (
                artifact_version_id, artifact_id, workspace_id, task_group_id,
                parent_version_id, version_number, title, chart_kind, kind, status,
                html_ref, thumbnail_ref, dataset_snapshot_ids_json, analysis_run_ids_json,
                lineage_ref, addressed_comment_ids_json, canvas_json, validation_report_json,
                summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                av.artifact_version_id,
                av.artifact_id,
                av.workspace_id,
                av.task_group_id,
                av.parent_version_id,
                av.version_number,
                av.title,
                av.chart_kind,
                av.kind,
                av.status,
                av.html_ref,
                av.thumbnail_ref,
                dumps(av.dataset_snapshot_ids),
                dumps(av.analysis_run_ids),
                av.lineage_ref,
                dumps(av.addressed_comment_ids),
                av.canvas.model_dump_json(),
                dumps(av.validation_report),
                av.summary,
                av.created_at,
            ),
        )

        if addressed_comment_ids:
            placeholders = ",".join("?" for _ in addressed_comment_ids)
            conn.execute(
                f"""
                UPDATE visual_comments
                SET status = 'addressed', resolved_in_version_id = ?
                WHERE comment_id IN ({placeholders})
                """,
                (av.artifact_version_id, *addressed_comment_ids),
            )

    return av


def row_to_artifact_version(row: Any, comment_counts: dict[str, tuple[int, int]] | None = None) -> ArtifactVersion:
    avid = row["artifact_version_id"]
    pending_cnt, addressed_cnt = (0, 0)
    if comment_counts and avid in comment_counts:
        pending_cnt, addressed_cnt = comment_counts[avid]
    return ArtifactVersion(
        artifact_version_id=avid,
        artifact_id=row["artifact_id"],
        workspace_id=row["workspace_id"],
        task_group_id=row["task_group_id"],
        parent_version_id=row["parent_version_id"],
        version_number=row["version_number"],
        title=row["title"],
        chart_kind=row["chart_kind"],
        kind=row["kind"],
        status=row["status"],
        html_ref=row["html_ref"],
        thumbnail_ref=row["thumbnail_ref"],
        dataset_snapshot_ids=loads(row["dataset_snapshot_ids_json"], []),
        analysis_run_ids=loads(row["analysis_run_ids_json"], []),
        lineage_ref=row["lineage_ref"],
        pending_comment_count=pending_cnt,
        addressed_comment_count=addressed_cnt,
        addressed_comment_ids=loads(row["addressed_comment_ids_json"], []),
        canvas=CanvasPosition.model_validate(loads(row["canvas_json"], {})),
        validation_report=loads(row["validation_report_json"], {}),
        summary=row["summary"] or "",
        created_at=row["created_at"],
    )


def get_artifact_version(artifact_version_id: str) -> ArtifactVersion | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM artifact_versions WHERE artifact_version_id = ?",
            (artifact_version_id,),
        ).fetchone()
        if not row:
            return None
        counts = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM visual_comments WHERE artifact_version_id = ? GROUP BY status",
            (artifact_version_id,),
        ).fetchall()
        p_cnt = sum(c["cnt"] for c in counts if c["status"] == "pending")
        a_cnt = sum(c["cnt"] for c in counts if c["status"] == "addressed")
    return row_to_artifact_version(row, {artifact_version_id: (p_cnt, a_cnt)})


def list_workspace_artifact_versions(workspace_id: str) -> list[ArtifactVersion]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM artifact_versions WHERE workspace_id = ? ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
        c_rows = conn.execute(
            "SELECT artifact_version_id, status, COUNT(*) as cnt "
            "FROM visual_comments WHERE workspace_id = ? GROUP BY artifact_version_id, status",
            (workspace_id,),
        ).fetchall()
    counts: dict[str, tuple[int, int]] = {}
    for cr in c_rows:
        avid = cr["artifact_version_id"]
        p, a = counts.get(avid, (0, 0))
        if cr["status"] == "pending":
            p += cr["cnt"]
        else:
            a += cr["cnt"]
        counts[avid] = (p, a)
    versions = [row_to_artifact_version(r, counts) for r in rows]
    group_order: dict[str, int] = {}
    for v in versions:
        if v.task_group_id not in group_order:
            group_order[v.task_group_id] = len(group_order)
    versions.sort(
        key=lambda v: (
            group_order[v.task_group_id],
            v.canvas.revision_column,
            v.canvas.row,
            v.created_at,
        )
    )
    return versions


def update_artifact_canvas_position(version_id: str, x: float, y: float) -> ArtifactVersion | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT canvas_json FROM artifact_versions WHERE artifact_version_id = ?",
            (version_id,),
        ).fetchone()
        if not row:
            return None
        pos = CanvasPosition.model_validate(loads(row["canvas_json"], {}))
        pos.x = float(x)
        pos.y = float(y)
        conn.execute(
            "UPDATE artifact_versions SET canvas_json = ? WHERE artifact_version_id = ?",
            (pos.model_dump_json(), version_id),
        )
    return get_artifact_version(version_id)


def delete_artifact_version(version_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT artifact_id, parent_version_id FROM artifact_versions WHERE artifact_version_id = ?",
            (version_id,),
        ).fetchone()
        if not row:
            return False
        art_id = row["artifact_id"]
        parent_vid = row["parent_version_id"]

        conn.execute(
            "UPDATE artifact_versions SET parent_version_id = ? WHERE parent_version_id = ?",
            (parent_vid, version_id),
        )
        conn.execute(
            "DELETE FROM visual_comments WHERE artifact_version_id = ?",
            (version_id,),
        )
        conn.execute(
            "DELETE FROM artifact_versions WHERE artifact_version_id = ?",
            (version_id,),
        )
        rem = conn.execute(
            "SELECT COUNT(*) AS cnt FROM artifact_versions WHERE artifact_id = ?",
            (art_id,),
        ).fetchone()
        if rem and rem["cnt"] == 0:
            conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (art_id,))

    shutil.rmtree(RUNTIME_DIR / "artifacts" / version_id, ignore_errors=True)
    (RUNTIME_DIR / "lineage" / f"{version_id}.json").unlink(missing_ok=True)
    return True


def build_provenance_zip_bytes(av: ArtifactVersion) -> bytes:
    buf = io.BytesIO()
    html_path = RUNTIME_DIR / av.html_ref
    html_text = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    standalone_html = make_standalone_html(html_text)
    lineage_path = RUNTIME_DIR / av.lineage_ref

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", standalone_html)
        zf.writestr("metadata.json", av.model_dump_json(indent=2))
        if lineage_path.exists():
            zf.writestr("lineage.json", lineage_path.read_text(encoding="utf-8"))
        with get_conn() as conn:
            for arid in av.analysis_run_ids:
                ar = conn.execute(
                    "SELECT * FROM analysis_runs WHERE analysis_run_id = ?", (arid,)
                ).fetchone()
                if ar:
                    zf.writestr(f"analysis/{arid}_script.py", ar["script_text"])
                    res_path = RUNTIME_DIR / ar["result_ref"]
                    if res_path.exists():
                        zf.writestr(f"analysis/{arid}_result.json", res_path.read_text(encoding="utf-8"))
            for sid in av.dataset_snapshot_ids:
                ds = conn.execute(
                    "SELECT * FROM dataset_snapshots WHERE snapshot_id = ?", (sid,)
                ).fetchone()
                if ds:
                    zf.writestr(f"dataset/{sid}_transform.py", ds["script_text"])
                    zf.writestr(
                        f"dataset/{sid}_provenance.json",
                        json.dumps(
                            {
                                "snapshot_id": sid,
                                "source_name": ds["source_name"],
                                "source_url": ds["source_url"],
                                "retrieval_time": ds["retrieval_time"],
                                "retrieval_mode": ds["retrieval_mode"],
                                "query_filter": ds["query_filter"],
                                "raw_record_count": ds["raw_record_count"],
                                "retained_record_count": ds["retained_record_count"],
                            },
                            indent=2,
                        ),
                    )
    return buf.getvalue()
