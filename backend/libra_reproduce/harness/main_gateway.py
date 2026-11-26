from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any

from ..schemas import (
    ChatCommand,
    ChatResponse,
    EventSubject,
    HarnessEvent,
    SelectedContext,
)
from ..storage.event_store import EventStore
from ..storage.db import utc_now_iso
from .coordinator import ResearchCoordinator, _wants_chinese
from .sdk_adapter import SDKAdapter

_READONLY_INQUIRY_PATTERNS = re.compile(
    r"(?:^(?:为什么|解释|说明|总结|查看|什么|有哪些|请问|为何|如何|怎么))|(?:^(?:why|explain|what|summarize|status|how\b|which\b))",
    flags=re.IGNORECASE,
)
_INQUIRY_HINT_RE = re.compile(
    r"(?:[?？]|为什么|怎么|如何|解释|说明|总结|查看|什么|哪些|原因|分析一下|请问|\b(?:why|how|what|explain|summarize|status|which|where|can\s+you|could\s+you)\b)",
    flags=re.IGNORECASE,
)
_EXPLICIT_RERUN_RE = re.compile(
    r"(?:重新复现|全部重跑|重新运行|再跑一遍|\b(?:re-?run|restart\s+reproduction|reproduce\s+again)\b)",
    flags=re.IGNORECASE,
)
_ROUNDTABLE_RE = re.compile(
    r"(?:召集.*讨论|圆桌|五人|全员|\b(?:roundtable|convene\s+all|all\s+agents|5-agent)\b)",
    flags=re.IGNORECASE,
)


_DYNAMIC_SPAWN_RE = re.compile(
    r"(?:动态调用|额外调用|并行调用|多调用|多个\s*(?:agent|分析|数据|论文|验证|支持)|再调用\s*\d+|spawn\s+\d+|spawn\s+(?:more|extra|parallel)\s+agents?)",
    flags=re.IGNORECASE,
)


class MainAgentGateway:
    """
    Sole user-facing command gateway (`POST /api/runs/{run_id}/chat`).
    - Enforces `(run_id, request_id)` idempotency.
    - Answers pure read-only inquiries (`200 answered`) from persisted Snapshot/Evidence without re-running tools.
    - Commits accepted commands atomically (`run.started`) before returning `202 accepted` and dispatching `ResearchCoordinator`.
    """

    def __init__(self, event_store: EventStore, coordinator: ResearchCoordinator) -> None:
        self.store = event_store
        self.coordinator = coordinator
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._tasks_by_run: dict[str, set[asyncio.Task[Any]]] = {}

    def _spawn_bg(self, run_id: str, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        self._tasks_by_run.setdefault(run_id, set()).add(t)

        def _done(done_task: asyncio.Task[Any]) -> None:
            self._bg_tasks.discard(done_task)
            run_set = self._tasks_by_run.get(run_id)
            if run_set is not None:
                run_set.discard(done_task)
                if not run_set:
                    self._tasks_by_run.pop(run_id, None)

        t.add_done_callback(_done)

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel any active background task for `run_id` and transition running state cleanly to failed."""
        meta = self.store.get_run_meta(run_id)
        if not meta:
            return False

        for task in list(self._tasks_by_run.get(run_id, set())):
            if not task.done():
                task.cancel()

        if meta["status"] in ("running", "paused"):
            self.coordinator.cancel_pending_intervention(run_id)
            mode = meta["mode"]

            def _cancel_mut(conn: sqlite3.Connection, seq: int) -> None:
                conn.execute(
                    "UPDATE reproduce_collaborations SET status = 'ended', last_event_seq = ? WHERE run_id = ? AND status IN ('active', 'ending')",
                    (seq, run_id),
                )
                conn.execute(
                    "UPDATE reproduce_personas SET state = 'idle', updated_seq = ? WHERE run_id = ?",
                    (seq, run_id),
                )

            self.store.update_run_status(
                run_id,
                status="failed",
                report_status="failed" if meta.get("report_status") == "generating" else None,
            )
            ev = self.store.append_event(
                run_id,
                event_type="run.failed",
                actor_id="persona_main",
                target_ids=["persona_main"],
                subject=EventSubject(kind="run", id=run_id),
                status="failed",
                summary="Run cancelled by user.",
                payload={"reason": "cancelled_by_user", "recoverable": True},
                mode=mode,
                source="user",
                state_mutator=_cancel_mut,
            )
            self.store.add_chat_message(
                run_id,
                kind="system",
                role="system",
                text="Run stopped by user.",
                related_refs=[SelectedContext(kind="agent", id="persona_main")],
                seq=ev.seq,
            )
        return True

    async def handle_chat(self, run_id: str, cmd: ChatCommand) -> tuple[int, ChatResponse]:
        cached = self.store.get_idempotent_command(run_id, cmd.request_id)
        if cached is not None:
            http_code = 200 if cached.status == "answered" else 202
            return http_code, cached

        run_meta = self.store.get_run_meta(run_id)
        if not run_meta:
            raise KeyError(f"Run '{run_id}' not found")

        text = cmd.text.strip()

        # 0. Human-in-the-loop Debate Intervention reply wakes up the waiting Coordinator immediately
        if self.coordinator.has_pending_intervention(run_id):
            pending = self.coordinator.get_pending_intervention(run_id) or {}
            from_ids = list(pending.get("from_persona_ids") or ["persona_validation", "persona_analysis"])
            debate_id = str(pending.get("debate_id") or "")
            collab_id = str(pending.get("collaboration_id") or "")
            int_id = str(pending.get("intervention_id") or "")

            def _commit_intervention_reply(conn: sqlite3.Connection, _command_id: str) -> HarnessEvent:
                refs = [cmd.selected_context] if cmd.selected_context else []
                self.store.add_chat_message(
                    run_id,
                    kind="message",
                    role="user",
                    text=text,
                    related_refs=[SelectedContext(kind="agent", id="persona_main"), *refs],
                    artifact_ids=cmd.attachment_ids,
                    conn=conn,
                )
                conn.execute(
                    "UPDATE reproduce_runs SET status = 'running', updated_at = ? WHERE run_id = ?",
                    (utc_now_iso(), run_id),
                )
                self.store.append_event_in_tx(
                    conn,
                    run_id,
                    event_type="debate.human_intervention_resolved",
                    actor_id="persona_main",
                    target_ids=from_ids,
                    subject=EventSubject(kind="debate", id=debate_id or run_id),
                    status="resolved",
                    summary=f"Main relayed human decision to debate team: {text[:120]}",
                    payload={
                        "intervention_id": int_id,
                        "debate_id": debate_id,
                        "collaboration_id": collab_id,
                        "user_decision": text,
                    },
                    source="user",
                )
                ev_turn = self.store.append_event_in_tx(
                    conn,
                    run_id,
                    event_type="debate.turn_added",
                    actor_id="persona_main",
                    target_ids=from_ids,
                    subject=EventSubject(kind="debate", id=debate_id or run_id),
                    status="active",
                    summary=f"Main relayed human decision: {text}",
                    payload={
                        "debate_id": debate_id,
                        "claim_id": "claim_robustness_validation",
                        "stance": "human_guidance",
                        "public_summary": f"Human investigator guidance relayed by Main: {text}",
                    },
                    source="user",
                )
                return ev_turn

            resp, was_dup, _ = self.store.record_command_atomic(
                run_id=run_id,
                request_id=cmd.request_id,
                text=text,
                selected_context=cmd.selected_context,
                attachment_ids=cmd.attachment_ids,
                status="accepted",
                message="Main Agent relayed your decision to the debating specialists; Round 2 experiments are resuming.",
                mutate_fn=_commit_intervention_reply,
            )
            if not was_dup:
                self.coordinator.resolve_human_intervention(run_id, text)
            return 202, resp

        # 1. Reject over-budget explicit requests (e.g., asking for >20% exploration budget)
        if re.search(r"(?:50%|60%|80%|100%|无限预算|unlimited budget)", text, flags=re.IGNORECASE):
            msg_req = (
                "Requested exploration allocation exceeds Harness `exploration_budget_fraction = 20%` cap. "
                "Please confirm within <=20% exploration budget so the primary reproduction is protected."
            )
            resp, _, _ = self.store.record_command_atomic(
                run_id=run_id,
                request_id=cmd.request_id,
                text=text,
                selected_context=cmd.selected_context,
                attachment_ids=cmd.attachment_ids,
                status="requires_approval",
                message=msg_req,
                reason_code="EXPLORATION_BUDGET_CAP",
            )
            return 202, resp

        snap = self.store.get_snapshot(run_id)
        events = self.store.list_events(run_id, after=0)
        has_existing_results = bool(snap and (snap.evidence or snap.debates))
        has_prior_run = run_meta["status"] in ("running", "paused", "completed", "failed") or len(events) > 0

        # 2. Follow-up execution triggers vs conversational follow-up Q&A with Main Agent
        is_dynamic_spawn_followup = bool(
            has_existing_results
            and run_meta["status"] in ("completed", "failed")
            and _DYNAMIC_SPAWN_RE.search(text)
            and not _INQUIRY_HINT_RE.search(text)
        )
        is_roundtable_followup = bool(
            not is_dynamic_spawn_followup
            and has_existing_results
            and run_meta["status"] in ("completed", "failed")
            and _ROUNDTABLE_RE.search(text)
            and not _INQUIRY_HINT_RE.search(text)
        )
        is_explicit_rerun = bool(
            (cmd.attachment_ids or _EXPLICIT_RERUN_RE.search(text))
            and not _INQUIRY_HINT_RE.search(text)
        )

        if has_prior_run and not (
            is_dynamic_spawn_followup or is_roundtable_followup or is_explicit_rerun
        ):
            answer = await self._answer_followup_question(
                run_id=run_id,
                run_meta=run_meta,
                cmd=cmd,
                snap=snap,
                events=events,
            )

            def _record_inquiry(conn: sqlite3.Connection, _cid: str) -> HarnessEvent | None:
                refs = [cmd.selected_context] if cmd.selected_context else []
                self.store.add_chat_message(
                    run_id,
                    kind="message",
                    role="user",
                    text=text,
                    related_refs=refs,
                    artifact_ids=cmd.attachment_ids,
                    conn=conn,
                )
                self.store.add_chat_message(
                    run_id,
                    kind="message",
                    role="main",
                    text=answer,
                    related_refs=[SelectedContext(kind="agent", id="persona_main"), *refs],
                    conn=conn,
                )
                return None

            resp, _, _ = self.store.record_command_atomic(
                run_id=run_id,
                request_id=cmd.request_id,
                text=text,
                selected_context=cmd.selected_context,
                attachment_ids=cmd.attachment_ids,
                status="answered",
                message=answer,
                mutate_fn=_record_inquiry,
            )
            return 200, resp

        def _commit_execution(conn: sqlite3.Connection, command_id: str) -> HarnessEvent:
            refs = [cmd.selected_context] if cmd.selected_context else []
            self.store.add_chat_message(
                run_id,
                kind="message",
                role="user",
                text=text,
                related_refs=refs,
                artifact_ids=cmd.attachment_ids,
                conn=conn,
            )
            conn.execute(
                "UPDATE reproduce_runs SET status = 'running' WHERE run_id = ?",
                (run_id,),
            )
            ev = self.store.append_event_in_tx(
                conn,
                run_id,
                event_type="run.started",
                actor_id="persona_main",
                target_ids=["persona_main"],
                subject=EventSubject(kind="run", id=run_id),
                status="running",
                summary=f"Main Agent accepted command ({command_id}): {text[:90]}",
                payload={
                    "command_id": command_id,
                    "selected_context": cmd.selected_context.model_dump() if cmd.selected_context else None,
                    "attachment_ids": cmd.attachment_ids,
                },
                source="user",
            )
            return ev

        if is_dynamic_spawn_followup:
            ack_msg = "Main Agent accepted the dynamic multi-agent scaling request and is spawning specialist instances."
        elif is_roundtable_followup:
            ack_msg = "Main Agent accepted the methodological review request and is convening the research team."
        else:
            ack_msg = "Main Agent accepted the reproduction plan and dispatched Paper, Data, Analysis, and Validation under Scheduler governance."

        resp, was_dup, _ = self.store.record_command_atomic(
            run_id=run_id,
            request_id=cmd.request_id,
            text=text,
            selected_context=cmd.selected_context,
            attachment_ids=cmd.attachment_ids,
            status="accepted",
            message=ack_msg,
            mutate_fn=_commit_execution,
        )

        if not was_dup:
            if is_dynamic_spawn_followup:
                role_counts = self._infer_dynamic_spawn_counts(text)
                self._spawn_bg(
                    run_id,
                    self.coordinator.execute_dynamic_spawn_followup(
                        run_id,
                        user_prompt=text,
                        role_counts=role_counts,
                    ),
                )
            elif is_roundtable_followup:
                participants = self._infer_roundtable_members(text)
                self._spawn_bg(
                    run_id,
                    self.coordinator.execute_team_roundtable_check(
                        run_id,
                        user_prompt=text,
                        participant_ids=participants,
                    ),
                )
            else:
                self._spawn_bg(
                    run_id,
                    self.coordinator.execute_reproduction_plan(
                        run_id,
                        user_prompt=text,
                        attachment_ids=cmd.attachment_ids,
                    ),
                )

        return 202, resp

    @staticmethod
    def _infer_dynamic_spawn_counts(text: str) -> dict[str, int]:
        aliases = {
            "paper": ("paper", "论文"),
            "data": ("data", "数据"),
            "analysis": ("analysis", "分析"),
            "validation": ("validation", "验证", "检验"),
            "support": ("support", "支持", "报告"),
        }
        counts: dict[str, int] = {}
        num_match = re.search(r"(\d+)\s*(?:个|名)?", text)
        default_n = min(4, max(2, int(num_match.group(1)))) if num_match else 2
        for role, kws in aliases.items():
            if any(k in text.lower() for k in kws):
                counts[role] = default_n
        if not counts:
            counts["analysis"] = 2
        return counts

    @staticmethod
    def _infer_roundtable_members(text: str) -> list[str]:
        low = text.lower()
        if any(k in low for k in ("五人", "全员", "all", "5", "ellip", "椭圆")):
            return [
                "persona_paper",
                "persona_data",
                "persona_analysis",
                "persona_validation",
                "persona_support",
            ]
        matched: list[str] = []
        for role in ("paper", "data", "analysis", "validation", "support"):
            if role in low:
                matched.append(f"persona_{role}")
        if len(matched) >= 2:
            return matched
        return ["persona_analysis", "persona_validation", "persona_data"]

    async def _answer_followup_question(
        self,
        *,
        run_id: str,
        run_meta: dict[str, Any],
        cmd: ChatCommand,
        snap: Any,
        events: list[HarnessEvent],
    ) -> str:
        text = cmd.text.strip()
        msg_page = self.store.list_messages(run_id, limit=20)
        failure_notes = [
            ev.summary
            for ev in reversed(events)
            if ev.type in ("run.failed", "task.failed") and ev.summary
        ]
        sys_notes = [m.text for m in reversed(msg_page.messages) if m.role == "system" and m.text]
        recent_turns = [
            f"{m.role}: {m.text[:240]}"
            for m in msg_page.messages[-6:]
            if m.role in ("user", "main")
        ]

        ev_items = (snap.evidence if snap else []) or []
        debates = (snap.debates if snap else []) or []
        ctx_desc = f"{cmd.selected_context.kind}:{cmd.selected_context.id}" if cmd.selected_context else "None"

        if run_meta.get("mode") == "live":
            try:
                sdk = SDKAdapter(self.coordinator.artifacts.run_dir(run_id))
                if sdk.detect_live_capability()[0]:
                    digest_lines = [
                        f"Run Title: {run_meta.get('title') or run_id}",
                        f"Run Status: {run_meta.get('status')}",
                        f"Selected Context: {ctx_desc}",
                    ]
                    if failure_notes or sys_notes:
                        digest_lines.append(
                            "Failure / System Notes: " + " | ".join((failure_notes + sys_notes)[:3])
                        )
                    if debates:
                        d = debates[-1]
                        digest_lines.append(
                            f"Latest Debate ({d.get('status')}): {d.get('claim')} -> {d.get('resolution_summary') or 'in review'}"
                        )
                    if ev_items:
                        ev = ev_items[-1]
                        gains = (ev.get("details") or {}).get("research_gains") or {}
                        verified = gains.get("verified_insights") or []
                        open_q = gains.get("open_questions") or []
                        if verified:
                            digest_lines.append("Verified Findings: " + "; ".join(verified[:2]))
                        if open_q:
                            digest_lines.append("Open Boundaries: " + "; ".join(open_q[:2]))
                    if recent_turns:
                        digest_lines.append("Recent Chat:\n" + "\n".join(recent_turns))

                    sys_prompt = (
                        "You are Main Agent of Libra Research answering a follow-up question from the investigator. "
                        "Answer strictly in Chinese if the user writes in Chinese (otherwise English), grounded in the run state below. "
                        "Be concise, factual, and direct; if the run failed or stopped, clearly explain the failure/stop reason and how they can ask follow-up questions or send '重新复现' to rerun."
                    )
                    res = await sdk.run_controlled_science_turn(
                        mode="live",
                        role="main",
                        system_prompt=sys_prompt,
                        user_prompt=f"{chr(10).join(digest_lines)}\n\nUser Question: {text}",
                        tools_spec=[],
                        handlers={},
                        max_turns=1,
                        timeout_seconds=30.0,
                        effort=None,
                    )
                    if res.ok and res.public_summary.strip():
                        return res.public_summary.strip()
            except Exception:
                pass

        return self._synthesize_diagnostic_or_summary_answer(
            text=text,
            selected_ctx=cmd.selected_context,
            run_meta=run_meta,
            snap=snap,
            failure_notes=failure_notes,
            sys_notes=sys_notes,
        )

    @staticmethod
    def _synthesize_diagnostic_or_summary_answer(
        *,
        text: str,
        selected_ctx: SelectedContext | None,
        run_meta: dict[str, Any],
        snap: Any,
        failure_notes: list[str],
        sys_notes: list[str],
    ) -> str:
        is_zh = _wants_chinese(text)
        status = str(run_meta.get("status") or "unknown")
        is_failure_q = status == "failed" or bool(
            re.search(r"(?:失败|报错|错误|停止|中断|怎么了|fail|error|stop)", text, flags=re.IGNORECASE)
        )
        reasons = [r for r in [*failure_notes, *sys_notes] if r]
        ctx_note = f" (`{selected_ctx.kind}:{selected_ctx.id}`)" if selected_ctx else ""

        if is_failure_q:
            reason_line = reasons[0] if reasons else (
                "未记录显式错误栈，可能因服务中断、配置异常或手动停止。"
                if is_zh
                else "No explicit stack trace recorded; the run may have been stopped manually or hit a runtime error."
            )
            if is_zh:
                return (
                    f"**Main Agent 诊断说明{ctx_note}**\n\n"
                    f"- **当前状态**：`{status}`\n"
                    f"- **原因记录**：{reason_line}\n"
                    "- **后续操作**：你可以继续直接向我追问，或发送 `重新复现` / 上传补充文件重新发起实验。"
                )
            return (
                f"**Main Agent Diagnostic Summary{ctx_note}**\n\n"
                f"- **Run status**: `{status}`\n"
                f"- **Recorded reason**: {reason_line}\n"
                "- **Next step**: Ask follow-up questions here or send `rerun` to start a fresh reproduction."
            )

        return MainAgentGateway._synthesize_readonly_answer(text, selected_ctx, snap)

    @staticmethod
    def _synthesize_readonly_answer(
        text: str,
        selected_ctx: SelectedContext | None,
        snap: Any,
    ) -> str:
        ev_items = (snap.evidence if snap else []) or []
        debates = (snap.debates if snap else []) or []
        ctx_note = f" (Context: `{selected_ctx.kind}:{selected_ctx.id}`)" if selected_ctx else ""
        parts = [f"**Main Agent Read-Only Summary{ctx_note}**:"]
        if debates:
            d = debates[-1]
            parts.append(
                f"- **Debate `{d.get('id')}` (`{d.get('status')}`)**: {d.get('claim')} — {d.get('resolution_summary') or 'In active review.'}"
            )
        if ev_items:
            ev = ev_items[-1]
            gains = (ev.get("details") or {}).get("research_gains") or {}
            verified = gains.get("verified_insights") or []
            open_q = gains.get("open_questions") or []
            if verified:
                parts.append(f"- **Verified Finding**: {verified[0]}")
            if open_q:
                parts.append(f"- **Open / Unresolved Boundary**: {open_q[0]}")
        else:
            at_seq = getattr(snap, "at_seq", 0) if snap else 0
            task_cnt = len(getattr(snap, "tasks", []) or []) if snap else 0
            parts.append(f"- Current run is at event sequence `{at_seq}` with `{task_cnt}` tracked task(s).")
        return "\n".join(parts)
