from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ..config import MAX_RETRIES, RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..models import (
    ArtifactVersion,
    AttachedCommentSummary,
    ChatMessage,
    CreateTaskRequest,
    FollowUpSuggestion,
    RunEvent,
    RunStage,
    RunStatus,
    TaskRun,
)
from ..services.artifact_manager import (
    get_artifact_version,
    list_workspace_artifact_versions,
    save_artifact_version,
)
from ..services.lineage_manager import build_and_persist_lineage
from .analysis_agent import AnalysisAgent
from .data_agent import DataAgent
from .interaction_agent import InteractionAgent
from .llm_client import LLMClient
from .validation_agent import ValidationAgent
from .visualization_agent import VisualizationAgent


class RunEventBus:
    """In-memory pub/sub broadcasting live SSE RunEvents per run_id."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[RunEvent | None]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent | None]:
        q: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[RunEvent | None]) -> None:
        lst = self._queues.get(run_id, [])
        if q in lst:
            lst.remove(q)

    def publish(self, event: RunEvent) -> None:
        for q in list(self._queues.get(event.run_id, [])):
            q.put_nowait(event)

    def close(self, run_id: str) -> None:
        for q in list(self._queues.get(run_id, [])):
            q.put_nowait(None)


EVENT_BUS = RunEventBus()


def _emit_event(
    run_id: str,
    workspace_id: str,
    agent_name: str,
    stage: RunStage,
    status: RunStatus,
    concise_summary: str,
    details: dict[str, Any] | None = None,
    artifact_refs: list[str] | None = None,
    error: str | None = None,
    retry_count: int | None = None,
) -> RunEvent:
    now = utc_now_iso()
    ev = RunEvent(
        event_id=f"ev_{uuid.uuid4().hex[:10]}",
        run_id=run_id,
        workspace_id=workspace_id,
        agent_name=agent_name,
        stage=stage,
        status=status,
        timestamp=now,
        concise_summary=concise_summary,
        details=details or {},
        optional_artifact_refs=artifact_refs or [],
        optional_error=error,
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO run_events (
                event_id, run_id, workspace_id, agent_name, stage, status,
                timestamp, concise_summary, details_json, optional_artifact_refs_json, optional_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.event_id,
                ev.run_id,
                ev.workspace_id,
                ev.agent_name,
                ev.stage,
                ev.status,
                ev.timestamp,
                ev.concise_summary,
                dumps(ev.details),
                dumps(ev.optional_artifact_refs),
                ev.optional_error,
            ),
        )
        if retry_count is not None:
            conn.execute(
                "UPDATE task_runs SET status = ?, stage = ?, retry_count = ?, updated_at = ? WHERE run_id = ?",
                (status, stage, retry_count, now, run_id),
            )
        else:
            conn.execute(
                "UPDATE task_runs SET status = ?, stage = ?, updated_at = ? WHERE run_id = ?",
                (status, stage, now, run_id),
            )
    EVENT_BUS.publish(ev)
    return ev


def _load_pending_comments(workspace_id: str) -> dict[str, list[dict[str, Any]]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM visual_comments WHERE workspace_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (workspace_id,),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        item = dict(r)
        item["anchor"] = loads(item.pop("anchor_json"), {})
        item["preview_view_state"] = loads(item.pop("preview_view_state_json"), {})
        grouped.setdefault(item["artifact_version_id"], []).append(item)
    return grouped


def _restore_unapplied_comments(comment_ids: list[str]) -> None:
    if not comment_ids:
        return
    placeholders = ",".join("?" for _ in comment_ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE visual_comments SET status = 'pending' "
            f"WHERE comment_id IN ({placeholders}) AND resolved_in_version_id IS NULL",
            tuple(comment_ids),
        )


def _add_chat_message(
    workspace_id: str,
    role: str,
    content: str,
    run_id: str | None = None,
    artifact_version_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChatMessage:
    now = utc_now_iso()
    meta_dict = metadata or {}
    fu_raw = meta_dict.get("follow_up")
    fu_obj = FollowUpSuggestion.model_validate(fu_raw) if isinstance(fu_raw, dict) else None
    ac_raw = meta_dict.get("attached_comments")
    ac_list = (
        [AttachedCommentSummary.model_validate(item) for item in ac_raw]
        if isinstance(ac_raw, list)
        else []
    )
    msg = ChatMessage(
        message_id=f"msg_{uuid.uuid4().hex[:10]}",
        workspace_id=workspace_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        run_id=run_id,
        artifact_version_ids=artifact_version_ids or [],
        follow_up=fu_obj,
        attached_comments=ac_list,
        created_at=now,
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO messages (message_id, workspace_id, role, content, run_id, artifact_version_ids_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg.message_id,
                msg.workspace_id,
                msg.role,
                msg.content,
                msg.run_id,
                dumps(msg.artifact_version_ids),
                dumps(meta_dict),
                msg.created_at,
            ),
        )
        conn.execute(
            "UPDATE workspaces SET updated_at = ? WHERE workspace_id = ?",
            (now, workspace_id),
        )
    return msg


class ResearchOrchestrator:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.data_agent = DataAgent(self.llm)
        self.analysis_agent = AnalysisAgent(self.llm)
        self.vis_agent = VisualizationAgent(self.llm)
        self.val_agent = ValidationAgent()
        self.interaction_agent = InteractionAgent(self.llm)

    async def _plan_run(
        self,
        workspace_id: str,
        req: CreateTaskRequest,
        pending_override: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        existing_versions = list_workspace_artifact_versions(workspace_id)
        pending_map = (
            pending_override
            if pending_override is not None
            else _load_pending_comments(workspace_id)
        )

        version_summaries = [
            {
                "artifact_version_id": v.artifact_version_id,
                "artifact_id": v.artifact_id,
                "title": v.title,
                "chart_kind": v.chart_kind,
                "version_number": v.version_number,
                "dataset_snapshot_ids": v.dataset_snapshot_ids,
                "pending_comments": [
                    {"comment_id": c["comment_id"], "text": c["text"], "anchor": c["anchor"]}
                    for c in pending_map.get(v.artifact_version_id, [])
                ],
            }
            for v in existing_versions
        ]

        planner_system = """
You are the Orchestrator Agent in WHALES Lab Research Canvas.
Analyze the user's natural-language request, existing workspace artifacts, and any pending visual comments, and produce a structured execution plan in JSON.

Core Rules:
1. NEVER use hardcoded templates or reject new visualization types. Interpret ANY analytical visualization request (statistical charts, force-directed/node-link networks, hierarchical edge bundling, heatmaps, treemaps, scatter/bubble plots, chord diagrams, etc.) dynamically.
2. If the user asks for multiple visualizations in one request (e.g. "show AI publication trends, an author collaboration network, and a hierarchical bundled network view"), decompose into separate `tasks` (one per independent interactive HTML artifact) so each becomes its own card stacked vertically.
3. Revision & Ambiguity Rule (§3.4):
   - If the user asks to apply pending comments or revise an existing chart:
     - Check `target_artifact_version_id`, `selected_artifact_version_ids`, explicit title/ID mentions in prompt, and which artifact(s) have `pending_comments`.
     - If the user says something ambiguous like "revise this chart" or "fix it" when multiple artifacts exist, none is targeted/selected, and either 0 or >1 artifacts have pending comments without clear disambiguation -> set `"action": "clarify"` with a polite `clarification_message` listing the candidate artifacts! NEVER guess randomly.
   - When revising an existing artifact purely for visual/layout/spacing/labeling/filtering feedback (without changing the underlying year range or dataset population), set `"reuse_dataset_snapshot": true` so Data Map preserves the exact `dataset_snapshot_id`! If the user explicitly asks to change the underlying dataset filter (e.g., restrict to 2021-2025 or different topic), set `"reuse_dataset_snapshot": false`.
4. Return valid JSON strictly matching:
{
  "action": "execute" | "clarify",
  "clarification_message": "<only if action == 'clarify'>",
  "reasoning": "<brief technical rationale>",
  "tasks": [
    {
      "mode": "create" | "revise",
      "parent_version_id": "<existing artifact_version_id if mode=='revise', else null>",
      "title": "<concise clear artifact title>",
      "chart_kind": "<descriptive paradigm e.g. 'Interactive Statistical Trend Chart', 'Force-Directed Collaboration Network', 'Hierarchical Edge Bundling Network', 'Topic-by-Year Heatmap'>",
      "reuse_dataset_snapshot": true | false,
      "data_scope": "<exact filtering/cleaning goal on OpenAlex AI works>",
      "analytical_goal": "<exact statistical/network/hierarchical computation to execute in Python>",
      "visualization_goal": "<exact D3 v7 visual encoding, layout algorithm, legends, tooltips, and interactive controls>",
      "comment_ids_to_address": ["comment_xxx", ...]
    }
  ]
}
"""

        planner_user = json.dumps(
            {
                "user_prompt": req.prompt,
                "uploaded_dataset_id": req.upload_id,
                "explicit_target_artifact_version_id": req.target_artifact_version_id,
                "selected_artifact_version_ids": req.selected_artifact_version_ids,
                "apply_pending_comments": req.apply_pending_comments,
                "existing_artifact_versions": version_summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
        return await self.llm.complete_json(planner_system, planner_user, max_tokens=8192)

    async def execute_run(
        self,
        run_id: str,
        workspace_id: str,
        task_group_id: str,
        req: CreateTaskRequest,
        submitted_comments: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        submitted_cids = [
            c["comment_id"]
            for clist in (submitted_comments or {}).values()
            for c in clist
        ]
        try:
            stage_labels = await self.interaction_agent.synthesize_stage_labels(req.prompt, [])
            _emit_event(
                run_id,
                workspace_id,
                "Orchestrator",
                "planning",
                "running",
                "Interpreting research intent, workspace context, and pending visual comments...",
                details={"stream_label": stage_labels["planning"]},
            )

            plan = await self._plan_run(
                workspace_id, req, pending_override=submitted_comments
            )
            action = plan.get("action", "execute")

            if action == "clarify":
                _restore_unapplied_comments(submitted_cids)
                clarification = plan.get("clarification_message") or (
                    "Could you clarify which artifact you would like me to revise? "
                    "Please name the chart or select it before sending a revision prompt."
                )
                _emit_event(
                    run_id,
                    workspace_id,
                    "Orchestrator",
                    "completed",
                    "completed",
                    "Requested clarification instead of guessing an ambiguous target.",
                    details={"clarification": clarification, "stream_label": stage_labels["completed"]},
                )
                _add_chat_message(workspace_id, "assistant", clarification, run_id=run_id)
                return

            planned_tasks: list[dict[str, Any]] = plan.get("tasks") or []
            if not planned_tasks:
                raise RuntimeError("Orchestrator produced an empty task plan.")

            _emit_event(
                run_id,
                workspace_id,
                "Orchestrator",
                "planning",
                "running",
                f"Planned {len(planned_tasks)} artifact workflow(s): "
                + ", ".join(t.get("title", "Artifact") for t in planned_tasks),
                details={"plan": plan, "stream_label": stage_labels["planning"]},
            )

            produced_versions: list[ArtifactVersion] = []
            failures: list[str] = []
            retry_state = {"count": 0}
            pending_map = (
                submitted_comments
                if submitted_comments is not None
                else _load_pending_comments(workspace_id)
            )
            playwright_sem = asyncio.Semaphore(3)

            # Step 1: Prepare DatasetSnapshots sequentially so shared initial tasks reuse cached_group_snapshot
            cached_group_snapshot: dict[str, Any] | None = None
            prepared_inputs: list[dict[str, Any]] = []
            for idx, task in enumerate(planned_tasks):
                parent_vid = task.get("parent_version_id")
                parent_av = get_artifact_version(parent_vid) if parent_vid else None
                title = task.get("title") or f"Research Artifact {idx + 1}"
                chart_kind = task.get("chart_kind") or "Interactive Visualization"
                data_scope = task.get("data_scope") or "All OpenAlex AI works (2018-2025)"

                reuse_sid: str | None = None
                if not req.upload_id and parent_av and task.get("reuse_dataset_snapshot", True):
                    if parent_av.dataset_snapshot_ids:
                        reuse_sid = parent_av.dataset_snapshot_ids[0]
                elif not parent_av and cached_group_snapshot and len(planned_tasks) > 1:
                    # Multiple outputs from the same initial query share the verified group snapshot
                    # unless the task explicitly asks for a narrower subset
                    reuse_sid = cached_group_snapshot["snapshot_id"]

                _emit_event(
                    run_id,
                    workspace_id,
                    "DataAgent",
                    "collecting",
                    "running",
                    f"[{title}] {'Reusing immutable DatasetSnapshot ' + reuse_sid if reuse_sid else 'Retrieving & transforming OpenAlex records in Python sandbox...'}",
                    details={"stream_label": stage_labels["collecting"]},
                )
                try:
                    snapshot = await self.data_agent.prepare_snapshot(
                        workspace_id=workspace_id,
                        data_scope=data_scope,
                        reuse_snapshot_id=reuse_sid,
                        upload_id=req.upload_id,
                    )
                    if not parent_av and not cached_group_snapshot:
                        cached_group_snapshot = snapshot
                    _emit_event(
                        run_id,
                        workspace_id,
                        "DataAgent",
                        "collecting",
                        "running",
                        f"[{title}] Snapshot {snapshot['snapshot_id']} ready ({snapshot['retained_record_count']} works, mode={snapshot['retrieval_mode']}).",
                        details={
                            "snapshot_id": snapshot["snapshot_id"],
                            "retained_record_count": snapshot["retained_record_count"],
                            "retrieval_mode": snapshot["retrieval_mode"],
                            "stream_label": stage_labels["collecting"],
                        },
                    )
                    prepared_inputs.append(
                        {
                            "idx": idx,
                            "task": task,
                            "parent_av": parent_av,
                            "title": title,
                            "chart_kind": chart_kind,
                            "snapshot": snapshot,
                            "error": None,
                        }
                    )
                except Exception as data_exc:  # noqa: BLE001
                    err_str = f"{type(data_exc).__name__}: {data_exc}"
                    prepared_inputs.append(
                        {
                            "idx": idx,
                            "task": task,
                            "parent_av": parent_av,
                            "title": title,
                            "chart_kind": chart_kind,
                            "snapshot": None,
                            "error": err_str,
                        }
                    )

            # Step 2: Execute Analysis + Visualization + Validation concurrently across tasks
            async def _run_artifact_pipeline(item: dict[str, Any]) -> dict[str, Any]:
                if item["error"] or not item["snapshot"]:
                    return item

                idx = item["idx"]
                task = item["task"]
                parent_av = item["parent_av"]
                title = item["title"]
                chart_kind = item["chart_kind"]
                snapshot = item["snapshot"]
                analytical_goal = task.get("analytical_goal") or req.prompt
                visualization_goal = task.get("visualization_goal") or req.prompt

                try:
                    target_comments: list[dict[str, Any]] = []
                    if parent_av:
                        all_parent_pending = pending_map.get(parent_av.artifact_version_id, [])
                        requested_cids = set(task.get("comment_ids_to_address") or [])
                        if requested_cids:
                            target_comments = [
                                c for c in all_parent_pending if c["comment_id"] in requested_cids
                            ]
                        if not target_comments and req.apply_pending_comments:
                            target_comments = all_parent_pending

                    revision_notes = "\n".join(
                        f"- Comment {c['comment_id']} at {c.get('anchor')}: {c['text']}"
                        for c in target_comments
                    )

                    _emit_event(
                        run_id,
                        workspace_id,
                        "AnalysisAgent",
                        "analyzing",
                        "running",
                        f"[{title}] Writing & executing Python statistical/network analysis on {snapshot['snapshot_id']}...",
                        details={"stream_label": stage_labels["analyzing"]},
                    )
                    reuse_ar = (
                        parent_av.analysis_run_ids[0]
                        if (
                            parent_av
                            and parent_av.analysis_run_ids
                            and task.get("reuse_dataset_snapshot", True)
                            and not revision_notes
                        )
                        else None
                    )
                    analysis_run = await self.analysis_agent.run_analysis(
                        workspace_id=workspace_id,
                        snapshot=snapshot,
                        analytical_goal=analytical_goal,
                        chart_kind=chart_kind,
                        revision_notes=revision_notes,
                        reuse_analysis_run_id=reuse_ar,
                    )
                    summary_obj = loads(analysis_run.get("summary_json"), {})
                    _emit_event(
                        run_id,
                        workspace_id,
                        "AnalysisAgent",
                        "analyzing",
                        "running",
                        f"[{title}] Analysis {analysis_run['analysis_run_id']} ({analysis_run['analysis_algorithm']}) completed: {summary_obj.get('headline', '')}",
                        details={
                            "analysis_run_id": analysis_run["analysis_run_id"],
                            "algorithm": analysis_run["analysis_algorithm"],
                            "summary": summary_obj,
                            "stream_label": stage_labels["analyzing"],
                        },
                    )

                    new_av_id = f"av_{uuid.uuid4().hex[:10]}"
                    art_id = parent_av.artifact_id if parent_av else f"art_{uuid.uuid4().hex[:10]}"
                    ver_num = (parent_av.version_number + 1) if parent_av else 1
                    thumb_path = RUNTIME_DIR / "artifacts" / new_av_id / "preview.png"

                    parent_html: str | None = None
                    if parent_av:
                        p_html_path = RUNTIME_DIR / parent_av.html_ref
                        if p_html_path.exists():
                            parent_html = p_html_path.read_text(encoding="utf-8")

                    validation_feedback: str | None = None
                    prev_html: str | None = None
                    val_report: dict[str, Any] = {}
                    final_html = ""

                    for attempt in range(MAX_RETRIES + 1):
                        if attempt == 0:
                            _emit_event(
                                run_id,
                                workspace_id,
                                "VisualizationAgent",
                                "generating_html",
                                "running",
                                f"[{title}] Generating interactive D3 v7 HTML visualization...",
                                details={"stream_label": stage_labels["generating_html"]},
                            )
                        else:
                            retry_state["count"] += 1
                            _emit_event(
                                run_id,
                                workspace_id,
                                "VisualizationAgent",
                                "retrying",
                                "retrying",
                                f"[{title}] Repair attempt {attempt}/{MAX_RETRIES} addressing headless Chromium validation errors...",
                                details={
                                    "validation_feedback": validation_feedback,
                                    "stream_label": stage_labels["retrying"],
                                },
                                retry_count=retry_state["count"],
                            )

                        inject_fault_now = bool(req.inject_validation_fault_once and idx == 0 and attempt == 0)
                        final_html = await self.vis_agent.generate_html(
                            title=title,
                            chart_kind=chart_kind,
                            visualization_goal=visualization_goal,
                            analysis_run=analysis_run,
                            comments=target_comments,
                            parent_html=parent_html,
                            validation_feedback=validation_feedback,
                            previous_attempt_html=prev_html,
                            inject_fault=inject_fault_now,
                        )

                        _emit_event(
                            run_id,
                            workspace_id,
                            "ValidationAgent",
                            "validating",
                            "running",
                            f"[{title}] Running headless Chromium QA & capturing real preview screenshot...",
                            details={"stream_label": stage_labels["validating"]},
                        )
                        async with playwright_sem:
                            val_report = await self.val_agent.validate_artifact_html(
                                html_text=final_html,
                                thumbnail_path=thumb_path,
                            )
                        if val_report.get("passed"):
                            break

                        prev_html = final_html
                        validation_feedback = "; ".join(val_report.get("reasons", ["Unknown validation failure"]))
                        _emit_event(
                            run_id,
                            workspace_id,
                            "ValidationAgent",
                            "validating",
                            "retrying" if attempt < MAX_RETRIES else "failed",
                            f"[{title}] Validation failed (attempt {attempt + 1}): {validation_feedback}",
                            error=validation_feedback,
                            details={
                                "validation_report": val_report,
                                "stream_label": (
                                    stage_labels["retrying"]
                                    if attempt < MAX_RETRIES
                                    else stage_labels["validating"]
                                ),
                            },
                        )

                    if not val_report.get("passed"):
                        raise RuntimeError(
                            f"[{title}] Failed Validation after {MAX_RETRIES + 1} attempts: {validation_feedback}"
                        )

                    # Persist immediately with deterministic target_row=idx so validated cards stream onto Canvas right away
                    build_and_persist_lineage(
                        artifact_version_id=new_av_id,
                        title=title,
                        chart_kind=chart_kind,
                        snapshot_ids=[snapshot["snapshot_id"]],
                        analysis_run_ids=[analysis_run["analysis_run_id"]],
                        validation_report=val_report,
                        parent_version_id=parent_av.artifact_version_id if parent_av else None,
                    )

                    saved_av = save_artifact_version(
                        artifact_version_id=new_av_id,
                        artifact_id=art_id,
                        workspace_id=workspace_id,
                        task_group_id=parent_av.task_group_id if parent_av else task_group_id,
                        parent_version_id=parent_av.artifact_version_id if parent_av else None,
                        version_number=ver_num,
                        title=title,
                        chart_kind=chart_kind,
                        html_text=final_html,
                        thumbnail_path=thumb_path,
                        dataset_snapshot_ids=[snapshot["snapshot_id"]],
                        analysis_run_ids=[analysis_run["analysis_run_id"]],
                        lineage_ref=f"lineage/{new_av_id}.json",
                        addressed_comment_ids=[c["comment_id"] for c in target_comments],
                        validation_report=val_report,
                        summary=str(summary_obj.get("headline", "")),
                        target_row=None if parent_av else idx,
                    )

                    _emit_event(
                        run_id,
                        workspace_id,
                        "ArtifactManager",
                        "validating",
                        "running",
                        f"[{title}] Validated & placed {saved_av.artifact_version_id} (v{saved_av.version_number}) onto Research Canvas.",
                        details={"stream_label": stage_labels["validating"]},
                        artifact_refs=[saved_av.artifact_version_id],
                    )

                    return {
                        **item,
                        "saved_av": saved_av,
                        "error": None,
                    }
                except Exception as task_exc:  # noqa: BLE001
                    return {**item, "error": f"{type(task_exc).__name__}: {task_exc}"}

            results = await asyncio.gather(*(_run_artifact_pipeline(item) for item in prepared_inputs))

            for res in sorted(results, key=lambda r: r["idx"]):
                title = res["title"]
                if res.get("error"):
                    err_str = str(res["error"])
                    failures.append(err_str)
                    _emit_event(
                        run_id,
                        workspace_id,
                        "Orchestrator",
                        "failed",
                        "running" if res["idx"] < len(results) - 1 else "failed",
                        f"[{title}] Artifact execution failed safely (existing versions preserved): {err_str}",
                        error=err_str,
                    )
                    continue

                produced_versions.append(res["saved_av"])

            produced_ids = [v.artifact_version_id for v in produced_versions]
            with get_conn() as conn:
                conn.execute(
                    "UPDATE task_runs SET produced_version_ids_json = ?, error_message = ? WHERE run_id = ?",
                    (
                        dumps(produced_ids),
                        "; ".join(failures) if failures else None,
                        run_id,
                    ),
                )

            if produced_versions and not failures:
                reply, follow_up = await self.interaction_agent.synthesize_completion(
                    user_prompt=req.prompt,
                    produced_versions=produced_versions,
                )
                _add_chat_message(
                    workspace_id,
                    "assistant",
                    reply,
                    run_id=run_id,
                    artifact_version_ids=produced_ids,
                    metadata={"follow_up": follow_up},
                )
                _emit_event(
                    run_id,
                    workspace_id,
                    "Orchestrator",
                    "completed",
                    "completed",
                    f"Run completed with {len(produced_versions)} validated artifact(s).",
                    details={"stream_label": stage_labels["completed"]},
                    artifact_refs=produced_ids,
                )
            elif produced_versions and failures:
                reply, follow_up = await self.interaction_agent.synthesize_completion(
                    user_prompt=req.prompt,
                    produced_versions=produced_versions,
                    failures=failures,
                )
                _add_chat_message(
                    workspace_id,
                    "assistant",
                    reply,
                    run_id=run_id,
                    artifact_version_ids=produced_ids,
                    metadata={"follow_up": follow_up},
                )
                _emit_event(
                    run_id,
                    workspace_id,
                    "Orchestrator",
                    "completed",
                    "completed",
                    f"Partially completed ({len(produced_versions)} succeeded, {len(failures)} failed).",
                    details={"stream_label": stage_labels["completed"]},
                    artifact_refs=produced_ids,
                    error="; ".join(failures),
                )
            else:
                _restore_unapplied_comments(submitted_cids)
                err_msg = "; ".join(failures) or "All artifact tasks failed."
                _add_chat_message(
                    workspace_id,
                    "assistant",
                    f"Analysis run failed safely without publishing unvalidated outputs (previous versions remain intact):\n{err_msg}",
                    run_id=run_id,
                )
                _emit_event(
                    run_id,
                    workspace_id,
                    "Orchestrator",
                    "failed",
                    "failed",
                    f"Run failed safely: {err_msg}",
                    error=err_msg,
                )
        except Exception as fatal_exc:  # noqa: BLE001
            _restore_unapplied_comments(submitted_cids)
            err_msg = f"{type(fatal_exc).__name__}: {fatal_exc}"
            with get_conn() as conn:
                conn.execute(
                    "UPDATE task_runs SET error_message = ? WHERE run_id = ?",
                    (err_msg, run_id),
                )
            _add_chat_message(
                workspace_id,
                "assistant",
                f"Orchestrator encountered an error (existing canvas artifacts unchanged): {err_msg}",
                run_id=run_id,
            )
            _emit_event(
                run_id,
                workspace_id,
                "Orchestrator",
                "failed",
                "failed",
                f"Fatal run error: {err_msg}",
                error=err_msg,
            )
        finally:
            EVENT_BUS.close(run_id)
