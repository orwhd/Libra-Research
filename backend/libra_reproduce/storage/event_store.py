from __future__ import annotations

import asyncio
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import DEFAULT_LIMITS
from ..harness.persona_catalog import build_initial_personas
from ..schemas import (
    CanvasProjection,
    ChatActivityMessage,
    ChatMessagePage,
    ChatResponse,
    Collaboration,
    EventSourceLiteral,
    EventSubject,
    HarnessEvent,
    Persona,
    ReportStatus,
    Run,
    RunMode,
    RunSnapshot,
    RunStatusLiteral,
    SelectedContext,
    SourceRef,
)
from ..security.tool_permissions import redact_secrets
from .db import get_reproduce_conn, jdumps, jloads, utc_now_iso


def _plain_summary(text: str | None, max_chars: int = 360) -> str:
    """Strip Markdown/LaTeX markers and normalize into complete plain text without ellipses."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"```[a-zA-Z0-9_-]*", " ", raw)
    raw = re.sub(r"^\s*#{1,6}\s+([^\n]+)", r"\1 — ", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s*>\s?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)
    raw = re.sub(r"(\*\*|__|~~|\*|`)", "", raw)
    raw = re.sub(r"\$([^$]{1,40})\$", r"\1", raw)
    collapsed = re.sub(r"\s+", " ", raw).strip()
    collapsed = re.sub(r"(?:\.{3,}|…)+\s*$", "", collapsed).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    window = collapsed[:max_chars]
    sentence_ends = [m.end() for m in re.finditer(r"[.!?。；;](?=\s|$)", window)]
    if sentence_ends and sentence_ends[-1] >= 36:
        return window[: sentence_ends[-1]].strip()
    last_space = window.rfind(" ")
    if last_space > 24:
        return window[:last_space].rstrip(" ,;:(-—")
    return window.rstrip(" ,;:(-—")


class EventSubscriberHub:
    """Per-run async SSE fan-out hub; only receives events AFTER SQLite commit."""

    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[HarnessEvent | None]]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue[HarnessEvent | None]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[HarnessEvent | None] = asyncio.Queue()
        self._subs.setdefault(run_id, []).append((loop, q))
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[HarnessEvent | None]) -> None:
        items = self._subs.get(run_id, [])
        self._subs[run_id] = [(lp, item_q) for lp, item_q in items if item_q is not q]

    def publish(self, run_id: str, event: HarnessEvent | None) -> None:
        for lp, q in list(self._subs.get(run_id, [])):
            if not lp.is_closed():
                lp.call_soon_threadsafe(q.put_nowait, event)


GLOBAL_EVENT_HUB = EventSubscriberHub()


class EventStore:
    """
    Append-only transactional EventStore + deterministic Snapshot/CanvasProjection builder.
    - Guarantees `UNIQUE(run_id, seq)` starting at `seq = 1`.
    - Guarantees `(run_id, request_id)` ChatCommand idempotency.
    - Guarantees `mode='simulation'` events can NEVER enter a `mode='live'` run.
    - Replays & snapshots NEVER invoke SDK models or experiment runners.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        hub: EventSubscriberHub | None = None,
    ) -> None:
        self.db_path = db_path
        self.hub = hub or GLOBAL_EVENT_HUB

    def create_run(
        self,
        *,
        title: str | None = None,
        paper_id: str = "paper_A",
        mode: RunMode = "live",
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> Run:
        rid = run_id or f"rep_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        clean_title = (title or "Paper Reproduction Session").strip() or "Paper Reproduction Session"
        initial_resources = {
            "limits": {
                "max_active_agent_tasks": DEFAULT_LIMITS.max_active_agent_tasks,
                "max_parallel_experiments": DEFAULT_LIMITS.max_parallel_experiments,
                "max_subagent_depth": DEFAULT_LIMITS.max_subagent_depth,
                "max_debate_turns": DEFAULT_LIMITS.max_debate_turns,
                "exploration_budget_fraction": DEFAULT_LIMITS.exploration_budget_fraction,
            },
            "active_agent_tasks": 0,
            "queued_agent_tasks": 0,
            "active_experiments": 0,
            "queued_experiments": 0,
            "exploration_used_fraction": 0.0,
        }
        personas = build_initial_personas()

        with get_reproduce_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO reproduce_runs (
                    run_id, title, paper_id, status, mode, workspace_id,
                    report_status, report_artifact_ids_json, missing_requirements_json,
                    resources_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'created', ?, ?, 'not_started', '[]', ?, ?, ?, ?)
                """,
                (
                    rid,
                    clean_title,
                    paper_id,
                    mode,
                    workspace_id,
                    jdumps(["verified_dataset", "executed_analysis", "evidence_gate_review", "report_file"]),
                    jdumps(initial_resources),
                    now,
                    now,
                ),
            )
            for p in personas:
                conn.execute(
                    """
                    INSERT INTO reproduce_personas (
                        run_id, persona_id, role, label, runtime_agent_id,
                        state, is_main, task_ids_json, updated_seq
                    ) VALUES (?, ?, ?, ?, NULL, 'idle', ?, '[]', 0)
                    """,
                    (rid, p.persona_id, p.role, p.label, 1 if p.is_main else 0),
                )

        return Run(
            run_id=rid,
            status="created",
            created_at=now,
            mode=mode,
            title=clean_title,
        )

    def get_run(self, run_id: str) -> Run | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return Run(
            run_id=row["run_id"],
            status=row["status"],
            created_at=row["created_at"],
            mode=row["mode"],
            title=row["title"],
        )

    def get_run_meta(self, run_id: str) -> dict[str, Any] | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def recover_orphaned_running_runs(self) -> list[str]:
        """
        Mark orphaned `status='running'` runs left behind by a prior backend restart as
        interrupted (`status='failed'`) so the UI unlocks and users can cancel/retry cleanly.
        """
        with get_reproduce_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT run_id, mode FROM reproduce_runs WHERE status IN ('running', 'paused')"
            ).fetchall()

        recovered: list[str] = []
        now = utc_now_iso()
        for row in rows:
            rid = str(row["run_id"])
            mode: RunMode = row["mode"]

            def _cleanup(conn: sqlite3.Connection, seq: int) -> None:
                conn.execute(
                    "UPDATE reproduce_collaborations SET status = 'ended', last_event_seq = ? WHERE run_id = ? AND status IN ('active', 'ending')",
                    (seq, rid),
                )
                conn.execute(
                    "UPDATE reproduce_personas SET state = 'idle', updated_seq = ? WHERE run_id = ?",
                    (seq, rid),
                )
                conn.execute(
                    "UPDATE reproduce_runs SET status = 'failed', report_status = CASE WHEN report_status = 'generating' THEN 'failed' ELSE report_status END, updated_at = ? WHERE run_id = ?",
                    (now, rid),
                )

            ev = self.append_event(
                rid,
                event_type="run.failed",
                actor_id="persona_main",
                target_ids=["persona_main"],
                subject=EventSubject(kind="run", id=rid),
                status="failed",
                summary="Run interrupted by backend restart; ready to retry.",
                payload={"reason": "backend_restart", "recoverable": True},
                mode=mode,
                source="harness",
                state_mutator=_cleanup,
            )
            self.add_chat_message(
                rid,
                kind="system",
                role="system",
                text="Previous run was interrupted by a backend restart. You can submit a message to start a fresh run.",
                related_refs=[SelectedContext(kind="agent", id="persona_main")],
                seq=ev.seq,
            )
            recovered.append(rid)
        return recovered

    def list_persona_ids(self, run_id: str) -> list[str]:
        """Return all persisted persona IDs (baseline + dynamically spawned) for a run."""
        with get_reproduce_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT persona_id FROM reproduce_personas WHERE run_id = ? ORDER BY rowid ASC",
                (run_id,),
            ).fetchall()
        return [str(r["persona_id"]) for r in rows]

    def get_next_role_instance_index(self, run_id: str, role: str) -> int:
        """Return the next unused >=2 numeric suffix for dynamic spawning within `role`."""
        existing = self.list_persona_ids(run_id)
        prefix = f"persona_{role}_"
        max_idx = 1
        for pid in existing:
            if pid == f"persona_{role}":
                max_idx = max(max_idx, 1)
            elif pid.startswith(prefix):
                tail = pid[len(prefix) :]
                if tail.isdigit():
                    max_idx = max(max_idx, int(tail))
        return max_idx + 1

    def get_or_create_workspace_run(
        self,
        workspace_id: str,
        *,
        title: str | None = None,
        mode: RunMode = "live",
    ) -> Run:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        if row:
            return Run(
                run_id=row["run_id"],
                status=row["status"],
                created_at=row["created_at"],
                mode=row["mode"],
                title=row["title"],
            )
        return self.create_run(title=title, paper_id="paper_A", mode=mode, workspace_id=workspace_id)

    def get_idempotent_command(self, run_id: str, request_id: str) -> ChatResponse | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT response_json FROM reproduce_commands WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            ).fetchone()
        if not row:
            return None
        return ChatResponse.model_validate(jloads(row["response_json"], {}))

    def record_command_atomic(
        self,
        *,
        run_id: str,
        request_id: str,
        text: str,
        selected_context: SelectedContext | None,
        attachment_ids: list[str],
        status: Literal_status,
        message: str,
        reason_code: str | None = None,
        mutate_fn: Callable[[sqlite3.Connection, str], HarnessEvent | None] | None = None,
    ) -> tuple[ChatResponse, bool, HarnessEvent | None]:
        """
        Atomically check `(run_id, request_id)` idempotency; if new, execute optional domain
        mutation + event append in the SAME SQLite transaction, commit, then broadcast SSE.
        Returns `(ChatResponse, was_duplicate, committed_event)`.
        """
        command_id = f"cmd_{uuid.uuid4().hex[:12]}"
        emitted: HarnessEvent | None = None

        with get_reproduce_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT response_json FROM reproduce_commands WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            ).fetchone()
            if existing:
                return ChatResponse.model_validate(jloads(existing["response_json"], {})), True, None

            if mutate_fn is not None:
                emitted = mutate_fn(conn, command_id)

            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM reproduce_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            last_seq = int(seq_row["max_seq"]) if seq_row else 0

            resp = ChatResponse(
                command_id=command_id,
                status=status,
                message=message,
                reason_code=reason_code,
                last_seq=last_seq,
            )
            conn.execute(
                """
                INSERT INTO reproduce_commands (
                    command_id, run_id, request_id, text, selected_context_json,
                    attachment_ids_json, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    run_id,
                    request_id,
                    text,
                    selected_context.model_dump_json() if selected_context else None,
                    jdumps(attachment_ids),
                    resp.model_dump_json(),
                    utc_now_iso(),
                ),
            )

        if emitted is not None:
            self.hub.publish(run_id, emitted)
        return resp, False, emitted

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        summary: str,
        actor_id: str | None = None,
        target_ids: list[str] | None = None,
        subject: EventSubject | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        artifact_ids: list[str] | None = None,
        mode: RunMode | None = None,
        source: EventSourceLiteral = "harness",
        state_mutator: Callable[[sqlite3.Connection, int], None] | None = None,
    ) -> HarnessEvent:
        """
        Append one `HarnessEvent` and run `state_mutator(conn, next_seq)` inside a single
        SQLite transaction; broadcast to SSE subscribers strictly after commit.
        """
        with get_reproduce_conn(self.db_path) as conn:
            ev = self.append_event_in_tx(
                conn,
                run_id,
                event_type=event_type,
                summary=summary,
                actor_id=actor_id,
                target_ids=target_ids,
                subject=subject,
                status=status,
                payload=payload,
                evidence_ids=evidence_ids,
                artifact_ids=artifact_ids,
                mode=mode,
                source=source,
                state_mutator=state_mutator,
            )
        self.hub.publish(run_id, ev)
        if event_type in ("run.completed", "run.failed"):
            self.hub.publish(run_id, None)
        return ev

    def append_event_in_tx(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        event_type: str,
        summary: str,
        actor_id: str | None = None,
        target_ids: list[str] | None = None,
        subject: EventSubject | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        artifact_ids: list[str] | None = None,
        mode: RunMode | None = None,
        source: EventSourceLiteral = "harness",
        state_mutator: Callable[[sqlite3.Connection, int], None] | None = None,
    ) -> HarnessEvent:
        run_row = conn.execute(
            "SELECT mode FROM reproduce_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not run_row:
            raise KeyError(f"Run '{run_id}' does not exist.")
        run_mode: RunMode = run_row["mode"]
        effective_mode: RunMode = mode or run_mode
        if run_mode == "live" and effective_mode == "simulation":
            raise ValueError("Refusing to mix mode='simulation' event into a LIVE scientific run.")
        if run_mode == "simulation" and effective_mode == "live":
            raise ValueError("Refusing to label event 'live' inside a SIMULATION run.")

        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM reproduce_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        next_seq = int(seq_row["max_seq"]) + 1
        now = utc_now_iso()
        clean_payload = redact_secrets(payload or {})
        dedup_targets = list(dict.fromkeys(target_ids or []))
        dedup_ev = list(dict.fromkeys(evidence_ids or []))
        dedup_art = list(dict.fromkeys(artifact_ids or []))

        if state_mutator is not None:
            state_mutator(conn, next_seq)

        ev = HarnessEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            seq=next_seq,
            occurred_at=now,
            type=event_type,
            actor_id=actor_id,
            target_ids=dedup_targets,
            subject=subject,
            status=status,
            summary=str(redact_secrets(summary)),
            payload=clean_payload,
            evidence_ids=dedup_ev,
            artifact_ids=dedup_art,
            mode=effective_mode,
            source=source,
        )
        conn.execute(
            """
            INSERT INTO reproduce_events (
                run_id, seq, event_id, occurred_at, type, actor_id, target_ids_json,
                subject_json, status, summary, payload_json, evidence_ids_json,
                artifact_ids_json, mode, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.run_id,
                ev.seq,
                ev.event_id,
                ev.occurred_at,
                ev.type,
                ev.actor_id,
                jdumps(ev.target_ids),
                ev.subject.model_dump_json() if ev.subject else None,
                ev.status,
                ev.summary,
                jdumps(ev.payload),
                jdumps(ev.evidence_ids),
                jdumps(ev.artifact_ids),
                ev.mode,
                ev.source,
            ),
        )
        conn.execute(
            "UPDATE reproduce_runs SET updated_at = ? WHERE run_id = ?",
            (now, run_id),
        )
        return ev

    def add_chat_message(
        self,
        run_id: str,
        *,
        kind: str,
        role: str,
        text: str,
        related_refs: list[SelectedContext] | None = None,
        artifact_ids: list[str] | None = None,
        seq: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> ChatActivityMessage:
        msg = ChatActivityMessage(
            message_id=f"msg_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            kind=kind,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            created_at=utc_now_iso(),
            text=str(redact_secrets(text)),
            related_refs=related_refs or [],
            artifact_ids=list(dict.fromkeys(artifact_ids or [])),
            seq=seq,
        )

        def _insert(c: sqlite3.Connection) -> None:
            c.execute(
                """
                INSERT INTO reproduce_messages (
                    message_id, run_id, kind, role, created_at, text,
                    related_refs_json, artifact_ids_json, seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.message_id,
                    msg.run_id,
                    msg.kind,
                    msg.role,
                    msg.created_at,
                    msg.text,
                    jdumps([r.model_dump() for r in msg.related_refs]),
                    jdumps(msg.artifact_ids),
                    msg.seq,
                ),
            )

        if conn is not None:
            _insert(conn)
        else:
            with get_reproduce_conn(self.db_path) as c:
                _insert(c)
        return msg

    def list_events(self, run_id: str, after: int = 0, up_to: int | None = None) -> list[HarnessEvent]:
        with get_reproduce_conn(self.db_path) as conn:
            if up_to is not None:
                rows = conn.execute(
                    "SELECT * FROM reproduce_events WHERE run_id = ? AND seq > ? AND seq <= ? ORDER BY seq ASC",
                    (run_id, after, up_to),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reproduce_events WHERE run_id = ? AND seq > ? ORDER BY seq ASC",
                    (run_id, after),
                ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def list_messages(
        self,
        run_id: str,
        *,
        ref_kind: str | None = None,
        ref_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ChatMessagePage:
        start_row = int(cursor) if (cursor and cursor.isdigit()) else 0
        with get_reproduce_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM reproduce_messages WHERE run_id = ? AND row_id > ? ORDER BY row_id ASC",
                (run_id, start_row),
            ).fetchall()

        matched: list[tuple[int, ChatActivityMessage]] = []
        for r in rows:
            refs_raw = jloads(r["related_refs_json"], [])
            refs = [SelectedContext.model_validate(x) for x in refs_raw]
            art_ids: list[str] = jloads(r["artifact_ids_json"], [])
            if ref_kind and ref_id:
                hit = any(rf.kind == ref_kind and rf.id == ref_id for rf in refs)
                if not hit and ref_kind == "artifact" and ref_id in art_ids:
                    hit = True
                if not hit:
                    continue
            elif ref_kind:
                if not any(rf.kind == ref_kind for rf in refs):
                    continue
            elif ref_id:
                if not (any(rf.id == ref_id for rf in refs) or ref_id in art_ids):
                    continue

            matched.append(
                (
                    int(r["row_id"]),
                    ChatActivityMessage(
                        message_id=r["message_id"],
                        run_id=r["run_id"],
                        kind=r["kind"],
                        role=r["role"],
                        created_at=r["created_at"],
                        text=r["text"],
                        related_refs=refs,
                        artifact_ids=art_ids,
                        seq=r["seq"],
                    ),
                )
            )
            if len(matched) > limit:
                break

        has_more = len(matched) > limit
        page_slice = matched[:limit]
        next_cursor = str(page_slice[-1][0]) if (has_more and page_slice) else None
        return ChatMessagePage(
            messages=[m for _, m in page_slice],
            next_cursor=next_cursor,
        )

    def get_report_status(self, run_id: str) -> ReportStatus | None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT report_status, report_artifact_ids_json, missing_requirements_json "
                "FROM reproduce_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return ReportStatus(
            status=row["report_status"],
            artifact_ids=jloads(row["report_artifact_ids_json"], []),
            missing_requirements=jloads(row["missing_requirements_json"], []),
        )

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatusLiteral | None = None,
        report_status: str | None = None,
        report_artifact_ids: list[str] | None = None,
        missing_requirements: list[str] | None = None,
        resources: dict[str, Any] | None = None,
    ) -> None:
        with get_reproduce_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM reproduce_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE reproduce_runs SET
                    status = ?,
                    report_status = ?,
                    report_artifact_ids_json = ?,
                    missing_requirements_json = ?,
                    resources_json = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status or row["status"],
                    report_status or row["report_status"],
                    jdumps(report_artifact_ids) if report_artifact_ids is not None else row["report_artifact_ids_json"],
                    jdumps(missing_requirements) if missing_requirements is not None else row["missing_requirements_json"],
                    jdumps(resources) if resources is not None else row["resources_json"],
                    utc_now_iso(),
                    run_id,
                ),
            )

    def get_snapshot(self, run_id: str, at_seq: int | None = None) -> RunSnapshot | None:
        """
        Pure read-only projection.
        - If `at_seq` is None or >= latest seq, reads current materialized state.
        - If `at_seq` < latest seq, deterministically folds events `1..at_seq` in memory
          WITHOUT calling any LLM, SDK, or subprocess tool!
        """
        with get_reproduce_conn(self.db_path) as conn:
            run_row = conn.execute(
                "SELECT * FROM reproduce_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run_row:
                return None
            max_seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM reproduce_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            latest_seq = int(max_seq_row["max_seq"]) if max_seq_row else 0

            if at_seq is not None and at_seq < latest_seq:
                ev_rows = conn.execute(
                    "SELECT * FROM reproduce_events WHERE run_id = ? AND seq <= ? ORDER BY seq ASC",
                    (run_id, at_seq),
                ).fetchall()
                events = [self._row_to_event(r) for r in ev_rows]
                return self._fold_historical_snapshot(run_id, run_row["mode"], at_seq, events)

            p_rows = conn.execute(
                "SELECT * FROM reproduce_personas WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
            ).fetchall()
            t_rows = conn.execute(
                "SELECT * FROM reproduce_tasks WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            ).fetchall()
            d_rows = conn.execute(
                "SELECT * FROM reproduce_debates WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            ).fetchall()
            pod_rows = conn.execute(
                "SELECT * FROM reproduce_pods WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            ).fetchall()
            c_rows = conn.execute(
                "SELECT * FROM reproduce_collaborations WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
            ).fetchall()
            evid_rows = conn.execute(
                "SELECT * FROM reproduce_evidence WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            ).fetchall()

        personas = [
            Persona(
                persona_id=r["persona_id"],
                role=r["role"],
                label=r["label"],
                runtime_agent_id=r["runtime_agent_id"],
                state=r["state"],
                is_main=bool(r["is_main"]),
                task_ids=jloads(r["task_ids_json"], []),
            )
            for r in p_rows
        ]
        active_collabs = [
            Collaboration(
                collaboration_id=r["collaboration_id"],
                kind=r["kind"],
                status=r["status"],
                topic=r["topic"],
                participant_persona_ids=jloads(r["participant_persona_ids_json"], []),
                ref=SourceRef.model_validate(jloads(r["ref_json"], {"kind": "collaboration", "id": r["collaboration_id"]})),
                last_event_seq=int(r["last_event_seq"]),
            )
            for r in c_rows
            if r["status"] in ("active", "ending")
        ]
        canvas = CanvasProjection(
            run_id=run_id,
            at_seq=latest_seq,
            mode=run_row["mode"],
            personas=personas,
            collaborations=active_collabs,
        )
        return RunSnapshot(
            run_id=run_id,
            at_seq=latest_seq,
            agents=[p.model_dump() for p in personas],
            tasks=[
                {
                    "id": r["task_id"],
                    "persona_id": r["persona_id"],
                    "role": r["role"],
                    "objective": r["objective"],
                    "status": r["status"],
                    "is_exploration": bool(r["is_exploration"]),
                    "artifact_ids": jloads(r["artifact_ids_json"], []),
                }
                for r in t_rows
            ],
            debates=[
                {
                    "id": r["debate_id"],
                    "claim_id": r["claim_id"],
                    "claim": r["claim"],
                    "participants": jloads(r["participants_json"], []),
                    "turns": jloads(r["turns_json"], []),
                    "proposed_experiments": jloads(r["proposed_experiments_json"], []),
                    "status": r["status"],
                    "resolution_summary": r["resolution_summary"],
                }
                for r in d_rows
            ],
            pods=[
                {
                    "id": r["pod_id"],
                    "research_question": r["research_question"],
                    "members": jloads(r["members_json"], []),
                    "budget_fraction": r["budget_fraction"],
                    "status": r["status"],
                    "outcome_summary": r["outcome_summary"],
                }
                for r in pod_rows
            ],
            evidence=[
                {
                    "id": r["evidence_id"],
                    "claim_id": r["claim_id"],
                    "dataset_manifest_id": r["dataset_manifest_id"],
                    "script_hash": r["script_hash"],
                    "result_artifact_ids": jloads(r["result_artifact_ids_json"], []),
                    "status": r["status"],
                    "reviewer_id": r["reviewer_id"],
                    "details": jloads(r["details_json"], {}),
                }
                for r in evid_rows
            ],
            resources=jloads(run_row["resources_json"], {}),
            canvas=canvas,
        )

    def _fold_historical_snapshot(
        self,
        run_id: str,
        mode: RunMode,
        target_seq: int,
        events: list[HarnessEvent],
    ) -> RunSnapshot:
        personas_map = {p.persona_id: p for p in build_initial_personas()}
        collabs_map: dict[str, tuple[int, Collaboration]] = {}
        tasks_map: dict[str, dict[str, Any]] = {}
        debates_map: dict[str, dict[str, Any]] = {}
        pods_map: dict[str, dict[str, Any]] = {}
        evidence_list: list[dict[str, Any]] = []

        for ev in events:
            pl = ev.payload
            if ev.type == "agent.spawned":
                pid = str(pl.get("persona_id") or "")
                rt_id = str(pl.get("runtime_agent_id") or ev.actor_id or "")
                if pid in personas_map:
                    personas_map[pid].runtime_agent_id = rt_id
                elif pid:
                    role_val = str(pl.get("role") or "support")
                    label_val = str(pl.get("label") or pid)
                    personas_map[pid] = Persona(
                        persona_id=pid,
                        role=role_val,
                        label=label_val,
                        runtime_agent_id=rt_id,
                        state="assigned",
                        is_main=bool(pl.get("is_main", False)),
                        task_ids=[],
                    )
            elif ev.type == "agent.status_changed":
                pid = str(pl.get("persona_id") or "")
                nxt = str(pl.get("next_state") or "idle")
                if pid in personas_map:
                    personas_map[pid].state = nxt  # type: ignore[assignment]
            elif ev.type == "task.created":
                tid = str(pl.get("task_id") or "")
                pid = str(pl.get("persona_id") or "")
                if tid:
                    tasks_map[tid] = {
                        "id": tid,
                        "persona_id": pid,
                        "role": str(pl.get("role") or ""),
                        "objective": str(pl.get("objective") or ev.summary),
                        "status": str(ev.status or "queued"),
                    }
                    if pid in personas_map and tid not in personas_map[pid].task_ids:
                        personas_map[pid].task_ids.append(tid)
            elif ev.type in ("task.started", "task.completed", "task.failed"):
                tid = str(pl.get("task_id") or (ev.subject.id if ev.subject else ""))
                if tid in tasks_map:
                    tasks_map[tid]["status"] = str(ev.status or ev.type.split(".")[-1])
            elif ev.type == "collaboration.started":
                cid = str(pl["collaboration_id"])
                rev = int(pl.get("revision", 1))
                sref = pl.get("source_ref") or {"kind": "collaboration", "id": cid}
                collabs_map[cid] = (
                    rev,
                    Collaboration(
                        collaboration_id=cid,
                        kind=pl.get("kind", "collaboration"),
                        status="active",
                        topic=str(pl.get("topic") or ev.summary),
                        participant_persona_ids=list(pl.get("participant_persona_ids") or []),
                        ref=SourceRef.model_validate(sref),
                        last_event_seq=ev.seq,
                    ),
                )
            elif ev.type == "collaboration.members_changed":
                cid = str(pl["collaboration_id"])
                rev = int(pl.get("revision", 2))
                if cid in collabs_map and rev > collabs_map[cid][0]:
                    prev_c = collabs_map[cid][1]
                    collabs_map[cid] = (
                        rev,
                        prev_c.model_copy(
                            update={
                                "participant_persona_ids": list(pl.get("participant_persona_ids") or []),
                                "last_event_seq": ev.seq,
                            }
                        ),
                    )
            elif ev.type == "collaboration.ended":
                cid = str(pl["collaboration_id"])
                collabs_map.pop(cid, None)
            elif ev.type == "debate.opened":
                did = str(pl.get("debate_id") or (ev.subject.id if ev.subject else ""))
                if did:
                    debates_map[did] = {"id": did, "claim": ev.summary, "status": "opened", "turns": []}
            elif ev.type == "debate.turn_added":
                did = str(pl.get("debate_id") or (ev.subject.id if ev.subject else ""))
                if did in debates_map:
                    debates_map[did]["turns"].append(pl)
                    debates_map[did]["status"] = "active"
            elif ev.type == "debate.closed":
                did = str(pl.get("debate_id") or (ev.subject.id if ev.subject else ""))
                if did in debates_map:
                    debates_map[did]["status"] = str(ev.status or "resolved")
            elif ev.type == "pod.started":
                pid_pod = str(pl.get("pod_id") or (ev.subject.id if ev.subject else ""))
                if pid_pod:
                    pods_map[pid_pod] = {"id": pid_pod, "research_question": ev.summary, "status": "active"}
            elif ev.type == "pod.closed":
                pid_pod = str(pl.get("pod_id") or (ev.subject.id if ev.subject else ""))
                if pid_pod in pods_map:
                    pods_map[pid_pod]["status"] = "closed"
            elif ev.type == "evidence.reviewed":
                evidence_list.append(
                    {"claim_id": pl.get("claim_id"), "status": pl.get("result"), "seq": ev.seq}
                )
            elif ev.type in ("run.completed", "run.failed"):
                collabs_map.clear()

        personas = list(personas_map.values())
        active_collabs = [c for _, c in collabs_map.values()]
        return RunSnapshot(
            run_id=run_id,
            at_seq=target_seq,
            agents=[p.model_dump() for p in personas],
            tasks=list(tasks_map.values()),
            debates=list(debates_map.values()),
            pods=list(pods_map.values()),
            evidence=evidence_list,
            resources={},
            canvas=CanvasProjection(
                run_id=run_id,
                at_seq=target_seq,
                mode=mode,
                personas=personas,
                collaborations=active_collabs,
            ),
        )

    @staticmethod
    def _row_to_event(r: Any) -> HarnessEvent:
        subj_raw = jloads(r["subject_json"], None)
        return HarnessEvent(
            event_id=r["event_id"],
            run_id=r["run_id"],
            seq=int(r["seq"]),
            occurred_at=r["occurred_at"],
            type=r["type"],
            actor_id=r["actor_id"],
            target_ids=jloads(r["target_ids_json"], []),
            subject=EventSubject.model_validate(subj_raw) if isinstance(subj_raw, dict) else None,
            status=r["status"],
            summary=r["summary"],
            payload=jloads(r["payload_json"], {}),
            evidence_ids=jloads(r["evidence_ids_json"], []),
            artifact_ids=jloads(r["artifact_ids_json"], []),
            mode=r["mode"],
            source=r["source"],
        )

    def build_run_timeline(self, run_id: str) -> dict[str, Any] | None:
        """
        Pure read-only timeline & collaboration dialogue projection:
        - `snapshots`: Milestone historical seqs (run start, team huddle start/expand/end with reasons & reports, run completion).
        - `table_dialogues`: Brief start-to-end dialogue turns keyed by collaboration_id.
        - `chat_steps`: Ordered left-aligned vertical spine nodes for Left Chat with per-step artifact_ids.
        """
        with get_reproduce_conn(self.db_path) as conn:
            run_row = conn.execute("SELECT * FROM reproduce_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not run_row:
                return None
            ev_rows = conn.execute(
                "SELECT * FROM reproduce_events WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
            p_rows = conn.execute(
                "SELECT persona_id, role, label FROM reproduce_personas WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            d_rows = conn.execute(
                "SELECT * FROM reproduce_debates WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
            art_rows = conn.execute(
                "SELECT artifact_id, kind, filename, metadata_json FROM reproduce_artifacts WHERE run_id = ?",
                (run_id,),
            ).fetchall()

        events = [self._row_to_event(r) for r in ev_rows]
        persona_labels: dict[str, str] = {str(r["persona_id"]): str(r["label"]) for r in p_rows}
        persona_roles: dict[str, str] = {str(r["persona_id"]): str(r["role"]) for r in p_rows}

        user_attachment_ids: set[str] = set()
        artifact_kind: dict[str, str] = {}
        artifact_name: dict[str, str] = {}
        for r in art_rows:
            aid = str(r["artifact_id"])
            meta = jloads(r["metadata_json"], {})
            artifact_kind[aid] = str(r["kind"])
            artifact_name[aid] = str(r["filename"])
            if meta.get("is_user_attachment"):
                user_attachment_ids.add(aid)

        debates_by_id: dict[str, dict[str, Any]] = {}
        for r in d_rows:
            debates_by_id[str(r["debate_id"])] = {
                "claim": str(r["claim"] or ""),
                "status": str(r["status"] or ""),
                "resolution_summary": str(r["resolution_summary"] or ""),
            }

        def _label(pid: str | None) -> str:
            if not pid:
                return "Agent"
            raw = persona_labels.get(pid)
            if pid == "persona_main" and (not raw or raw.strip().lower() in ("main", "main agent")):
                return "Boss"
            return raw or pid.replace("persona_", "").replace("_", " #").title()

        # 1. Extract collaboration windows & chronological start-to-end dialogues
        collab_order: list[str] = []
        collab_meta: dict[str, dict[str, Any]] = {}
        for ev in events:
            pl = ev.payload or {}
            if ev.type == "collaboration.started":
                cid = str(pl.get("collaboration_id") or "")
                if not cid:
                    continue
                if cid not in collab_meta:
                    collab_order.append(cid)
                sref = pl.get("source_ref") if isinstance(pl.get("source_ref"), dict) else {}
                ref_id = str(sref.get("id") or "")
                deb_info = debates_by_id.get(ref_id, {})
                start_reason = deb_info.get("claim") or str(pl.get("topic") or ev.summary)
                collab_meta[cid] = {
                    "collaboration_id": cid,
                    "kind": str(pl.get("kind") or "debate"),
                    "topic": _plain_summary(str(pl.get("topic") or ev.summary), 72),
                    "start_seq": ev.seq,
                    "end_seq": None,
                    "expand_events": [],
                    "participants": [_label(p) for p in (pl.get("participant_persona_ids") or []) if p != "persona_main"],
                    "start_reason": _plain_summary(start_reason, 180),
                    "end_report": _plain_summary(deb_info.get("resolution_summary") or "", 200),
                    "turns": [],
                }
            elif ev.type == "collaboration.members_changed":
                cid = str(pl.get("collaboration_id") or "")
                if cid in collab_meta:
                    members = [_label(p) for p in (pl.get("participant_persona_ids") or []) if p != "persona_main"]
                    collab_meta[cid]["participants"] = members
                    collab_meta[cid]["expand_events"].append(
                        {
                            "seq": ev.seq,
                            "members": members,
                            "reason": _plain_summary(str(pl.get("reason") or ev.summary), 160),
                        }
                    )
                    collab_meta[cid]["turns"].append(
                        {
                            "seq": ev.seq,
                            "speaker_id": ev.actor_id or "persona_main",
                            "speaker_label": f"{_label(ev.actor_id)} (Team Expanded)",
                            "stance": "expand",
                            "summary": str(pl.get("reason") or ev.summary),
                        }
                    )
            elif ev.type == "collaboration.ended":
                cid = str(pl.get("collaboration_id") or "")
                if cid in collab_meta:
                    collab_meta[cid]["end_seq"] = ev.seq
                    if not collab_meta[cid]["end_report"]:
                        collab_meta[cid]["end_report"] = _plain_summary(
                            str(pl.get("outcome") or ev.summary), 200
                        )
                    collab_meta[cid]["turns"].append(
                        {
                            "seq": ev.seq,
                            "speaker_id": ev.actor_id or "persona_validation",
                            "speaker_label": f"{_label(ev.actor_id)} (Conclusion)",
                            "stance": "resolved",
                            "summary": collab_meta[cid]["end_report"] or ev.summary,
                        }
                    )

        # Attach debate turns, human interventions & pod events into whichever collaboration window is active at ev.seq
        interventions_by_id: dict[str, dict[str, Any]] = {}
        intervention_order: list[str] = []
        main_turn_seqs = {
            e.seq
            for e in events
            if e.type == "debate.turn_added" and e.actor_id == "persona_main"
        }

        for ev in events:
            pl = ev.payload or {}
            if ev.type not in (
                "debate.turn_added",
                "pod.started",
                "debate.closed",
                "evidence.reviewed",
                "debate.human_intervention_requested",
                "debate.human_intervention_resolved",
            ):
                continue

            if ev.type == "debate.human_intervention_requested":
                int_id = str(pl.get("intervention_id") or f"int_{ev.seq}")
                cid_req = str(pl.get("collaboration_id") or "") or next(
                    (
                        cid
                        for cid in collab_order
                        if collab_meta[cid]["start_seq"] - 8 <= ev.seq
                        and (collab_meta[cid]["end_seq"] is None or ev.seq <= collab_meta[cid]["end_seq"] + 1)
                    ),
                    None,
                )
                from_ids = [str(p) for p in (pl.get("from_persona_ids") or ([ev.actor_id] if ev.actor_id else ["persona_validation"]))]
                from_labels = [_label(p) for p in from_ids]
                opts_raw = pl.get("options") or []
                opts = [
                    {"label": str(o.get("label") or ""), "prompt": str(o.get("prompt") or "")}
                    for o in opts_raw
                    if isinstance(o, dict) and o.get("label")
                ]
                interventions_by_id[int_id] = {
                    "intervention_id": int_id,
                    "seq": ev.seq,
                    "resolved_seq": None,
                    "status": "pending",
                    "debate_id": str(pl.get("debate_id") or ""),
                    "collaboration_id": cid_req,
                    "from_persona_ids": from_ids,
                    "from_labels": from_labels,
                    "to_persona_id": str(pl.get("to_persona_id") or "persona_main"),
                    "dissent_summary": str(pl.get("dissent_summary") or ev.summary),
                    "question": str(pl.get("question") or "Please confirm how the research team should proceed."),
                    "options": opts,
                    "user_decision": None,
                    "linked_step_ids": [f"step_debate_{cid_req}"] if cid_req else ["step_validation_gate"],
                }
                if int_id not in intervention_order:
                    intervention_order.append(int_id)
                if cid_req and cid_req in collab_meta:
                    collab_meta[cid_req]["turns"].append(
                        {
                            "seq": ev.seq,
                            "speaker_id": from_ids[0] if from_ids else "persona_validation",
                            "speaker_label": f"{', '.join(from_labels)} (Email → Boss)",
                            "stance": "escalate",
                            "summary": (
                                f"Sent intervention email to Boss: "
                                f"{str(pl.get('question') or 'Requesting human guidance on methodological dissent.')}"
                            ),
                        }
                    )
                continue

            if ev.type == "debate.human_intervention_resolved":
                int_id = str(pl.get("intervention_id") or (intervention_order[-1] if intervention_order else ""))
                rec = interventions_by_id.get(int_id)
                user_dec = str(pl.get("user_decision") or ev.summary)
                if rec:
                    rec["resolved_seq"] = ev.seq
                    rec["status"] = "resolved"
                    rec["user_decision"] = user_dec
                    cid_res = rec.get("collaboration_id")
                    if cid_res and cid_res in collab_meta:
                        has_main_turn_near = any(
                            abs(seq - ev.seq) <= 2 for seq in main_turn_seqs
                        ) or any(
                            t["speaker_id"] == "persona_main" and abs(t["seq"] - ev.seq) <= 2
                            for t in collab_meta[cid_res]["turns"]
                        )
                        if not has_main_turn_near:
                            collab_meta[cid_res]["turns"].append(
                                {
                                    "seq": ev.seq,
                                    "speaker_id": "persona_main",
                                    "speaker_label": _label("persona_main"),
                                    "stance": "human_guidance",
                                    "summary": f"Human guidance relayed by Boss: {user_dec}",
                                }
                            )
                continue

            target_cid = next(
                (
                    cid
                    for cid in collab_order
                    if collab_meta[cid]["start_seq"] - 8 <= ev.seq
                    and (collab_meta[cid]["end_seq"] is None or ev.seq <= collab_meta[cid]["end_seq"] + 1)
                ),
                None,
            )
            if not target_cid:
                continue
            if ev.type == "debate.turn_added":
                collab_meta[target_cid]["turns"].append(
                    {
                        "seq": ev.seq,
                        "speaker_id": ev.actor_id or "persona_validation",
                        "speaker_label": _label(ev.actor_id),
                        "stance": str(pl.get("stance") or "comment"),
                        "summary": str(pl.get("public_summary") or ev.summary),
                    }
                )
            elif ev.type == "pod.started":
                collab_meta[target_cid]["turns"].append(
                    {
                        "seq": ev.seq,
                        "speaker_id": ev.actor_id or "persona_validation",
                        "speaker_label": f"{_label(ev.actor_id)} (Research Pod)",
                        "stance": "pod",
                        "summary": ev.summary,
                    }
                )
            elif ev.type == "evidence.reviewed":
                collab_meta[target_cid]["turns"].append(
                    {
                        "seq": ev.seq,
                        "speaker_id": ev.actor_id or "persona_validation",
                        "speaker_label": f"{_label(ev.actor_id)} (Evidence Gate)",
                        "stance": str(pl.get("result") or "reviewed"),
                        "summary": ev.summary,
                    }
                )

        for cid in collab_order:
            collab_meta[cid]["turns"].sort(key=lambda t: t["seq"])

        active_intervention: dict[str, Any] | None = None
        if run_row["status"] == "paused":
            for int_id in reversed(intervention_order):
                if interventions_by_id[int_id]["status"] == "pending":
                    active_intervention = interventions_by_id[int_id]
                    break

        # 2. Construct concise Left-Aligned Vertical Chat Spine (`chat_steps`)
        role_art_ids: dict[str, list[str]] = {
            "paper": [],
            "data": [],
            "analysis_r1": [],
            "support_pdf": [],
            "scripts": [],
        }
        collab_art_ids: dict[str, list[str]] = {cid: [] for cid in collab_order}
        first_collab_start = collab_meta[collab_order[0]]["start_seq"] if collab_order else 10**9

        for ev in events:
            if ev.type != "artifact.created":
                continue
            for aid in ev.artifact_ids:
                if aid in user_attachment_ids:
                    continue
                ak = artifact_kind.get(aid, "other")
                fn = artifact_name.get(aid, "").lower()
                if ak == "script" and aid not in role_art_ids["scripts"]:
                    role_art_ids["scripts"].append(aid)
                if ak == "report" and fn.endswith(".pdf") and aid not in role_art_ids["support_pdf"]:
                    role_art_ids["support_pdf"].append(aid)

                if ak == "report":
                    continue
                assigned_collab = next(
                    (
                        cid
                        for cid in collab_order
                        if collab_meta[cid]["start_seq"] <= ev.seq <= (collab_meta[cid]["end_seq"] or ev.seq)
                    ),
                    None,
                )
                if assigned_collab:
                    if aid not in collab_art_ids[assigned_collab]:
                        collab_art_ids[assigned_collab].append(aid)
                    continue

                actor_role = persona_roles.get(ev.actor_id or "", "")
                if not actor_role and ev.actor_id:
                    actor_role = ev.actor_id.replace("persona_", "").split("_")[0]
                if actor_role == "data" and ev.seq < first_collab_start:
                    if aid not in role_art_ids["data"]:
                        role_art_ids["data"].append(aid)
                elif actor_role == "paper":
                    if aid not in role_art_ids["paper"]:
                        role_art_ids["paper"].append(aid)
                elif actor_role == "analysis" and ev.seq < first_collab_start:
                    if aid not in role_art_ids["analysis_r1"]:
                        role_art_ids["analysis_r1"].append(aid)

        has_run_started = any(e.type == "run.started" for e in events)
        paper_done = any(e.type == "task.completed" and "paper" in (e.actor_id or "") for e in events)
        paper_started = paper_done or any(e.type == "task.started" and "paper" in (e.actor_id or "") for e in events)
        data_done = any(e.type == "task.completed" and "data" in (e.actor_id or "") for e in events)
        data_started = data_done or any(e.type == "task.started" and "data" in (e.actor_id or "") for e in events)
        r1_done = any(
            e.type == "task.completed"
            and "analysis" in (e.actor_id or "")
            and e.seq < first_collab_start
            for e in events
        )
        r1_started = r1_done or any(
            e.type == "task.started"
            and "analysis" in (e.actor_id or "")
            and e.seq < first_collab_start
            for e in events
        )
        ev_reviewed = next((e for e in events if e.type == "evidence.reviewed"), None)
        support_started = any(
            e.type == "agent.status_changed"
            and (e.payload or {}).get("persona_id") == "persona_support"
            and (e.payload or {}).get("next_state") == "working"
            for e in events
        )
        run_completed_ev = next((e for e in reversed(events) if e.type in ("run.completed", "run.failed")), None)
        first_task_seq = next((e.seq for e in events if e.type in ("task.started", "agent.spawned")), 1)

        chat_steps: list[dict[str, Any]] = []
        if has_run_started:
            main_done = paper_started or data_started or bool(run_completed_ev)
            chat_steps.append(
                {
                    "step_id": "step_main_init",
                    "status": "done" if main_done else "working",
                    "actor_label": "Boss agent",
                    "text": (
                        "Boss agent read uploaded files and dispatched specialists"
                        if main_done
                        else "Boss agent reading files, extracting context & calling specialists..."
                    ),
                    "artifact_ids": [],
                    "snapshot_seq": first_task_seq,
                }
            )
        if paper_started:
            chat_steps.append(
                {
                    "step_id": "step_paper",
                    "status": "done" if paper_done else "working",
                    "actor_label": "Paper agent",
                    "text": (
                        "Paper agent extracted methodology, claims & experiment plan"
                        if paper_done
                        else "Paper agent reading paper & extracting methodology..."
                    ),
                    "artifact_ids": role_art_ids["paper"],
                    "snapshot_seq": next(
                        (e.seq for e in events if e.type == "task.started" and "paper" in (e.actor_id or "")),
                        first_task_seq,
                    ),
                }
            )
        if data_started:
            chat_steps.append(
                {
                    "step_id": "step_data",
                    "status": "done" if data_done else "working",
                    "actor_label": "Data agents",
                    "text": (
                        "Data agents found dataset schemas & benchmark findings"
                        if data_done
                        else "Data agents auditing datasets & benchmark files..."
                    ),
                    "artifact_ids": role_art_ids["data"] if data_done else [],
                    "snapshot_seq": next(
                        (e.seq for e in events if e.type == "task.started" and "data" in (e.actor_id or "")),
                        first_task_seq,
                    ),
                }
            )
        if r1_started:
            chat_steps.append(
                {
                    "step_id": "step_analysis_r1",
                    "status": "done" if r1_done else "working",
                    "actor_label": "Analysis agents",
                    "text": (
                        "Analysis agents ran Round 1 quantitative reproduction"
                        if r1_done
                        else "Analysis agents executing Round 1 Python experiments..."
                    ),
                    "artifact_ids": role_art_ids["analysis_r1"] if r1_done else [],
                    "snapshot_seq": next(
                        (e.seq for e in events if e.type == "task.started" and "analysis" in (e.actor_id or "")),
                        first_task_seq,
                    ),
                }
            )

        for idx, cid in enumerate(collab_order):
            cm = collab_meta[cid]
            is_done = cm["end_seq"] is not None
            is_primary = idx == 0
            chat_steps.append(
                {
                    "step_id": f"step_debate_{cid}",
                    "status": "done" if is_done else "working",
                    "actor_label": "Research Team",
                    "text": (
                        (
                            "Agents debated & ran Round 2 robustness experiments"
                            if is_primary
                            else _plain_summary(f"Agents reviewed: {cm['topic']}", 110)
                        )
                        if is_done
                        else "Agents are debating methodology & running robustness checks..."
                    ),
                    "artifact_ids": collab_art_ids.get(cid, []) if is_done else [],
                    "collaboration_id": cid,
                    "debate_summary": _plain_summary(cm["end_report"] or cm["start_reason"], 200),
                    "snapshot_seq": cm["start_seq"],
                }
            )

        if ev_reviewed:
            chat_steps.append(
                {
                    "step_id": "step_validation_gate",
                    "status": "done",
                    "actor_label": "Validation #2",
                    "text": f"Validation #2 audited Evidence Gate ({str((ev_reviewed.payload or {}).get('reproduction_level') or 'S1')} · {str(ev_reviewed.status or 'passed').upper()})",
                    "artifact_ids": [],
                    "snapshot_seq": ev_reviewed.seq,
                }
            )

        if support_started or run_completed_ev:
            deliverables = list(dict.fromkeys([*role_art_ids["support_pdf"], *role_art_ids["scripts"]]))
            chat_steps.append(
                {
                    "step_id": "step_main_summary",
                    "status": "done" if run_completed_ev else "working",
                    "actor_label": "Boss agent",
                    "text": (
                        "Boss agent summarized findings, final PDF report & reproducible scripts"
                        if run_completed_ev
                        else "Boss agent & Support compiling final PDF report and scripts..."
                    ),
                    "artifact_ids": deliverables if run_completed_ev else [],
                    "snapshot_seq": run_completed_ev.seq if run_completed_ev else events[-1].seq,
                }
            )

        # 2b. Extract Main <-> Subagent PPT Whiteboard Briefings (`next_state in ('reporting', 'handoff')`)
        briefings_by_id: dict[str, dict[str, Any]] = {}
        briefing_groups: list[dict[str, Any]] = []

        def _briefing_topic_and_step(reason: str) -> tuple[str, str, list[str]]:
            r_low = reason.lower()
            if "paper" in r_low or "data" in r_low or "setup" in r_low:
                return "Setup Brief", "Paper Spec & Data Protocol", ["step_paper", "step_data"]
            if "report" in r_low or "final" in r_low or "deliver" in r_low:
                return "Final Brief", "Final Report Presentation", ["step_main_summary"]
            return "Validation Brief", "Validation & Evidence Audit Brief", ["step_validation_gate"]

        for ev in events:
            if ev.type != "agent.status_changed":
                continue
            pl = ev.payload or {}
            pid = str(pl.get("persona_id") or "")
            nxt = str(pl.get("next_state") or "")
            if not pid or pid == "persona_main" or nxt not in ("reporting", "handoff"):
                continue

            reason = str(pl.get("reason") or "")
            default_label, default_topic, default_steps = _briefing_topic_and_step(reason)
            topic = str(pl.get("briefing_topic") or default_topic)
            custom_summary = str(pl.get("briefing_summary") or "").strip()
            custom_steps = pl.get("linked_step_ids")
            linked_steps = [str(s) for s in custom_steps] if isinstance(custom_steps, list) and custom_steps else default_steps

            if not custom_summary:
                # Look back at nearest preceding informative summary event
                for prev in reversed(events):
                    if prev.seq >= ev.seq:
                        continue
                    if prev.type in ("evidence.reviewed", "report.generated", "task.completed") and (
                        prev.actor_id == pid or prev.type in ("evidence.reviewed", "report.generated")
                    ):
                        custom_summary = prev.summary
                        break
            if not custom_summary:
                custom_summary = f"{_label(pid)} briefed Boss Agent at the whiteboard on {topic}."

            if (
                briefing_groups
                and ev.seq - briefing_groups[-1]["seq"] <= 3
                and briefing_groups[-1]["topic"] == topic
            ):
                grp = briefing_groups[-1]
                grp["seq"] = ev.seq
                if pid not in grp["presenter_ids"]:
                    grp["presenter_ids"].append(pid)
                    grp["presenter_labels"].append(_label(pid))
                for aid in ev.artifact_ids:
                    if aid not in grp["artifact_ids"]:
                        grp["artifact_ids"].append(aid)
                if custom_summary and custom_summary not in grp["summary"]:
                    grp["summary"] = f"{grp['summary']} {custom_summary}".strip()
            else:
                bid = f"brief_{ev.seq}"
                grp = {
                    "briefing_id": bid,
                    "seq": ev.seq,
                    "label": str(pl.get("briefing_label") or default_label),
                    "presenter_ids": [pid],
                    "presenter_labels": [_label(pid)],
                    "topic": topic,
                    "summary": custom_summary,
                    "artifact_ids": list(ev.artifact_ids),
                    "linked_step_ids": linked_steps,
                }
                briefing_groups.append(grp)

        # Keep up to 3 key briefings cleanly in the timeline
        for grp in briefing_groups[:3]:
            bid = grp["briefing_id"]
            brief_turns = [
                {
                    "seq": grp["seq"],
                    "speaker_id": grp["presenter_ids"][0] if grp["presenter_ids"] else "persona_validation",
                    "speaker_label": " & ".join(grp["presenter_labels"]) if grp["presenter_labels"] else "Specialist",
                    "stance": "report",
                    "summary": grp["summary"],
                }
            ]
            briefings_by_id[bid] = {
                "briefing_id": bid,
                "seq": grp["seq"],
                "presenter_ids": grp["presenter_ids"],
                "presenter_labels": grp["presenter_labels"],
                "topic": grp["topic"],
                "summary": grp["summary"],
                "turns": brief_turns,
                "artifact_ids": grp["artifact_ids"],
                "linked_step_ids": grp["linked_step_ids"],
            }

        # 3. Construct Canvas Milestone Snapshots
        snapshots: list[dict[str, Any]] = []
        if has_run_started:
            snapshots.append(
                {
                    "snapshot_id": "snap_run_start",
                    "seq": first_task_seq,
                    "kind": "run_start",
                    "label": "Dispatch",
                    "reason": "Boss agent read uploaded files and dispatched Paper & Data specialists.",
                    "report": "",
                    "collaboration_id": None,
                    "briefing_id": None,
                    "participants": [],
                    "linked_step_ids": ["step_main_init", "step_paper", "step_data"],
                }
            )

        for grp in briefing_groups[:3]:
            snapshots.append(
                {
                    "snapshot_id": f"snap_brief_{grp['seq']}",
                    "seq": grp["seq"],
                    "kind": "briefing",
                    "label": grp["label"],
                    "reason": _plain_summary(grp["topic"], 360),
                    "report": _plain_summary(grp["summary"], 360),
                    "collaboration_id": None,
                    "briefing_id": grp["briefing_id"],
                    "intervention_id": None,
                    "participants": grp["presenter_labels"],
                    "linked_step_ids": grp["linked_step_ids"],
                }
            )

        for int_id in intervention_order:
            irec = interventions_by_id[int_id]
            snapshots.append(
                {
                    "snapshot_id": f"snap_int_{irec['seq']}",
                    "seq": irec["seq"],
                    "kind": "intervention",
                    "label": "Human Review",
                    "reason": _plain_summary(irec["dissent_summary"], 360),
                    "report": _plain_summary(
                        irec["user_decision"] or "Awaiting human decision in Chat", 360
                    ),
                    "collaboration_id": irec.get("collaboration_id"),
                    "briefing_id": None,
                    "intervention_id": int_id,
                    "participants": irec["from_labels"],
                    "linked_step_ids": irec["linked_step_ids"],
                }
            )

        for idx, cid in enumerate(collab_order):
            cm = collab_meta[cid]
            step_ref = f"step_debate_{cid}"
            snapshots.append(
                {
                    "snapshot_id": f"snap_start_{cid}",
                    "seq": cm["start_seq"],
                    "kind": "huddle_start",
                    "label": "Huddle Start" if idx == 0 else f"Table #{idx + 1}",
                    "reason": _plain_summary(cm["start_reason"], 360),
                    "report": "",
                    "collaboration_id": cid,
                    "briefing_id": None,
                    "participants": cm["participants"],
                    "linked_step_ids": [step_ref],
                }
            )
            for exp_idx, exp in enumerate(cm["expand_events"]):
                snapshots.append(
                    {
                        "snapshot_id": f"snap_expand_{cid}_{exp['seq']}",
                        "seq": exp["seq"],
                        "kind": "huddle_expand",
                        "label": f"{len(exp['members'])}-Agent Pod",
                        "reason": _plain_summary(exp["reason"], 360),
                        "report": "",
                        "collaboration_id": cid,
                        "briefing_id": None,
                        "participants": exp["members"],
                        "linked_step_ids": [step_ref],
                    }
                )
            if cm["end_seq"] is not None:
                linked = [step_ref]
                if ev_reviewed:
                    linked.append("step_validation_gate")
                snapshots.append(
                    {
                        "snapshot_id": f"snap_end_{cid}",
                        "seq": cm["end_seq"],
                        "kind": "huddle_end",
                        "label": "Huddle Report" if idx == 0 else f"Table #{idx + 1} Report",
                        "reason": _plain_summary(cm["start_reason"], 360),
                        "report": _plain_summary(cm["end_report"], 360),
                        "collaboration_id": cid,
                        "briefing_id": None,
                        "participants": cm["participants"],
                        "linked_step_ids": linked,
                    }
                )

        if run_completed_ev:
            snapshots.append(
                {
                    "snapshot_id": "snap_final",
                    "seq": run_completed_ev.seq,
                    "kind": "run_completed",
                    "label": "Final Report",
                    "reason": "",
                    "report": _plain_summary(run_completed_ev.summary, 360),
                    "collaboration_id": None,
                    "briefing_id": None,
                    "participants": [],
                    "linked_step_ids": ["step_main_summary"],
                }
            )

        snapshots.sort(key=lambda s: s["seq"])

        seq_to_epoch: dict[int, float] = {}
        for ev in events:
            try:
                seq_to_epoch[ev.seq] = datetime.fromisoformat(
                    str(ev.occurred_at).replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue

        stage_by_kind = {
            "run_start": "setup",
            "briefing": "briefing",
            "huddle_start": "debate",
            "huddle_expand": "debate",
            "intervention": "intervention",
            "huddle_end": "synthesis",
            "run_completed": "synthesis",
        }
        last_seq = events[-1].seq if events else 0
        for idx, snap in enumerate(snapshots):
            snap["stage"] = stage_by_kind.get(str(snap.get("kind") or ""), "setup")
            cur_seq = int(snap.get("seq") or 0)
            next_seq = (
                int(snapshots[idx + 1]["seq"])
                if idx + 1 < len(snapshots)
                else max(cur_seq, last_seq)
            )
            dt = 0.0
            if cur_seq in seq_to_epoch and next_seq in seq_to_epoch:
                dt = max(0.0, seq_to_epoch[next_seq] - seq_to_epoch[cur_seq])
            if dt < 0.05:
                seq_span = max(1, next_seq - cur_seq)
                dt = 1.5 if snap.get("kind") == "run_completed" else round(seq_span * 1.25, 2)
            snap["duration_sec"] = round(dt, 2)

        table_dialogues = {
            cid: {
                "collaboration_id": cid,
                "kind": cm["kind"],
                "topic": cm["topic"],
                "participants": cm["participants"],
                "start_reason": cm["start_reason"],
                "end_report": cm["end_report"],
                "turns": cm["turns"],
            }
            for cid, cm in collab_meta.items()
        }

        return {
            "run_id": run_id,
            "latest_seq": events[-1].seq if events else 0,
            "snapshots": snapshots,
            "table_dialogues": table_dialogues,
            "briefings": briefings_by_id,
            "interventions": interventions_by_id,
            "active_intervention": active_intervention,
            "chat_steps": chat_steps,
        }


Literal_status = Any
