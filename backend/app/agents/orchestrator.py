from __future__ import annotations

import asyncio
import json
import re
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
from ..services.browser_validator import await_comment_screenshots
from ..services.lineage_manager import build_and_persist_lineage
from .analysis_agent import AnalysisAgent
from .data_agent import DataAgent
from .interaction_agent import InteractionAgent, _contains_cjk
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

        snap_counts: dict[str, int] = {}
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT snapshot_id, retained_record_count FROM dataset_snapshots WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall():
                snap_counts[r["snapshot_id"]] = int(r["retained_record_count"] or 0)

        version_summaries = [
            {
                "artifact_version_id": v.artifact_version_id,
                "artifact_id": v.artifact_id,
                "title": v.title,
                "chart_kind": v.chart_kind,
                "version_number": v.version_number,
                "dataset_snapshot_ids": v.dataset_snapshot_ids,
                "snapshot_record_counts": [snap_counts.get(sid, 0) for sid in v.dataset_snapshot_ids],
                "analysis_run_ids": v.analysis_run_ids,
                "pending_comments": [
                    {
                        "comment_id": c["comment_id"],
                        "text": c["text"],
                        "anchor": c["anchor"],
                        "surface": (
                            "data_map"
                            if (((c.get("preview_view_state") or {}).get("filter_state") or {}).get("surface") == "data_map")
                            else "chart_preview"
                        ),
                        "element_hint": c.get("optional_element_hint"),
                        "nearby_labels": (
                            ((c.get("preview_view_state") or {}).get("filter_state") or {})
                            .get("__target_inspector", {})
                            .get("nearby_labels")
                        ),
                    }
                    for c in pending_map.get(v.artifact_version_id, [])
                ],
            }
            for v in existing_versions
        ]

        planner_system = """
You are the Orchestrator Agent in Libra Research Canvas.
Interpret the user's natural-language research or revision request, existing workspace artifacts, and any pending visual comments, and produce a generalizable execution plan in JSON.

Core Principles:
1. ZERO Hardcoded Templates: Interpret ANY analytical domain and visualization requirement dynamically from first principles. Choose complementary, high-signal views suited to the user's research question (keep initial exploratory requests focused to 2-3 high-value interactive artifacts unless the user explicitly asks for a different number).
2. Independent Canvas Cards: Decompose multi-view requests into separate `tasks` (one per independent interactive HTML artifact).
3. Revision & Fast Iteration Rules:
   - If the user asks to apply pending comments or revise an existing chart:
     - Check `target_artifact_version_id`, `selected_artifact_version_ids`, explicit title/ID mentions in prompt, and which artifact(s) have `pending_comments`.
     - If the target is genuinely ambiguous across multiple existing artifacts with no selection or comment disambiguation -> set `"action": "clarify"` with a polite `clarification_message` listing the candidate artifacts.
   - `reuse_dataset_snapshot`: Set `true` when revising an existing artifact without changing the underlying dataset population/topic/time-window; set `false` if a comment/prompt questions insufficient sample size (check `snapshot_record_counts`!), requests a different filter/time-window, or critiques Data Map source/exclusion rules (`surface == "data_map"`).
   - `reuse_analysis_run`: Set `true` when `mode == "revise"`, `reuse_dataset_snapshot == true`, and the revision/comments only change visual encoding, layout, styling, colors, labels, tooltips, UI controls, or series visibility without needing new Python statistical/graph metrics; set `false` when comments question computed numbers/aggregations or request new metrics.
4. Return valid JSON strictly matching:
{
  "action": "execute" | "clarify",
  "clarification_message": "<only if action == 'clarify'>",
  "brief_action": "<1 ultra-concise user-facing sentence (<= 25 words / <= 32 Chinese chars) in the user's language stating ONLY what substantive data processing or chart change is being done. NEVER mention snapshots, IDs, caching, reuse, agents, or workflow stages!>",
  "reasoning": "<brief technical rationale>",
  "tasks": [
    {
      "mode": "create" | "revise",
      "parent_version_id": "<existing artifact_version_id if mode=='revise', else null>",
      "title": "<concise clear artifact title in user's language>",
      "chart_kind": "<descriptive visualization paradigm>",
      "reuse_dataset_snapshot": true | false,
      "reuse_analysis_run": true | false,
      "data_scope": "<exact domain, entity scope, and filtering criteria>",
      "analytical_goal": "<exact statistical, temporal, clustering, relational, or distributional computation to execute>",
      "visualization_goal": "<exact visual encoding, interactive controls, hover details, and layout refinements>",
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
        return await self.llm.complete_json(planner_system, planner_user, max_tokens=6144)

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
        comment_texts = " ".join(
            str(c.get("text") or "")
            for clist in (submitted_comments or {}).values()
            for c in clist
        )
        ws_title = ""
        with get_conn() as conn:
            ws_row = conn.execute(
                "SELECT title FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if ws_row:
                ws_title = str(ws_row["title"] or "")
        lang_probe = f"{req.prompt} {comment_texts} {ws_title}".strip()
        is_cjk = _contains_cjk(lang_probe)
        try:
            initial_plan_label = "解析需求与数据范围" if is_cjk else "Scoping analysis"
            _emit_event(
                run_id,
                workspace_id,
                "Orchestrator",
                "planning",
                "running",
                "Interpreting research intent, workspace context, and pending visual comments...",
                details={
                    "stream_label": initial_plan_label,
                },
            )

            # Run stage-label synthesis and run-planning concurrently to cut initial latency
            stage_labels, plan = await asyncio.gather(
                self.interaction_agent.synthesize_stage_labels(lang_probe or req.prompt, []),
                self._plan_run(workspace_id, req, pending_override=submitted_comments),
            )
            action = plan.get("action", "execute")

            if action == "clarify":
                _restore_unapplied_comments(submitted_cids)
                clarification = plan.get("clarification_message") or (
                    "请说明你希望修改哪一张图表，或在画布中选中目标图表后再发送修改指令。"
                    if is_cjk
                    else "Could you clarify which artifact you would like me to revise? Please name the chart or select it before sending a revision prompt."
                )
                _add_chat_message(workspace_id, "assistant", clarification, run_id=run_id)
                _emit_event(
                    run_id,
                    workspace_id,
                    "Orchestrator",
                    "completed",
                    "completed",
                    "Requested clarification instead of guessing an ambiguous target.",
                    details={"clarification": clarification, "stream_label": stage_labels["completed"]},
                )
                return

            planned_tasks: list[dict[str, Any]] = plan.get("tasks") or []
            if not planned_tasks:
                raise RuntimeError("Orchestrator produced an empty task plan.")

            brief_action = str(plan.get("brief_action") or "").strip()
            # Scrub any accidental internal ID tokens from brief_action
            brief_action = re.sub(r"\b(?:ds|ar|av|ws|run)_[A-Za-z0-9]+\b", "", brief_action).strip()

            _emit_event(
                run_id,
                workspace_id,
                "Orchestrator",
                "planning",
                "running",
                f"Planned {len(planned_tasks)} artifact workflow(s): "
                + ", ".join(t.get("title", "Artifact") for t in planned_tasks),
                details={
                    "plan": plan,
                    "stream_label": stage_labels["planning"],
                    **({"progress_note": brief_action} if brief_action else {}),
                },
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

            # Step 1: Prepare DatasetSnapshots (sharing a group snapshot across sibling creation tasks, or running distinct scopes concurrently)
            task_entries: list[dict[str, Any]] = []
            shared_create_scope: str | None = None
            for idx, task in enumerate(planned_tasks):
                parent_vid = task.get("parent_version_id")
                parent_av = get_artifact_version(parent_vid) if parent_vid else None
                title = task.get("title") or f"Research Artifact {idx + 1}"
                chart_kind = task.get("chart_kind") or "Interactive Visualization"
                data_scope = task.get("data_scope") or req.prompt

                reuse_sid: str | None = None
                if not req.upload_id and parent_av and task.get("reuse_dataset_snapshot", True):
                    if parent_av.dataset_snapshot_ids:
                        reuse_sid = parent_av.dataset_snapshot_ids[0]

                if reuse_sid:
                    group_key = f"reuse:{reuse_sid}"
                elif not parent_av:
                    # Sibling exploratory views from one user prompt share one rich multi-miner corpus snapshot
                    if shared_create_scope is None:
                        shared_create_scope = data_scope
                    group_key = "shared_create_corpus"
                else:
                    group_key = f"custom:{idx}:{data_scope[:80]}"

                task_entries.append(
                    {
                        "idx": idx,
                        "task": task,
                        "parent_av": parent_av,
                        "title": title,
                        "chart_kind": chart_kind,
                        "data_scope": shared_create_scope if group_key == "shared_create_corpus" else data_scope,
                        "reuse_sid": reuse_sid,
                        "group_key": group_key,
                    }
                )

            unique_groups: dict[str, dict[str, Any]] = {}
            for entry in task_entries:
                unique_groups.setdefault(entry["group_key"], entry)

            def _make_miner_progress_cb() -> Any:
                def _cb(summary: str, extra: dict[str, Any]) -> None:
                    _emit_event(
                        run_id,
                        workspace_id,
                        "MinerCoordinator",
                        "collecting",
                        "running",
                        summary,
                        details={
                            **extra,
                            "stream_label": stage_labels["collecting"],
                        },
                    )
                return _cb

            async def _prepare_group_snapshot(rep_entry: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
                gkey = rep_entry["group_key"]
                reuse_sid = rep_entry["reuse_sid"]
                d_scope = rep_entry["data_scope"]
                t_title = rep_entry["title"]

                if reuse_sid:
                    _emit_event(
                        run_id,
                        workspace_id,
                        "DataAgent",
                        "collecting",
                        "running",
                        f"[{t_title}] Reusing immutable DatasetSnapshot {reuse_sid}",
                        details={"stream_label": stage_labels["collecting"]},
                    )
                else:
                    _emit_event(
                        run_id,
                        workspace_id,
                        "DataAgent",
                        "collecting",
                        "running",
                        f"[{t_title}] Dispatching parallel Miner Agents & sandbox cleaning...",
                        details={"stream_label": stage_labels["collecting"]},
                    )

                try:
                    snap = await self.data_agent.prepare_snapshot(
                        workspace_id=workspace_id,
                        data_scope=d_scope,
                        reuse_snapshot_id=reuse_sid,
                        upload_id=req.upload_id,
                        research_prompt=lang_probe or req.prompt,
                        on_progress=_make_miner_progress_cb() if not reuse_sid else None,
                    )
                    # Encapsulation: NEVER report internal snapshot reuse or internal IDs in chat!
                    # Only report genuine data cleaning deltas when new data transformation occurred.
                    snap_note: str | None = None
                    if not reuse_sid:
                        raw_cnt = int(snap.get("raw_record_count") or 0)
                        ret_cnt = int(snap.get("retained_record_count") or 0)
                        dropped_cnt = max(0, raw_cnt - ret_cnt)
                        if dropped_cnt > 0:
                            snap_note = (
                                f"已完成字段标准化与缺失值过滤，剔除 {dropped_cnt} 条不完整记录，进入统计分析样本共 **{ret_cnt} 条**。"
                                if is_cjk
                                else f"Standardized fields and removed {dropped_cnt} incomplete records (**{ret_cnt} valid records** entering analysis)."
                            )
                    _emit_event(
                        run_id,
                        workspace_id,
                        "DataAgent",
                        "collecting",
                        "running",
                        f"Snapshot {snap['snapshot_id']} ready ({snap['retained_record_count']} records, mode={snap['retrieval_mode']}).",
                        details={
                            "snapshot_id": snap["snapshot_id"],
                            "retained_record_count": snap["retained_record_count"],
                            "retrieval_mode": snap["retrieval_mode"],
                            "stream_label": stage_labels["collecting"],
                            **({"progress_note": snap_note} if snap_note else {}),
                        },
                    )
                    return gkey, snap, None
                except Exception as data_exc:  # noqa: BLE001
                    return gkey, None, f"{type(data_exc).__name__}: {data_exc}"

            group_results = await asyncio.gather(
                *(_prepare_group_snapshot(rep) for rep in unique_groups.values())
            )
            snapshots_by_group = {gk: (snap, err) for gk, snap, err in group_results}

            prepared_inputs: list[dict[str, Any]] = []
            for entry in task_entries:
                snap, err = snapshots_by_group[entry["group_key"]]
                prepared_inputs.append(
                    {
                        "idx": entry["idx"],
                        "task": entry["task"],
                        "parent_av": entry["parent_av"],
                        "title": entry["title"],
                        "chart_kind": entry["chart_kind"],
                        "snapshot": snap,
                        "error": err,
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

                    if target_comments:
                        await await_comment_screenshots([c["comment_id"] for c in target_comments])

                    revision_lines: list[str] = []
                    for c in target_comments:
                        hint_str = f" | target_element={c.get('optional_element_hint')}" if c.get("optional_element_hint") else ""
                        insp = (((c.get("preview_view_state") or {}).get("filter_state") or {}).get("__target_inspector") or {})
                        nearby = insp.get("nearby_labels")
                        nearby_str = f" | nearby_labels={nearby}" if nearby else ""
                        revision_lines.append(
                            f"- Comment {c['comment_id']} at anchor={c.get('anchor')}{hint_str}{nearby_str}: {c['text']}"
                        )
                    revision_notes = "\n".join(revision_lines)

                    can_reuse_ar = bool(
                        parent_av
                        and parent_av.analysis_run_ids
                        and task.get("reuse_dataset_snapshot", True)
                        and (task.get("reuse_analysis_run", False) or not revision_notes)
                    )
                    reuse_ar = parent_av.analysis_run_ids[0] if (can_reuse_ar and parent_av) else None

                    if not reuse_ar:
                        _emit_event(
                            run_id,
                            workspace_id,
                            "AnalysisAgent",
                            "analyzing",
                            "running",
                            f"[{title}] Executing Python analytical computation on {snapshot['snapshot_id']}...",
                            details={"stream_label": stage_labels["analyzing"]},
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
                    headline_txt = str(summary_obj.get("headline") or "").strip()
                    # Encapsulation: Only surface substantive data findings when new computation ran; never report cached metric reuse!
                    analysis_note = headline_txt if (not reuse_ar and headline_txt) else None
                    _emit_event(
                        run_id,
                        workspace_id,
                        "AnalysisAgent",
                        "analyzing",
                        "running",
                        f"[{title}] Analysis {analysis_run['analysis_run_id']} ({analysis_run['analysis_algorithm']}) completed: {headline_txt}",
                        details={
                            "analysis_run_id": analysis_run["analysis_run_id"],
                            "algorithm": analysis_run["analysis_algorithm"],
                            "summary": summary_obj,
                            "stream_label": stage_labels["analyzing"],
                            **({"progress_note": analysis_note} if analysis_note else {}),
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
                                f"[{title}] Generating interactive HTML visualization...",
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
                            f"[{title}] Running headless Chromium QA & capturing preview screenshot...",
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
                            "retrying" if attempt < MAX_RETRIES else "running",
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
                        summary=headline_txt,
                        target_row=None if parent_av else idx,
                    )

                    _emit_event(
                        run_id,
                        workspace_id,
                        "ArtifactManager",
                        "validating",
                        "running",
                        f"[{title}] Validated & placed {saved_av.artifact_version_id} (v{saved_av.version_number}) onto Research Canvas.",
                        details={
                            "stream_label": stage_labels["validating"],
                        },
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
                    # IMPORTANT: Keep run status='running' while synthesizing the final chat message so the chat UI NEVER goes blank!
                    _emit_event(
                        run_id,
                        workspace_id,
                        "Orchestrator",
                        "validating",
                        "running",
                        f"[{title}] Artifact execution failed safely (existing versions preserved): {err_str}",
                        error=err_str,
                        details={
                            "stream_label": stage_labels.get("summarizing", stage_labels["validating"]),
                            "progress_note": (
                                f"《{title}》未通过最终校验（已保留既有版本不受影响），正在汇总其余分析结果。"
                                if is_cjk
                                else f"**{title}** did not pass validation (existing canvas versions preserved); wrapping up findings."
                            ),
                        },
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

            if produced_versions:
                _emit_event(
                    run_id,
                    workspace_id,
                    "InteractionAgent",
                    "validating",
                    "running",
                    "Synthesizing analytical report and follow-up suggestions...",
                    details={
                        "stream_label": stage_labels.get("summarizing", stage_labels["validating"]),
                    },
                )
                reply, follow_up = await self.interaction_agent.synthesize_completion(
                    user_prompt=lang_probe or req.prompt,
                    produced_versions=produced_versions,
                    failures=failures if failures else None,
                )
                # Commit assistant message FIRST, BEFORE emitting terminal 'completed' status!
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
                    (
                        f"Run completed with {len(produced_versions)} validated artifact(s)."
                        if not failures
                        else f"Partially completed ({len(produced_versions)} succeeded, {len(failures)} failed)."
                    ),
                    details={"stream_label": stage_labels["completed"]},
                    artifact_refs=produced_ids,
                    error="; ".join(failures) if failures else None,
                )
            else:
                _restore_unapplied_comments(submitted_cids)
                err_msg = "; ".join(failures) or "All artifact tasks failed."
                fail_reply = (
                    f"本次运行未能通过图表校验，未发布不合规结果（画布原有版本保持完好）：\n\n{err_msg}"
                    if is_cjk
                    else f"Analysis run failed safely without publishing unvalidated outputs (previous versions remain intact):\n\n{err_msg}"
                )
                # Commit assistant failure explanation FIRST, BEFORE emitting terminal 'failed' status!
                _add_chat_message(
                    workspace_id,
                    "assistant",
                    fail_reply,
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
                (
                    f"运行过程中遇到异常（画布已有图表不受影响）：{err_msg}"
                    if is_cjk
                    else f"Orchestrator encountered an error (existing canvas artifacts unchanged): {err_msg}"
                ),
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
            # Restore any submitted comment that was not resolved into a validated ArtifactVersion
            _restore_unapplied_comments(submitted_cids)
            EVENT_BUS.close(run_id)
