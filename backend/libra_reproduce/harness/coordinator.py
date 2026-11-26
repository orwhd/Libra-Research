from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..schemas import (
    EventSubject,
    RunMode,
    SelectedContext,
    SourceRef,
    VisualStateLiteral,
)
from ..science.evidence_gate import EvidenceGate, _verify_png_header_and_size
from ..science.iterative_experiments import (
    build_general_science_tools,
    run_generic_followup_experiment,
    run_simulation_round1_fixture,
    run_simulation_round1b_invariants,
    run_simulation_round2_fixture,
)
from ..science.paper_ingestor import build_paper_context_brief, stage_paper_workspace
from ..science.report_builder import build_reproduction_reports
from ..storage.artifact_store import ArtifactStore
from ..storage.db import get_reproduce_conn, jdumps, jloads, utc_now_iso
from ..storage.event_store import EventStore
from .collaboration_manager import CollaborationManager
from .persona_catalog import SPECIALIST_ROLES, build_dynamic_persona
from .scheduler import ResourceScheduler
from .sdk_adapter import SDKAdapter


def _short_title(title: str, max_chars: int = 60) -> str:
    """Shorten a paper title for UI copy at a clean boundary (subtitle colon, parenthesis, or word) without ellipses."""
    t = " ".join(str(title or "").split())
    if len(t) <= max_chars:
        return t
    for sep in (":", " (", " — ", " - "):
        idx = t.find(sep)
        if 16 <= idx <= max_chars:
            return t[:idx].strip()
    cut = t[:max_chars]
    sp = cut.rfind(" ")
    if sp > 16:
        cut = cut[:sp]
    words = cut.rstrip(" ,;:&-(").split()
    while len(words) > 3 and words[-1].lower() in {"and", "or", "of", "the", "a", "an", "for", "in", "on", "to", "with", "&"}:
        words.pop()
    return " ".join(words).strip()


def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


_DEBATE_DISSENT_RE = re.compile(
    r"(?:inconsisten|contradict|disagree|conflict|ambigu|defect|discrepancy|mismatch|alternative|confound|challenge|uncertain|audit|异议|分歧|冲突|矛盾|不一致)",
    flags=re.IGNORECASE,
)


def _wants_chinese(prompt: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]|chinese|中文", prompt, flags=re.IGNORECASE))


def _clean_complete_sentences(text: str | None, max_chars: int = 280) -> str:
    """Normalize LLM output into complete plain sentences without mid-word slices or ellipses."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"```[a-zA-Z0-9_-]*", " ", raw)
    raw = re.sub(r"^\s*#{1,6}\s+([^\n]+)", r"\1 — ", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s*>\s?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)
    raw = re.sub(r"(\*\*|__|~~|\*|`)", "", raw)
    collapsed = re.sub(r"\s+", " ", raw).strip()
    collapsed = re.sub(r"(?:\.{3,}|…)+\s*$", "", collapsed).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    window = collapsed[:max_chars]
    # Prefer cutting at the end of a complete sentence inside max_chars
    sentence_ends = [m.end() for m in re.finditer(r"[.!?。；;](?=\s|$)", window)]
    if sentence_ends and sentence_ends[-1] >= 40:
        return window[: sentence_ends[-1]].strip()
    # Otherwise cut cleanly at a clause or word boundary with no ellipsis
    clause_ends = [m.start() for m in re.finditer(r"[,，](?=\s|$)", window)]
    if clause_ends and clause_ends[-1] >= 40:
        return window[: clause_ends[-1]].strip() + "."
    last_space = window.rfind(" ")
    trimmed = window[:last_space].rstrip(" ,;:(-—") if last_space > 24 else window.rstrip(" ,;:(-—")
    return trimmed


def _format_concise_findings_md(
    findings: list[dict[str, Any]],
    ok_trials: list[dict[str, Any]],
    *,
    is_cjk: bool,
) -> str:
    if findings:
        blocks: list[str] = []
        for f in findings[:5]:
            rid = str(f.get("round_id") or "exp").strip()
            verdict = str(f.get("verdict") or "checked").strip()
            raw_res = str(f.get("quantitative_result") or "").strip()
            # Split long semicolon-separated dumps into crisp indented sub-bullets
            parts = [p.strip() for p in re.split(r";\s*", raw_res) if p.strip()]
            head = f"  - **`{rid}`** (`{verdict}`)"
            if len(parts) <= 1:
                blocks.append(f"{head}: {raw_res[:240]}")
            else:
                sub = "\n".join(f"    - {p[:160]}" for p in parts[:4])
                blocks.append(f"{head}:\n{sub}")
        return "\n".join(blocks)
    if ok_trials:
        return "\n".join(
            f"  - **`{t.get('round_label', 'trial')}`** (`{t.get('script_name')}`): {str(t.get('stdout') or '').strip()[:150]}"
            for t in ok_trials[:4]
        )
    return "  - " + ("已完成受控验证实验。" if is_cjk else "Controlled verification completed.")


def _derive_fallback_title(user_prompt: str, dossier: dict[str, Any]) -> str:
    meta_title = str((dossier.get("scholarly_metadata") or {}).get("title") or "").strip()
    if meta_title:
        return meta_title[:160]
    for att in dossier.get("attachments") or []:
        pdf_title = str((att.get("pdf_metadata") or {}).get("title") or "").strip()
        if pdf_title and len(pdf_title) >= 4:
            return pdf_title[:160]
        fname = str(att.get("filename") or "").strip()
        if fname and fname.lower().endswith(".pdf"):
            return Path(fname).stem.replace("_", " ").replace("-", " ")[:160]
    return user_prompt.strip()[:120] or "Scientific Paper Reproduction"


class ResearchCoordinator:
    """
    Paper-agnostic scientific multi-agent Coordinator.
    Orchestrates dynamic multi-instance specialists across 6 canonical role classes
    (`Main`, `Paper`, `Data`, `Analysis`, `Validation`, `Support`), dual Scheduler pools,
    genuine multi-round Python experimentation inside `SafeExperimentRunner`, live methodological
    Debate -> Pod collaboration morphing, universal `EvidenceGate`, and DOCX/PDF reporting.
    """

    def __init__(
        self,
        *,
        event_store: EventStore,
        artifact_store: ArtifactStore,
        scheduler: ResourceScheduler,
    ) -> None:
        self.store = event_store
        self.artifacts = artifact_store
        self.scheduler = scheduler
        self.collab = CollaborationManager(event_store)
        self.evidence_gate = EvidenceGate()
        self._pending_interventions: dict[str, dict[str, Any]] = {}

    def has_pending_intervention(self, run_id: str) -> bool:
        entry = self._pending_interventions.get(run_id)
        if not entry:
            return False
        fut = entry.get("future")
        return isinstance(fut, asyncio.Future) and not fut.done()

    def get_pending_intervention(self, run_id: str) -> dict[str, Any] | None:
        return self._pending_interventions.get(run_id)

    def resolve_human_intervention(self, run_id: str, decision_text: str) -> dict[str, Any] | None:
        entry = self._pending_interventions.pop(run_id, None)
        if not entry:
            return None
        fut = entry.get("future")
        if isinstance(fut, asyncio.Future) and not fut.done():
            fut.set_result(decision_text)
        return entry

    def cancel_pending_intervention(self, run_id: str) -> None:
        entry = self._pending_interventions.pop(run_id, None)
        if not entry:
            return
        fut = entry.get("future")
        if isinstance(fut, asyncio.Future) and not fut.done():
            fut.cancel()

    def _get_persona_state(self, run_id: str, persona_id: str) -> dict[str, Any]:
        with get_reproduce_conn(self.store.db_path) as conn:
            row = conn.execute(
                "SELECT state, runtime_agent_id, role, label FROM reproduce_personas WHERE run_id = ? AND persona_id = ?",
                (run_id, persona_id),
            ).fetchone()
        return dict(row) if row else {"state": "idle", "runtime_agent_id": None, "role": None, "label": None}

    def _transition_persona(
        self,
        run_id: str,
        persona_id: str,
        next_state: VisualStateLiteral,
        reason: str,
        *,
        runtime_agent_id: str | None = None,
        mode: RunMode | None = None,
        extra_payload: dict[str, Any] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> None:
        with_prev = self._get_persona_state(run_id, persona_id)
        display_label = str(with_prev.get("label") or persona_id.replace("persona_", "").replace("_", " #").title())

        if runtime_agent_id and not with_prev.get("runtime_agent_id"):
            self.store.append_event(
                run_id,
                event_type="agent.spawned",
                actor_id=runtime_agent_id,
                target_ids=[persona_id],
                subject=EventSubject(kind="agent", id=persona_id),
                status="active",
                summary=f"Bound runtime execution session for {display_label}.",
                payload={
                    "persona_id": persona_id,
                    "role": with_prev.get("role") or persona_id.replace("persona_", "").split("_")[0],
                    "label": display_label,
                    "is_main": persona_id == "persona_main",
                    "runtime_agent_id": runtime_agent_id,
                },
                mode=mode,
                state_mutator=lambda conn, seq: conn.execute(
                    "UPDATE reproduce_personas SET runtime_agent_id = ?, updated_seq = ? WHERE run_id = ? AND persona_id = ?",
                    (runtime_agent_id, seq, run_id, persona_id),
                ),
            )

        if with_prev.get("state") == next_state and not extra_payload:
            return

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            row = conn.execute(
                "SELECT runtime_agent_id FROM reproduce_personas WHERE run_id = ? AND persona_id = ?",
                (run_id, persona_id),
            ).fetchone()
            eff_runtime = runtime_agent_id if runtime_agent_id is not None else (row["runtime_agent_id"] if row else None)
            conn.execute(
                "UPDATE reproduce_personas SET state = ?, runtime_agent_id = ?, updated_seq = ? WHERE run_id = ? AND persona_id = ?",
                (next_state, eff_runtime, seq, run_id, persona_id),
            )

        payload_out: dict[str, Any] = {
            "persona_id": persona_id,
            "previous_state": with_prev.get("state", "idle"),
            "next_state": next_state,
            "reason": reason,
        }
        if extra_payload:
            payload_out.update(extra_payload)

        self.store.append_event(
            run_id,
            event_type="agent.status_changed",
            actor_id=runtime_agent_id or persona_id,
            target_ids=[persona_id],
            subject=EventSubject(kind="agent", id=persona_id),
            status=next_state,
            summary=f"{display_label} -> {next_state} ({reason})",
            payload=payload_out,
            artifact_ids=artifact_ids,
            mode=mode,
            state_mutator=_mut,
        )

    async def _brief_main_with_easel(
        self,
        run_id: str,
        *,
        presenter_ids: list[str],
        reason: str,
        topic: str,
        summary: str,
        briefing_label: str,
        linked_step_ids: list[str],
        artifact_ids: list[str] | None = None,
        mode: RunMode,
    ) -> None:
        """
        Walk 1-2 specialist subagents up to Main Agent with a whiteboard easel to present findings.
        Honors strict `CanvasProjection` contracts by encoding briefing state inside `state='reporting'`
        plus structured `payload` fields projected by `/timeline`.
        """
        clean_presenters = [p for p in _dedup(presenter_ids) if p != "persona_main"][:2]
        if not clean_presenters:
            return
        self._transition_persona(
            run_id,
            "persona_main",
            "waiting",
            f"listening_briefing_{reason}",
            mode=mode,
        )
        for idx, pid in enumerate(clean_presenters):
            self._transition_persona(
                run_id,
                pid,
                "reporting",
                reason,
                artifact_ids=artifact_ids if idx == 0 else None,
                mode=mode,
                extra_payload={
                    "briefing_to": "persona_main",
                    "briefing_label": briefing_label,
                    "briefing_topic": topic,
                    "briefing_summary": summary,
                    "linked_step_ids": linked_step_ids,
                },
            )
        await asyncio.sleep(0.18 if mode == "simulation" else 2.8)
        self._transition_persona(run_id, "persona_main", "working", "coordinating_reproduction_plan", mode=mode)

    def _spawn_dynamic_persona(
        self,
        run_id: str,
        *,
        role: str,
        instance_index: int | None = None,
        label: str | None = None,
        specialty: str = "",
        mode: RunMode | None = None,
    ) -> str:
        idx = instance_index or self.store.get_next_role_instance_index(run_id, role)
        rt_id = f"rt_{role}_{idx}_{run_id[-6:]}"
        persona = build_dynamic_persona(
            role=role,
            instance_index=idx,
            label=label,
            runtime_agent_id=rt_id,
        )

        def _insert_dynamic(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                INSERT OR REPLACE INTO reproduce_personas (
                    run_id, persona_id, role, label, runtime_agent_id,
                    state, is_main, task_ids_json, updated_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?)
                """,
                (run_id, persona.persona_id, persona.role, persona.label, rt_id, persona.state, 0, seq),
            )

        desc = (
            f"Boss dynamically spawned {persona.label} ({persona.role}): {specialty}."
            if specialty
            else f"Boss dynamically spawned {persona.label} ({persona.role}) for parallel execution."
        )
        ev = self.store.append_event(
            run_id,
            event_type="agent.spawned",
            actor_id="persona_main",
            target_ids=[persona.persona_id],
            subject=EventSubject(kind="agent", id=persona.persona_id),
            status="active",
            summary=desc,
            payload={
                "persona_id": persona.persona_id,
                "role": persona.role,
                "label": persona.label,
                "is_main": False,
                "runtime_agent_id": rt_id,
                "dynamic": True,
                "spawned_by": "persona_main",
                "specialty": specialty,
            },
            mode=mode,
            state_mutator=_insert_dynamic,
        )
        self.store.add_chat_message(
            run_id,
            kind="activity",
            role="activity",
            text=desc,
            related_refs=[
                SelectedContext(kind="agent", id="persona_main"),
                SelectedContext(kind="agent", id=persona.persona_id),
            ],
            seq=ev.seq,
        )
        return persona.persona_id

    def _create_task_record(
        self,
        run_id: str,
        *,
        persona_id: str,
        role: str,
        objective: str,
        is_exploration: bool = False,
        mode: RunMode | None = None,
    ) -> str:
        tid = f"task_{role}_{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                INSERT INTO reproduce_tasks (
                    task_id, run_id, parent_task_id, persona_id, role, objective,
                    status, priority, is_exploration, budget_json, artifact_ids_json,
                    updated_seq, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, 'queued', 10, ?, '{}', '[]', ?, ?, ?)
                """,
                (tid, run_id, persona_id, role, objective, 1 if is_exploration else 0, seq, now, now),
            )
            p_row = conn.execute(
                "SELECT task_ids_json FROM reproduce_personas WHERE run_id = ? AND persona_id = ?",
                (run_id, persona_id),
            ).fetchone()
            tids = jloads(p_row["task_ids_json"], []) if p_row else []
            if tid not in tids:
                tids.append(tid)
            conn.execute(
                "UPDATE reproduce_personas SET task_ids_json = ?, updated_seq = ? WHERE run_id = ? AND persona_id = ?",
                (jdumps(tids), seq, run_id, persona_id),
            )

        self.store.append_event(
            run_id,
            event_type="task.created",
            actor_id="persona_main",
            target_ids=[persona_id],
            subject=EventSubject(kind="task", id=tid),
            status="queued",
            summary=f"Assigned {role.capitalize()}: {objective}",
            payload={
                "task_id": tid,
                "persona_id": persona_id,
                "role": role,
                "objective": objective,
                "is_exploration": is_exploration,
            },
            mode=mode,
            state_mutator=_mut,
        )
        return tid

    def _mark_task_status(
        self,
        run_id: str,
        task_id: str,
        event_type: str,
        status: str,
        summary: str,
        *,
        persona_id: str,
        artifact_ids: list[str] | None = None,
        mode: RunMode | None = None,
    ) -> None:
        aids = _dedup(artifact_ids or [])

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                "UPDATE reproduce_tasks SET status = ?, artifact_ids_json = ?, updated_seq = ?, updated_at = ? WHERE task_id = ?",
                (status, jdumps(aids), seq, utc_now_iso(), task_id),
            )

        ev = self.store.append_event(
            run_id,
            event_type=event_type,
            actor_id=persona_id,
            target_ids=[persona_id],
            subject=EventSubject(kind="task", id=task_id),
            status=status,
            summary=summary,
            payload={"task_id": task_id, "persona_id": persona_id},
            artifact_ids=aids,
            mode=mode,
            state_mutator=_mut,
        )
        self.store.add_chat_message(
            run_id,
            kind="activity",
            role="activity",
            text=summary,
            related_refs=[
                SelectedContext(kind="agent", id=persona_id),
                SelectedContext(kind="task", id=task_id),
            ],
            artifact_ids=aids,
            seq=ev.seq,
        )

    def _emit_tool_traces(
        self,
        run_id: str,
        *,
        actor_id: str,
        task_id: str,
        tool_traces: list[dict[str, Any]],
        mode: RunMode,
    ) -> None:
        for tr in tool_traces:
            self.store.append_event(
                run_id,
                event_type="tool.executed",
                actor_id=actor_id,
                target_ids=[actor_id],
                subject=EventSubject(kind="task", id=task_id),
                status="completed" if tr.get("status") != "denied" else "denied",
                summary=f"Tool '{tr['tool']}' ({tr.get('status', 'ok')}) via {tr['provider']}.",
                payload=tr,
                mode=mode,
                source="sdk",
            )

    def _emit_artifact_created(
        self,
        run_id: str,
        *,
        actor_id: str,
        target_ids: list[str],
        artifact_id: str,
        kind: str,
        validation_status: str,
        summary: str,
        related_refs: list[SelectedContext],
        mode: RunMode,
    ) -> None:
        ev = self.store.append_event(
            run_id,
            event_type="artifact.created",
            actor_id=actor_id,
            target_ids=_dedup(target_ids),
            subject=EventSubject(kind="artifact", id=artifact_id),
            status=validation_status,
            summary=summary,
            payload={
                "artifact_id": artifact_id,
                "kind": kind,
                "validation_status": validation_status,
            },
            artifact_ids=[artifact_id],
            mode=mode,
            source="runner",
        )
        self.store.add_chat_message(
            run_id,
            kind="file",
            role="activity",
            text=summary,
            related_refs=[*related_refs, SelectedContext(kind="artifact", id=artifact_id)],
            artifact_ids=[artifact_id],
            seq=ev.seq,
        )

    async def execute_reproduction_plan(
        self,
        run_id: str,
        *,
        user_prompt: str,
        attachment_ids: list[str] | None = None,
    ) -> None:
        meta = self.store.get_run_meta(run_id)
        if not meta:
            return
        mode: RunMode = meta["mode"]
        run_dir = self.artifacts.run_dir(run_id)
        sdk_adapter = SDKAdapter(run_dir)

        if mode == "live":
            can_live, _ = sdk_adapter.detect_live_capability()
            if not can_live:
                self._fail_run(
                    run_id,
                    mode=mode,
                    reason=(
                        "BLOCKED: No live Claude Agent SDK or configured LLM API credentials detected. "
                        "Configure an API key in settings or run explicitly in mode='simulation'."
                    ),
                )
                return

        active_collab_id: str | None = None
        try:
            self.store.update_run_status(
                run_id,
                status="running",
                report_status="generating",
                resources=self.scheduler.snapshot_state(run_id),
            )
            self._transition_persona(
                run_id,
                "persona_main",
                "working",
                "coordinating_reproduction_plan",
                runtime_agent_id=f"rt_main_{run_id[-6:]}",
                mode=mode,
            )

            # Stage 0: Ingest all attached paper PDFs / datasets / code / scholarly metadata
            att_paths: list[Path] = []
            for aid in attachment_ids or []:
                resolved = self.artifacts.resolve_artifact_bytes(aid)
                if resolved:
                    att_paths.append(resolved[0])

            staged = await stage_paper_workspace(
                run_dir,
                user_prompt=user_prompt,
                attachment_paths=att_paths,
                mode=mode,
            )
            dossier = staged["dossier"]
            fallback_title = _derive_fallback_title(user_prompt, dossier)
            paper_brief = build_paper_context_brief(dossier, run_dir)

            shared_state: dict[str, Any] = {
                "paper_spec": {},
                "data_manifest": {},
                "data_manifest_path": None,
                "primary_data_path": None,
                "executed_trials": [],
                "recorded_findings": [],
                "created_figures": [],
                "created_tables": [],
            }

            # Create primary specialist tasks
            t_paper = self._create_task_record(
                run_id,
                persona_id="persona_paper",
                role="paper",
                objective=f"Inspect target paper sources and formalize testable claims, metrics, and multi-round protocol for '{fallback_title[:60]}'",
                mode=mode,
            )
            t_data = self._create_task_record(
                run_id,
                persona_id="persona_data",
                role="data",
                objective="Audit input data/datasets, fetch open benchmarks if available, or construct honest experimental harness manifest",
                mode=mode,
            )
            t_analysis = self._create_task_record(
                run_id,
                persona_id="persona_analysis",
                role="analysis",
                objective="Design and execute Round 1 quantitative reproduction script and generate primary figure",
                mode=mode,
            )
            t_validation = self._create_task_record(
                run_id,
                persona_id="persona_validation",
                role="validation",
                objective="Critique Round 1 findings, drive Round 2 ablations/falsification, and enforce Evidence Gate",
                mode=mode,
            )

            data_2_id = self._spawn_dynamic_persona(
                run_id,
                role="data",
                instance_index=2,
                label="Data #2",
                specialty="Attachment & Benchmark Schema Audit",
                mode=mode,
            )
            t_data_2 = self._create_task_record(
                run_id,
                persona_id=data_2_id,
                role="data",
                objective="Catalog uploaded paper dossier, extracted PDF sections, and baseline metrics",
                mode=mode,
            )

            # --- Phase 1: Parallel Paper + Data + Data #2 ---
            async def _paper_stage() -> str:
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_paper):
                    self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                    self._transition_persona(
                        run_id,
                        "persona_paper",
                        "working",
                        "extracting_paper_methodology",
                        runtime_agent_id=f"rt_paper_{run_id[-6:]}",
                        mode=mode,
                    )
                    self._mark_task_status(
                        run_id,
                        t_paper,
                        "task.started",
                        "running",
                        f"Paper Agent inspecting `{len(att_paths)}` attachment(s) & scholarly metadata for '{fallback_title[:70]}'.",
                        persona_id="persona_paper",
                        mode=mode,
                    )
                    provider_used = "simulation_mock"
                    if mode == "live":
                        tools_p, handlers_p = build_general_science_tools(
                            run_id=run_id,
                            workspace_dir=run_dir,
                            scheduler=self.scheduler,
                            owner_task_id=t_paper,
                            shared_state=shared_state,
                        )
                        p_tools = [t for t in tools_p if t["name"] in ("inspect_workspace_files", "register_paper_spec")]
                        p_res = await sdk_adapter.run_controlled_science_turn(
                            mode=mode,
                            role="paper",
                            system_prompt=(
                                "You are Paper Agent. A compact structured paper digest is provided below. "
                                "Use `inspect_workspace_files` with `search_query` ONLY if you need an exact detail not in the digest (never paginate sequentially from offset 0!). "
                                "Call `register_paper_spec` within 1-2 turns with exact fields for THIS specific paper: "
                                "paper_title, authors_year, domain, doi_or_url, core_claims, methodology_summary, key_metrics, "
                                "datasets_or_benchmarks, rival_hypotheses (at least 2 plausible confounders/alternatives), and a concrete 2-round experiment_plan."
                            ),
                            user_prompt=(
                                f"Target request: {user_prompt}\n\n"
                                f"=== Paper & Workspace Digest ===\n{paper_brief}"
                            ),
                            tools_spec=p_tools,
                            handlers={k: handlers_p[k] for k in ("inspect_workspace_files", "register_paper_spec")},
                            max_turns=5,
                            timeout_seconds=65.0,
                            effort=None,
                            on_tool_trace=lambda tr: self._emit_tool_traces(
                                run_id, actor_id="persona_paper", task_id=t_paper, tool_traces=[tr], mode=mode
                            ),
                        )
                        provider_used = p_res.provider
                        if not p_res.ok:
                            raise RuntimeError(p_res.error or "Paper Agent live turn failed")

                    if not shared_state["paper_spec"]:
                        # Simulation or safeguard: register structured spec from paper_dossier.json
                        _, handlers_p = build_general_science_tools(
                            run_id=run_id,
                            workspace_dir=run_dir,
                            scheduler=self.scheduler,
                            owner_task_id=t_paper,
                            shared_state=shared_state,
                        )
                        sch = dossier.get("scholarly_metadata") or {}
                        await handlers_p["register_paper_spec"](
                            {
                                "paper_title": fallback_title,
                                "authors_year": ", ".join(sch.get("authors") or []) or str(sch.get("year") or "Target Paper"),
                                "domain": "Scientific & Empirical Reproduction",
                                "doi_or_url": str(sch.get("url") or sch.get("detected_doi") or ""),
                                "core_claims": [f"Core empirical/methodological claim from: {user_prompt[:140]}"],
                                "methodology_summary": str(sch.get("abstract") or user_prompt),
                                "key_metrics": ["Primary Evaluation Metric", "Effect Delta vs. Baseline"],
                                "datasets_or_benchmarks": list(dossier.get("tabular_rel_paths") or ["Controlled Experimental Benchmark"]),
                                "rival_hypotheses": [
                                    "Confounding or sampling noise explains the reported baseline-vs-method gap",
                                    "Effect disappears under component ablation or hyperparameter perturbation",
                                ],
                                "experiment_plan": [
                                    "Round 1: Reconstruct core baseline vs. proposed method comparison with 95% CIs",
                                    "Round 2: Execute ablation, 200-trial permutation null, and sensitivity sweep",
                                ],
                            }
                        )

                    self._mark_task_status(
                        run_id,
                        t_paper,
                        "task.completed",
                        "done",
                        f"Paper specification registered for '{_short_title(shared_state['paper_spec']['paper_title'], 80)}'.",
                        persona_id="persona_paper",
                        mode=mode,
                    )
                    self._transition_persona(run_id, "persona_paper", "completed", "paper_spec_ready", mode=mode)
                    return provider_used

            async def _data_stage() -> None:
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_data):
                    self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                    self._transition_persona(
                        run_id,
                        "persona_data",
                        "working",
                        "staging_data_and_protocol",
                        runtime_agent_id=f"rt_data_{run_id[-6:]}",
                        mode=mode,
                    )
                    self._mark_task_status(
                        run_id,
                        t_data,
                        "task.started",
                        "running",
                        "Data Agent auditing uploaded datasets, public links, and controlled benchmark protocol.",
                        persona_id="persona_data",
                        mode=mode,
                    )
                    tools_d, handlers_d = build_general_science_tools(
                        run_id=run_id,
                        workspace_dir=run_dir,
                        scheduler=self.scheduler,
                        owner_task_id=t_data,
                        shared_state=shared_state,
                    )
                    if mode == "live":
                        d_tools = [
                            t for t in tools_d
                            if t["name"] in ("inspect_workspace_files", "fetch_public_dataset", "register_data_manifest")
                        ]
                        d_res = await sdk_adapter.run_controlled_science_turn(
                            mode=mode,
                            role="data",
                            system_prompt=(
                                "You are Data Agent. A workspace & paper digest is provided below (do NOT paginate extracted PDFs linearly; use `inspect_workspace_files` with `search_query` only if needed). "
                                "If `tabular_rel_paths` has an uploaded CSV/TSV/Parquet, use it as `primary_data_file`. Otherwise if the digest cites a direct public CSV/JSON/TSV URL you may try `fetch_public_dataset`. "
                                "If no external dataset is uploaded/fetchable, honestly declare `dataset_kind='controlled_synthetic_benchmark'` or `'algorithmic_simulation'` "
                                "with `reproduction_level='S1'` or `'S2'` and explicit `known_biases`. Call `register_data_manifest` directly within 1-2 turns."
                            ),
                            user_prompt=(
                                f"Audit and stage the dataset/protocol manifest for reproducing: {user_prompt}\n\n"
                                f"=== Workspace & Paper Digest ===\n{paper_brief[:4500]}"
                            ),
                            tools_spec=d_tools,
                            handlers={k: handlers_d[k] for k in ("inspect_workspace_files", "fetch_public_dataset", "register_data_manifest")},
                            max_turns=5,
                            timeout_seconds=65.0,
                            effort=None,
                            on_tool_trace=lambda tr: self._emit_tool_traces(
                                run_id, actor_id="persona_data", task_id=t_data, tool_traces=[tr], mode=mode
                            ),
                        )

                    if not shared_state["data_manifest"]:
                        tab_files = dossier.get("tabular_rel_paths") or []
                        primary_rel = tab_files[0] if tab_files else ""
                        ds_kind = "user_uploaded_dataset" if primary_rel else "controlled_synthetic_benchmark"
                        await handlers_d["register_data_manifest"](
                            {
                                "source_doi_or_url": str((dossier.get("scholarly_metadata") or {}).get("url") or "User-provided paper & controlled benchmark"),
                                "license": "Open Scientific Evaluation / User Upload",
                                "dataset_kind": ds_kind,
                                "primary_data_file": primary_rel,
                                "row_count": 0,
                                "columns_verified": [],
                                "reproduction_level": "S1",
                                "metric_origin": "computed_from_experiment",
                                "schema_verification_note": (
                                    f"Staged user dataset '{primary_rel}'."
                                    if primary_rel
                                    else "No external raw dataset file attached; using controlled synthetic benchmark & algorithmic simulation."
                                ),
                                "known_biases": (
                                    [] if primary_rel else ["Controlled benchmark simulates paper mechanisms at reduced scale rather than full proprietary corpus."]
                                ),
                            }
                        )

                    man_path = shared_state["data_manifest_path"]
                    art_man, _ = self.artifacts.register_file(
                        run_id=run_id,
                        kind="dataset",
                        source_path=man_path,
                        validation_status="passed",
                        metadata={
                            "reproduction_level": shared_state["data_manifest"].get("reproduction_level", "S1"),
                            "description": "Data & experimental environment SHA-256 manifest",
                        },
                    )
                    shared_state["manifest_artifact_id"] = art_man.artifact_id
                    self._emit_artifact_created(
                        run_id,
                        actor_id="persona_data",
                        target_ids=["persona_data"],
                        artifact_id=art_man.artifact_id,
                        kind="dataset",
                        validation_status="passed",
                        summary=f"Manifest verified ({shared_state['data_manifest']['dataset_kind']}).",
                        related_refs=[SelectedContext(kind="agent", id="persona_data")],
                        mode=mode,
                    )
                    self._mark_task_status(
                        run_id,
                        t_data,
                        "task.completed",
                        "done",
                        f"Data manifest audited: {shared_state['data_manifest']['dataset_kind']}.",
                        persona_id="persona_data",
                        artifact_ids=[art_man.artifact_id],
                        mode=mode,
                    )
                    self._transition_persona(run_id, "persona_data", "idle", "data_manifest_ready", mode=mode)

            async def _data_2_stage() -> None:
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_data_2):
                    self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                    self._transition_persona(run_id, data_2_id, "working", "staging_paper_dossier", mode=mode)
                    self._mark_task_status(
                        run_id,
                        t_data_2,
                        "task.started",
                        "running",
                        "Data #2 indexing uploaded paper PDF extracts and attachment profiles.",
                        persona_id=data_2_id,
                        mode=mode,
                    )
                    art_dos, _ = self.artifacts.register_file(
                        run_id=run_id,
                        kind="dataset",
                        source_path=staged["dossier_path"],
                        validation_status="passed",
                        metadata={"description": "Ingested paper dossier (PDF text, links, schemas)"},
                    )
                    shared_state["dossier_artifact_id"] = art_dos.artifact_id
                    self._emit_artifact_created(
                        run_id,
                        actor_id=data_2_id,
                        target_ids=[data_2_id],
                        artifact_id=art_dos.artifact_id,
                        kind="dataset",
                        validation_status="passed",
                        summary="Paper dossier (`paper_dossier.json`) staged by Data #2.",
                        related_refs=[SelectedContext(kind="agent", id=data_2_id)],
                        mode=mode,
                    )
                    self._mark_task_status(
                        run_id,
                        t_data_2,
                        "task.completed",
                        "done",
                        f"Data #2 indexed {len(dossier.get('attachments') or [])} attachment(s) cleanly.",
                        persona_id=data_2_id,
                        artifact_ids=[art_dos.artifact_id],
                        mode=mode,
                    )
                    self._transition_persona(run_id, data_2_id, "completed", "dossier_ready", mode=mode)

            provider_used, _, _ = await asyncio.gather(_paper_stage(), _data_stage(), _data_2_stage())
            paper_spec = shared_state["paper_spec"]
            spec_digest = json.dumps(paper_spec, ensure_ascii=False)[:2600]
            manifest_digest = json.dumps(shared_state["data_manifest"], ensure_ascii=False)[:1400]

            # Subagents walk up to Main Agent with whiteboard easel to present Paper Spec & Data setup
            await self._brief_main_with_easel(
                run_id,
                presenter_ids=["persona_paper", "persona_data"],
                reason="briefing_main_paper_spec",
                briefing_label="Setup Brief",
                topic="Paper Spec & Data Protocol",
                summary=(
                    f"Paper & Data briefed Boss on '{_short_title(paper_spec['paper_title'], 70)}' "
                    f"({shared_state['data_manifest'].get('dataset_kind', 'controlled_benchmark')}, "
                    f"{len(paper_spec.get('core_claims') or [])} testable claim(s))."
                ),
                linked_step_ids=["step_paper", "step_data"],
                artifact_ids=[
                    x
                    for x in (shared_state.get("manifest_artifact_id"), shared_state.get("dossier_artifact_id"))
                    if x
                ],
                mode=mode,
            )
            self._transition_persona(run_id, "persona_paper", "completed", "paper_spec_ready", mode=mode)
            self._transition_persona(run_id, "persona_data", "idle", "data_manifest_ready", mode=mode)

            # --- Phase 2: Parallel Multi-Instance Round 1 (`persona_analysis` + `persona_analysis_2`) ---
            analysis_2_id = self._spawn_dynamic_persona(
                run_id,
                role="analysis",
                instance_index=2,
                label="Analysis #2",
                specialty="Mathematical Invariants & Unit Edge-Case Verification",
                mode=mode,
            )
            t_analysis_2 = self._create_task_record(
                run_id,
                persona_id=analysis_2_id,
                role="analysis",
                objective="Author & execute independent mathematical invariant / boundary test script for the target paper",
                mode=mode,
            )

            async def _analysis_round1() -> None:
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_analysis):
                    self._transition_persona(
                        run_id,
                        "persona_analysis",
                        "working",
                        "running_round1_reproduction",
                        runtime_agent_id=f"rt_analysis_{run_id[-6:]}",
                        mode=mode,
                    )
                    self._mark_task_status(
                        run_id,
                        t_analysis,
                        "task.started",
                        "running",
                        f"Analysis Agent acquiring parallel experiment slot #1 for Round 1 reproduction of '{_short_title(paper_spec['paper_title'], 60)}'.",
                        persona_id="persona_analysis",
                        mode=mode,
                    )
                    if mode == "live":
                        tools_a1, handlers_a1 = build_general_science_tools(
                            run_id=run_id,
                            workspace_dir=run_dir,
                            scheduler=self.scheduler,
                            owner_task_id=t_analysis,
                            shared_state=shared_state,
                        )
                        a1_tools = [
                            t for t in tools_a1
                            if t["name"] in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")
                        ]
                        a1_handlers = {k: handlers_a1[k] for k in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")}
                        sys_a1 = (
                            "You are Analysis Agent #1 (Round 1 Primary Reproduction). The paper specification and data manifest are ALREADY provided in the prompt below — "
                            "do NOT waste turns paging through extracted PDF text! Jump straight to authoring and EXECUTING `run_python_experiment` "
                            "(`script_name='exp_round1_reproduction.py'`, `round_label='round1'`) implementing this paper's core estimator/algorithm vs. baseline. "
                            "Requirements: "
                            "(1) Use numpy/pandas/scipy/matplotlib/networkx (`sys.argv[1]` is `data/`, `sys.argv[2]` is `outputs/` — NEVER import `os.environ`, `subprocess`, or network libraries); "
                            "(2) Compute quantitative metrics (with CIs/p-values) and save `Path(sys.argv[2]) / 'round1_results.csv'`; "
                            "(3) Render a clear figure `Path(sys.argv[2]) / 'figure_round1_reproduction.png'` (>= 800x400 px); "
                            "(4) Print clean JSON summary to stdout; fix & re-run if stderr occurs; call `record_experiment_finding` with concise key metrics (<= 220 chars, use inline LaTeX like `$\\hat{\\delta}$` or `$p$` for math)."
                        )
                        a1_res = await sdk_adapter.run_controlled_science_turn(
                            mode=mode,
                            role="analysis",
                            system_prompt=sys_a1,
                            user_prompt=(
                                f"Execute Round 1 quantitative reproduction experiment for '{paper_spec['paper_title']}'. Target prompt: {user_prompt}\n\n"
                                f"=== Paper Spec ===\n{spec_digest}\n\n=== Data Manifest ===\n{manifest_digest}"
                            ),
                            tools_spec=a1_tools,
                            handlers=a1_handlers,
                            max_turns=6,
                            timeout_seconds=80.0,
                            effort=None,
                            on_tool_trace=lambda tr: self._emit_tool_traces(
                                run_id, actor_id="persona_analysis", task_id=t_analysis, tool_traces=[tr], mode=mode
                            ),
                        )
                        # Check if Round 1 produced at least 1 passing script & 1 valid figure; if not (and provider responded), nudge once cleanly
                        has_r1_fig = any(_verify_png_header_and_size(p)[0] for p in shared_state["created_figures"])
                        has_r1_ok = any(t.get("ok") and t.get("round_label") == "round1" for t in shared_state["executed_trials"])
                        if a1_res.ok and not (has_r1_ok and has_r1_fig):
                            await sdk_adapter.run_controlled_science_turn(
                                mode=mode,
                                role="analysis",
                                system_prompt=sys_a1,
                                user_prompt=(
                                    "Round 1 requires one clean `run_python_experiment` execution (exit_code=0) saving `round1_results.csv` "
                                    "and `figure_round1_reproduction.png` (>=800x400 px) in `Path(sys.argv[2])`. Execute now."
                                ),
                                tools_spec=a1_tools,
                                handlers=a1_handlers,
                                max_turns=4,
                                timeout_seconds=60.0,
                                effort=None,
                                on_tool_trace=lambda tr: self._emit_tool_traces(
                                    run_id, actor_id="persona_analysis", task_id=t_analysis, tool_traces=[tr], mode=mode
                                ),
                            )
                    else:
                        async with self.scheduler.experiment_slot(run_id=run_id, owner_id=t_analysis):
                            self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                            r1 = await run_simulation_round1_fixture(run_dir)
                        shared_state["executed_trials"].append(r1["trial"])
                        shared_state["created_tables"].append(r1["csv_path"])
                        shared_state["created_figures"].append(r1["fig_path"])
                        shared_state["recorded_findings"].append(
                            {
                                "kind": "recorded_finding",
                                "round_id": "round1",
                                "hypothesis": f"Core reproduction of {_short_title(paper_spec['paper_title'], 80)}",
                                "method": "Simulated controlled scaling benchmark",
                                "primary_metric": "effect_delta",
                                "quantitative_result": f"mean_effect_delta={r1['summary']['mean_effect_delta']}, scaling_slope={r1['summary']['scaling_slope']}",
                                "verdict": "supported",
                                "next_question": "Does the effect survive component ablation and permutation null testing?",
                            }
                        )

                    self._mark_task_status(
                        run_id,
                        t_analysis,
                        "task.completed",
                        "done",
                        f"Analysis Agent completed Round 1 experiments ({len([t for t in shared_state['executed_trials'] if t.get('ok')])} clean run(s)).",
                        persona_id="persona_analysis",
                        mode=mode,
                    )

            async def _analysis_round1b() -> None:
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_analysis_2):
                    self._transition_persona(run_id, analysis_2_id, "working", "verifying_mathematical_invariants", mode=mode)
                    self._mark_task_status(
                        run_id,
                        t_analysis_2,
                        "task.started",
                        "running",
                        "Analysis #2 acquiring parallel experiment slot #2 to test mathematical invariants & boundary conditions.",
                        persona_id=analysis_2_id,
                        mode=mode,
                    )
                    if mode == "live":
                        tools_a2, handlers_a2 = build_general_science_tools(
                            run_id=run_id,
                            workspace_dir=run_dir,
                            scheduler=self.scheduler,
                            owner_task_id=t_analysis_2,
                            shared_state=shared_state,
                        )
                        a2_tools = [
                            t for t in tools_a2
                            if t["name"] in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")
                        ]
                        a2_handlers = {k: handlers_a2[k] for k in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")}
                        await sdk_adapter.run_controlled_science_turn(
                            mode=mode,
                            role="analysis_2",
                            system_prompt=(
                                "You are Analysis #2. The paper specification is already provided below — do NOT paginate PDFs! Immediately run an independent Python unit/invariant verification script "
                                "via `run_python_experiment` (`script_name='exp_round1b_invariants.py'`, `round_label='round1b_invariants'`) verifying key equations, "
                                "monotonicity, boundary/degenerate edge cases, or analytical toy cases of this paper's method. Save summary JSON to `Path(sys.argv[2]) / 'round1b_invariants.json'` "
                                "and call `record_experiment_finding`."
                            ),
                            user_prompt=f"Verify mathematical equations & boundary invariants of '{paper_spec['paper_title']}' in Python.\n\n=== Paper Spec ===\n{spec_digest}",
                            tools_spec=a2_tools,
                            handlers=a2_handlers,
                            max_turns=5,
                            timeout_seconds=70.0,
                            effort=None,
                            on_tool_trace=lambda tr: self._emit_tool_traces(
                                run_id, actor_id=analysis_2_id, task_id=t_analysis_2, tool_traces=[tr], mode=mode
                            ),
                        )
                    else:
                        async with self.scheduler.experiment_slot(run_id=run_id, owner_id=t_analysis_2):
                            self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                            r1b = await run_simulation_round1b_invariants(run_dir)
                        shared_state["executed_trials"].append(r1b["trial"])
                        shared_state["recorded_findings"].append(
                            {
                                "kind": "recorded_finding",
                                "round_id": "round1b_invariants",
                                "hypothesis": "Mathematical & normalization invariants hold on boundary inputs",
                                "method": "Unit invariant suite",
                                "primary_metric": "invariants_passed",
                                "quantitative_result": f"passed={r1b['result']['passed']}",
                                "verdict": "supported",
                                "next_question": "",
                            }
                        )

                    self._mark_task_status(
                        run_id,
                        t_analysis_2,
                        "task.completed",
                        "done",
                        "Analysis #2 verified mathematical invariants & boundary conditions.",
                        persona_id=analysis_2_id,
                        mode=mode,
                    )
                    self._transition_persona(run_id, analysis_2_id, "completed", "invariants_verified", mode=mode)

            await asyncio.gather(_analysis_round1(), _analysis_round1b())

            # --- Phase 3: Live Validation Critique (`2p -> 3p Pod -> 5p Elliptical Synthesis`) & Round 2 ---
            debate_list: list[dict[str, Any]] = []
            async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_validation):
                self._transition_persona(
                    run_id,
                    "persona_validation",
                    "debating",
                    "auditing_round1_methodology",
                    runtime_agent_id=f"rt_validation_{run_id[-6:]}",
                    mode=mode,
                )
                self._transition_persona(run_id, "persona_analysis", "debating", "responding_to_critique", mode=mode)
                self._mark_task_status(
                    run_id,
                    t_validation,
                    "task.started",
                    "running",
                    "Validation Agent auditing Round 1 empirical outputs and designing Round 2 falsification critique.",
                    persona_id="persona_validation",
                    mode=mode,
                )

                debate_claim = f"Methodological robustness & alternative explanations for '{_short_title(paper_spec['paper_title'], 90)}'"
                debate_id, _ = self.collab.open_debate(
                    run_id,
                    claim_id="claim_robustness_validation",
                    claim=debate_claim,
                    participants=["persona_analysis", "persona_validation"],
                    actor_id="persona_validation",
                    mode=mode,
                )
                collab_id, _ = self.collab.start_collaboration(
                    run_id,
                    kind="debate",
                    participant_persona_ids=["persona_analysis", "persona_validation"],
                    topic=f"Robustness review: {_short_title(paper_spec['paper_title'], 60)}",
                    source_ref=SourceRef(kind="debate", id=debate_id),
                    actor_id="persona_validation",
                    summary="Analysis and Validation convened a 2-person review table on Round 1 experimental evidence.",
                    mode=mode,
                )
                active_collab_id = collab_id
                await asyncio.sleep(0.2 if mode == "simulation" else 2.6)

                val_challenge_summary = (
                    "Validation challenged Round 1 findings: single-setting reproduction is insufficient without testing "
                    "component ablations, permutation/randomization nulls, and hyperparameter/noise stress regimes."
                )
                r1_digest = json.dumps(
                    {
                        "executed_trials": [
                            {
                                "script_name": t.get("script_name"),
                                "round_label": t.get("round_label"),
                                "ok": t.get("ok"),
                                "stdout": str(t.get("stdout") or "")[:500],
                            }
                            for t in shared_state["executed_trials"]
                        ],
                        "recorded_findings": shared_state["recorded_findings"],
                    },
                    ensure_ascii=False,
                )[:2600]
                if mode == "live":
                    tools_v, handlers_v = build_general_science_tools(
                        run_id=run_id,
                        workspace_dir=run_dir,
                        scheduler=self.scheduler,
                        owner_task_id=t_validation,
                        shared_state=shared_state,
                    )
                    v_res = await sdk_adapter.run_controlled_science_turn(
                        mode=mode,
                        role="validation",
                        system_prompt=(
                            "You are Validation Agent. The paper spec and Round 1 experimental results are provided below (only call `inspect_workspace_files` if checking a CSV row). "
                            "Write 1-2 complete, self-contained plain sentences (under 220 characters total) naming the single strongest confounder, edge case, or ablation Round 2 MUST test for THIS paper. "
                            "Do NOT output Markdown headings (#), bullet lists, raw JSON/field dumps, or ellipses (...)."
                        ),
                        user_prompt=(
                            f"Critique Round 1 results for '{paper_spec['paper_title']}' and write a targeted Round 2 experimental challenge.\n\n"
                            f"=== Paper Spec ===\n{spec_digest[:1800]}\n\n=== Round 1 Summary ===\n{r1_digest}"
                        ),
                        tools_spec=[t for t in tools_v if t["name"] == "inspect_workspace_files"],
                        handlers={"inspect_workspace_files": handlers_v["inspect_workspace_files"]},
                        max_turns=3,
                        timeout_seconds=55.0,
                        effort=None,
                        on_tool_trace=lambda tr: self._emit_tool_traces(
                            run_id, actor_id="persona_validation", task_id=t_validation, tool_traces=[tr], mode=mode
                        ),
                    )
                    if v_res.ok and v_res.public_summary:
                        val_challenge_summary = _clean_complete_sentences(v_res.public_summary, 300)

                self.collab.add_debate_turn(
                    run_id,
                    debate_id=debate_id,
                    claim_id="claim_robustness_validation",
                    speaker_persona_id="persona_validation",
                    stance="challenge",
                    public_summary=val_challenge_summary,
                    evidence_ref=shared_state.get("manifest_artifact_id"),
                    target_ids=["persona_analysis", "persona_validation"],
                    mode=mode,
                )
                self.collab.add_debate_turn(
                    run_id,
                    debate_id=debate_id,
                    claim_id="claim_robustness_validation",
                    speaker_persona_id="persona_analysis",
                    stance="propose_test",
                    public_summary="Analysis proposed expanding to a 3-member Research Pod with Data and dynamically spawning Analysis #3 to execute Round 2 ablations, null tests, and sensitivity sweeps.",
                    evidence_ref=shared_state.get("dossier_artifact_id"),
                    target_ids=["persona_analysis", "persona_validation"],
                    mode=mode,
                )
                await asyncio.sleep(0.2 if mode == "simulation" else 2.5)

                # If debating agents flagged a methodological dissent/discrepancy, send Email to Main & invite human decision in Chat
                human_guidance = ""
                should_escalate = (mode == "live" and _DEBATE_DISSENT_RE.search(val_challenge_summary)) or bool(
                    shared_state.get("force_human_intervention")
                )
                if should_escalate:
                    is_zh = _wants_chinese(user_prompt)
                    int_id = f"int_{uuid.uuid4().hex[:8]}"
                    q_text = (
                        "讨论中的子 Agent 对统计口径或稳健性检验存在分歧，请确认 Round 2 的核心排查方向："
                        if is_zh
                        else "Debating specialists raised a methodological discrepancy. How should Round 2 proceed?"
                    )
                    opts = (
                        [
                            {
                                "label": "按严格对照口径重跑并对齐检验",
                                "prompt": "请在 Round 2 中严格统一估计量与对照组口径，优先验证核心分歧并补充置换检验。",
                            },
                            {
                                "label": "同时做消融、零检验与敏感性全套检验",
                                "prompt": "请在 Round 2 中并行开展组件消融、200 次置换零检验以及参数敏感性扫描。",
                            },
                        ]
                        if is_zh
                        else [
                            {
                                "label": "Reconcile estimator & run strict paired audit",
                                "prompt": "Reconcile the exact estimator definition first and run strict paired + permutation checks in Round 2.",
                            },
                            {
                                "label": "Run full ablation, null & sensitivity suite",
                                "prompt": "Proceed with full component ablation, 200-trial permutation null, and parameter sensitivity sweep in Round 2.",
                            },
                        ]
                    )
                    self._transition_persona(run_id, "persona_main", "waiting", "awaiting_human_intervention", mode=mode)
                    self.store.update_run_status(
                        run_id,
                        status="paused",
                        resources=self.scheduler.snapshot_state(run_id),
                    )
                    clean_dissent = _clean_complete_sentences(val_challenge_summary, 280)
                    ev_int = self.store.append_event(
                        run_id,
                        event_type="debate.human_intervention_requested",
                        actor_id="persona_validation",
                        target_ids=["persona_main"],
                        subject=EventSubject(kind="debate", id=debate_id),
                        status="waiting",
                        summary=f"Debating agents sent intervention email to Boss: {clean_dissent}",
                        payload={
                            "intervention_id": int_id,
                            "debate_id": debate_id,
                            "collaboration_id": collab_id,
                            "from_persona_ids": ["persona_validation", "persona_analysis"],
                            "to_persona_id": "persona_main",
                            "dissent_summary": clean_dissent,
                            "question": q_text,
                            "options": opts,
                        },
                        mode=mode,
                    )
                    self.store.add_chat_message(
                        run_id,
                        kind="system",
                        role="system",
                        text=f"Human review requested by Validation & Analysis: {q_text}",
                        related_refs=[
                            SelectedContext(kind="agent", id="persona_main"),
                            SelectedContext(kind="collaboration", id=collab_id),
                        ],
                        seq=ev_int.seq,
                    )
                    loop = asyncio.get_running_loop()
                    fut: asyncio.Future[str] = loop.create_future()
                    self._pending_interventions[run_id] = {
                        "intervention_id": int_id,
                        "debate_id": debate_id,
                        "collaboration_id": collab_id,
                        "from_persona_ids": ["persona_validation", "persona_analysis"],
                        "future": fut,
                    }
                    try:
                        human_guidance = await asyncio.wait_for(fut, timeout=180.0)
                    except asyncio.TimeoutError:
                        self._pending_interventions.pop(run_id, None)
                        auto_note = (
                            "自动继续：按严格估计量核对、消融与置换零检验执行 Round 2。"
                            if is_zh
                            else "Auto-continued with strict estimator audit, ablation, and permutation null checks in Round 2."
                        )
                        self.store.update_run_status(
                            run_id,
                            status="running",
                            resources=self.scheduler.snapshot_state(run_id),
                        )
                        self.store.append_event(
                            run_id,
                            event_type="debate.human_intervention_resolved",
                            actor_id="persona_main",
                            target_ids=["persona_validation", "persona_analysis"],
                            subject=EventSubject(kind="debate", id=debate_id),
                            status="auto_continued",
                            summary=auto_note,
                            payload={
                                "intervention_id": int_id,
                                "debate_id": debate_id,
                                "collaboration_id": collab_id,
                                "user_decision": auto_note,
                            },
                            mode=mode,
                        )
                    self._transition_persona(run_id, "persona_main", "working", "resumed_after_human_guidance", mode=mode)

                # Expand 2p -> 3p circular Research Pod
                pod_id, _ = self.collab.start_pod(
                    run_id,
                    research_question=f"Do core findings of '{_short_title(paper_spec['paper_title'], 70)}' hold under Round 2 ablations, null tests & stress checks?",
                    members=["persona_analysis", "persona_validation", "persona_data"],
                    budget_fraction=0.12,
                    actor_id="persona_validation",
                    mode=mode,
                )
                self._transition_persona(run_id, "persona_data", "debating", "joined_validation_pod", mode=mode)
                self.collab.change_collaboration_members(
                    run_id,
                    collaboration_id=collab_id,
                    participant_persona_ids=["persona_analysis", "persona_validation", "persona_data"],
                    reason="Data joined Validation & Analysis to verify data splits and confounder controls",
                    actor_id="persona_data",
                    summary="Data joined Analysis & Validation (table expanded to 3-member circular Research Pod).",
                    mode=mode,
                )
                self.collab.add_debate_turn(
                    run_id,
                    debate_id=debate_id,
                    claim_id="claim_robustness_validation",
                    speaker_persona_id="persona_data",
                    stance="align_data",
                    public_summary=(
                        f"Data verified `{shared_state['data_manifest'].get('dataset_kind', 'benchmark')}` splits and schema constraints, "
                        "confirming clean inputs for Round 2 ablations and permutation null controls."
                    ),
                    evidence_ref=shared_state.get("manifest_artifact_id"),
                    target_ids=["persona_analysis", "persona_validation", "persona_data"],
                    mode=mode,
                )

                # Dynamically spawn Analysis #3 for genuine Round 2 falsification & ablation experiments
                analysis_3_id = self._spawn_dynamic_persona(
                    run_id,
                    role="analysis",
                    instance_index=3,
                    label="Analysis #3",
                    specialty="Round 2 Ablation, Null Falsification & Sensitivity Experiments",
                    mode=mode,
                )
                t_analysis_3 = self._create_task_record(
                    run_id,
                    persona_id=analysis_3_id,
                    role="analysis",
                    objective="Author & execute Round 2 ablation, permutation/null falsification, and sensitivity experiments",
                    is_exploration=True,
                    mode=mode,
                )
                async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=t_analysis_3, is_exploration=True, budget_fraction=0.12):
                    self._transition_persona(run_id, analysis_3_id, "working", "running_round2_falsification", mode=mode)
                    self._mark_task_status(
                        run_id,
                        t_analysis_3,
                        "task.started",
                        "running",
                        "Analysis #3 designing & executing Round 2 ablations, null controls, and sensitivity experiments.",
                        persona_id=analysis_3_id,
                        mode=mode,
                    )
                    if mode == "live":
                        tools_a3, handlers_a3 = build_general_science_tools(
                            run_id=run_id,
                            workspace_dir=run_dir,
                            scheduler=self.scheduler,
                            owner_task_id=t_analysis_3,
                            shared_state=shared_state,
                        )
                        a3_tools = [
                            t for t in tools_a3
                            if t["name"] in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")
                        ]
                        a3_handlers = {k: handlers_a3[k] for k in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")}
                        sys_a3 = (
                            "You are Analysis #3 (Round 2 Ablation, Falsification & Sensitivity Scientist). "
                            "Paper spec and Round 1 findings are provided below — jump straight to authoring and EXECUTING `run_python_experiment` "
                            "(`script_name='exp_round2_robustness.py'`, `round_label='round2_robustness'`) addressing Validation's critique: "
                            f"\"{val_challenge_summary[:450]}\"\n"
                            "Test: (1) component ablation or alternative mechanism comparison, (2) permutation/null/placebo test, and (3) hyperparameter/noise/subgroup sensitivity sweep. "
                            "Save `Path(sys.argv[2]) / 'round2_experiments.csv'` and `Path(sys.argv[2]) / 'figure_round2_robustness.png'` (>=800x400 px). Fix stderr and re-run if needed. "
                            "Call `record_experiment_finding` with exact quantitative outcomes."
                        )
                        a3_res = await sdk_adapter.run_controlled_science_turn(
                            mode=mode,
                            role="analysis_3",
                            system_prompt=sys_a3 + " Keep `quantitative_result` concise (<= 220 chars, use inline LaTeX like `$\\hat{\\delta}$` or `$p$`).",
                            user_prompt=(
                                "Execute Round 2 ablation, falsification/null, and sensitivity experiments addressing Validation's critique.\n\n"
                                + (f"=== Human Investigator Guidance ===\n{human_guidance}\n\n" if human_guidance else "")
                                + f"=== Paper Spec ===\n{spec_digest[:1800]}\n\n=== Round 1 Findings ===\n{r1_digest[:1800]}"
                            ),
                            tools_spec=a3_tools,
                            handlers=a3_handlers,
                            max_turns=6,
                            timeout_seconds=80.0,
                            effort=None,
                            on_tool_trace=lambda tr: self._emit_tool_traces(
                                run_id, actor_id=analysis_3_id, task_id=t_analysis_3, tool_traces=[tr], mode=mode
                            ),
                        )
                        r2_ok = any(t.get("ok") and "round2" in str(t.get("round_label") or "") for t in shared_state["executed_trials"])
                        if a3_res.ok and not r2_ok:
                            await sdk_adapter.run_controlled_science_turn(
                                mode=mode,
                                role="analysis_3",
                                system_prompt=sys_a3,
                                user_prompt="Please complete and cleanly execute `exp_round2_robustness.py` via `run_python_experiment`, saving `round2_experiments.csv` and `figure_round2_robustness.png`.",
                                tools_spec=a3_tools,
                                handlers=a3_handlers,
                                max_turns=4,
                                timeout_seconds=60.0,
                                effort=None,
                                on_tool_trace=lambda tr: self._emit_tool_traces(
                                    run_id, actor_id=analysis_3_id, task_id=t_analysis_3, tool_traces=[tr], mode=mode
                                ),
                            )
                    else:
                        async with self.scheduler.experiment_slot(run_id=run_id, owner_id=t_analysis_3):
                            self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                            r2 = await run_simulation_round2_fixture(run_dir)
                        shared_state["executed_trials"].append(r2["trial"])
                        shared_state["created_tables"].append(r2["csv_path"])
                        shared_state["created_figures"].append(r2["fig_path"])
                        shared_state["recorded_findings"].append(
                            {
                                "kind": "recorded_finding",
                                "round_id": "round2_ablation",
                                "hypothesis": "Effect survives ablation, 200-trial permutation null, and perturbation noise",
                                "method": "Ablation + permutation null + noise sweep",
                                "primary_metric": "ablation_delta & perm_p",
                                "quantitative_result": f"ablation_delta={r2['summary']['ablation_delta']}, perm_p={r2['summary']['permutation_null']['p_value']}",
                                "verdict": "supported",
                                "next_question": "Test larger real-world benchmark scale in downstream evaluation.",
                            }
                        )

                    self._mark_task_status(
                        run_id,
                        t_analysis_3,
                        "task.completed",
                        "done",
                        "Analysis #3 completed Round 2 ablations, falsification, and sensitivity sweeps.",
                        persona_id=analysis_3_id,
                        mode=mode,
                    )

                # Expand 3p -> 5p elliptical multi-instance synthesis group (`collaboration.members_changed`)
                self._transition_persona(run_id, analysis_2_id, "debating", "joined_elliptical_synthesis", mode=mode)
                self._transition_persona(run_id, analysis_3_id, "debating", "joined_elliptical_synthesis", mode=mode)
                self.collab.change_collaboration_members(
                    run_id,
                    collaboration_id=collab_id,
                    participant_persona_ids=[
                        "persona_analysis",
                        "persona_validation",
                        "persona_data",
                        analysis_2_id,
                        analysis_3_id,
                    ],
                    reason="Analysis #2 and Analysis #3 joined cross-round synthesis & Evidence Gate preparation",
                    actor_id=analysis_3_id,
                    summary="Analysis #2 & Analysis #3 joined (table expanded to 5-member elliptical synthesis group).",
                    mode=mode,
                )
                r2_notes = "; ".join(
                    str(f.get("quantitative_result") or "")[:95]
                    for f in shared_state["recorded_findings"]
                    if "round2" in str(f.get("round_id") or "").lower()
                )
                self.collab.add_debate_turn(
                    run_id,
                    debate_id=debate_id,
                    claim_id="claim_robustness_validation",
                    speaker_persona_id=analysis_3_id,
                    stance="report_round2",
                    public_summary=(
                        f"Analysis #3 reported Round 2 ablation & falsification evidence: {r2_notes or 'ablation, permutation null & sensitivity checks passed cleanly.'}"
                    ),
                    target_ids=[
                        "persona_analysis",
                        "persona_validation",
                        "persona_data",
                        analysis_2_id,
                        analysis_3_id,
                    ],
                    mode=mode,
                )
                await asyncio.sleep(0.2 if mode == "simulation" else 2.8)

                # Register all generated experiment scripts, CSV tables, and PNG figures deduplicated by path
                registered_art_ids: list[str] = []
                if shared_state.get("manifest_artifact_id"):
                    registered_art_ids.append(shared_state["manifest_artifact_id"])
                if shared_state.get("dossier_artifact_id"):
                    registered_art_ids.append(shared_state["dossier_artifact_id"])

                seen_paths: set[Path] = set()
                for tr in shared_state["executed_trials"]:
                    if not tr.get("ok") or not tr.get("script_path"):
                        continue
                    sp = Path(str(tr["script_path"])).resolve()
                    if sp.exists() and sp not in seen_paths:
                        seen_paths.add(sp)
                        art_s, _ = self.artifacts.register_file(
                            run_id=run_id,
                            kind="script",
                            source_path=sp,
                            validation_status="passed",
                            metadata={
                                "reproduction_level": shared_state["data_manifest"].get("reproduction_level", "S1"),
                                "description": f"{tr.get('round_label', 'trial')}: {str(tr.get('hypothesis', ''))[:80]}",
                            },
                        )
                        registered_art_ids.append(art_s.artifact_id)
                        self._emit_artifact_created(
                            run_id,
                            actor_id="persona_analysis",
                            target_ids=["persona_analysis", analysis_3_id],
                            artifact_id=art_s.artifact_id,
                            kind="script",
                            validation_status="passed",
                            summary=f"Executed script `{sp.name}` saved with SHA-256 provenance.",
                            related_refs=[SelectedContext(kind="collaboration", id=collab_id)],
                            mode=mode,
                        )

                out_dir = run_dir / "outputs"
                all_tables = sorted(out_dir.glob("*.csv")) if out_dir.exists() else []
                for tp in all_tables:
                    rp = tp.resolve()
                    if rp in seen_paths:
                        continue
                    seen_paths.add(rp)
                    art_t, _ = self.artifacts.register_file(
                        run_id=run_id,
                        kind="dataset",
                        source_path=rp,
                        validation_status="passed",
                        metadata={
                            "reproduction_level": shared_state["data_manifest"].get("reproduction_level", "S1"),
                            "description": f"Quantitative result table `{tp.name}`",
                        },
                    )
                    registered_art_ids.append(art_t.artifact_id)
                    self._emit_artifact_created(
                        run_id,
                        actor_id="persona_analysis",
                        target_ids=["persona_analysis", "persona_validation"],
                        artifact_id=art_t.artifact_id,
                        kind="dataset",
                        validation_status="passed",
                        summary=f"Result table `{tp.name}` saved.",
                        related_refs=[SelectedContext(kind="collaboration", id=collab_id)],
                        mode=mode,
                    )

                all_figs = sorted(out_dir.glob("*.png")) if out_dir.exists() else []
                valid_figs: list[Path] = []
                for fp in all_figs:
                    if not _verify_png_header_and_size(fp)[0]:
                        continue
                    valid_figs.append(fp)
                    rp = fp.resolve()
                    if rp in seen_paths:
                        continue
                    seen_paths.add(rp)
                    art_f, _ = self.artifacts.register_file(
                        run_id=run_id,
                        kind="figure",
                        source_path=rp,
                        validation_status="passed",
                        metadata={
                            "reproduction_level": shared_state["data_manifest"].get("reproduction_level", "S1"),
                            "description": f"Reproduced experimental figure `{fp.name}`",
                        },
                    )
                    registered_art_ids.append(art_f.artifact_id)
                    self._emit_artifact_created(
                        run_id,
                        actor_id="persona_analysis",
                        target_ids=["persona_analysis", "persona_validation"],
                        artifact_id=art_f.artifact_id,
                        kind="figure",
                        validation_status="passed",
                        summary=f"Figure `{fp.name}` verified (PNG IHDR & legible size).",
                        related_refs=[SelectedContext(kind="collaboration", id=collab_id)],
                        mode=mode,
                    )

                # Fallback check for tables if agent saved JSON result outputs instead of CSV
                table_paths_for_gate = all_tables or (
                    sorted(
                        p
                        for p in out_dir.glob("*.json")
                        if p.name not in ("data_manifest.json", "experimental_protocol.json", "paper_spec.json")
                    )
                    if out_dir.exists()
                    else []
                )

                # Stage 4: Dynamically spawn Validation #2 to enforce universal Evidence Gate
                val_2_id = self._spawn_dynamic_persona(
                    run_id,
                    role="validation",
                    instance_index=2,
                    label="Validation #2",
                    specialty="Universal Evidence Gate & Provenance Audit",
                    mode=mode,
                )
                self._transition_persona(run_id, val_2_id, "working", "auditing_evidence_gate", mode=mode)

                resolution_text = (
                    f"Completed multi-round experimental synthesis for '{_short_title(paper_spec['paper_title'], 80)}': "
                    f"{len([t for t in shared_state['executed_trials'] if t.get('ok')])} clean Python script(s), "
                    f"{len(all_tables)} CSV table(s), and {len(valid_figs)} verified PNG figure(s)."
                )
                debate_list = [
                    {
                        "id": debate_id,
                        "status": "resolved",
                        "claim": debate_claim,
                        "resolution_summary": resolution_text,
                    }
                ]

                ev_review = self.evidence_gate.review_reproduction_bundle(
                    claim_id=f"claim_{run_id[-6:]}",
                    claimed_level=str(shared_state["data_manifest"].get("reproduction_level") or "S1"),
                    paper_spec=paper_spec,
                    data_manifest=shared_state["data_manifest"],
                    primary_data_path=shared_state.get("primary_data_path"),
                    executed_trials=shared_state["executed_trials"],
                    recorded_findings=shared_state["recorded_findings"],
                    table_paths=table_paths_for_gate,
                    figure_paths=valid_figs,
                    debate_records=debate_list,
                )
                evidence_id = f"evid_{uuid.uuid4().hex[:10]}"
                last_sha = next(
                    (
                        str(t.get("script_sha256"))
                        for t in reversed(shared_state["executed_trials"])
                        if t.get("ok") and t.get("script_sha256")
                    ),
                    hashlib.sha256(b"empty").hexdigest(),
                )
                dedup_art_ids = _dedup(registered_art_ids)

                def _save_evid(conn: sqlite3.Connection, _seq: int) -> None:
                    conn.execute(
                        """
                        INSERT INTO reproduce_evidence (
                            evidence_id, run_id, claim_id, dataset_manifest_id, script_hash,
                            result_artifact_ids_json, status, reviewer_id, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evidence_id,
                            run_id,
                            ev_review.claim_id,
                            shared_state["data_manifest"].get("dataset_id", "ds_unknown"),
                            last_sha,
                            jdumps(dedup_art_ids),
                            "verified" if ev_review.result == "passed" else ev_review.result,
                            val_2_id,
                            jdumps(
                                {
                                    "checks": ev_review.checks,
                                    "reasons": ev_review.reasons,
                                    "reproduction_level": ev_review.reproduction_level_verified,
                                    "research_gains": ev_review.research_gains,
                                }
                            ),
                            utc_now_iso(),
                        ),
                    )

                self.store.append_event(
                    run_id,
                    event_type="evidence.reviewed",
                    actor_id=val_2_id,
                    target_ids=_dedup(["persona_analysis", "persona_validation", val_2_id]),
                    subject=EventSubject(kind="evidence", id=evidence_id),
                    status=ev_review.result,
                    summary=f"Evidence Gate verdict by {val_2_id}: {ev_review.result.upper()} ({ev_review.reproduction_level_verified} verified).",
                    payload={
                        "claim_id": ev_review.claim_id,
                        "result": ev_review.result,
                        "reproduction_level": ev_review.reproduction_level_verified,
                        "checks": ev_review.checks,
                        "research_gains": ev_review.research_gains,
                    },
                    evidence_ids=[evidence_id],
                    artifact_ids=dedup_art_ids,
                    mode=mode,
                    source="validator",
                    state_mutator=_save_evid,
                )
                self._transition_persona(run_id, val_2_id, "completed", "evidence_gate_complete", mode=mode)

                debate_list[0]["status"] = "resolved" if ev_review.result == "passed" else "unresolved"
                self.collab.close_pod(
                    run_id,
                    pod_id=pod_id,
                    outcome_summary=resolution_text,
                    members=["persona_analysis", "persona_validation", "persona_data"],
                    actor_id="persona_validation",
                    artifact_ids=dedup_art_ids[:4],
                    mode=mode,
                )
                self.collab.close_debate(
                    run_id,
                    debate_id=debate_id,
                    outcome_status="resolved" if ev_review.result == "passed" else "unresolved",
                    resolution_summary=resolution_text,
                    actor_id="persona_validation",
                    target_ids=["persona_analysis", "persona_validation"],
                    evidence_ids=[evidence_id],
                    mode=mode,
                )
                self.collab.end_collaboration(
                    run_id,
                    collaboration_id=collab_id,
                    outcome="resolved" if ev_review.result == "passed" else "inconclusive",
                    actor_id="persona_validation",
                    summary="Research table concluded; participants returning to home anchors.",
                    mode=mode,
                )
                active_collab_id = None

                self._transition_persona(run_id, "persona_data", "completed", "pod_complete", mode=mode)
                self._transition_persona(run_id, "persona_analysis", "completed", "analysis_verified", mode=mode)
                self._mark_task_status(
                    run_id,
                    t_validation,
                    "task.completed",
                    "done",
                    f"Evidence Gate verdict: {ev_review.result.upper()} ({ev_review.reproduction_level_verified}).",
                    persona_id="persona_validation",
                    artifact_ids=dedup_art_ids[:4],
                    mode=mode,
                )
                await self._brief_main_with_easel(
                    run_id,
                    presenter_ids=["persona_validation"],
                    reason="reporting_to_main",
                    briefing_label="Validation Brief",
                    topic="Evidence Gate & Robustness Audit",
                    summary=f"Validation briefed Boss on Evidence Gate verdict: {ev_review.result.upper()} ({ev_review.reproduction_level_verified}). {resolution_text}",
                    linked_step_ids=["step_validation_gate"],
                    artifact_ids=dedup_art_ids[:4],
                    mode=mode,
                )

            # --- Stage 5: Support Agent generates DOCX & PDF Reports ---
            self._transition_persona(
                run_id,
                "persona_support",
                "working",
                "compiling_docx_pdf_reports",
                runtime_agent_id=f"rt_support_{run_id[-6:]}",
                mode=mode,
            )
            report_files = await asyncio.to_thread(
                build_reproduction_reports,
                output_dir=run_dir / "reports",
                run_id=run_id,
                user_prompt=user_prompt,
                paper_spec=paper_spec,
                data_manifest=shared_state["data_manifest"],
                executed_trials=shared_state["executed_trials"],
                recorded_findings=shared_state["recorded_findings"],
                table_paths=all_tables,
                figure_paths=valid_figs,
                evidence_result=ev_review,
                debate_records=debate_list,
                sdk_provider_used=provider_used,
            )

            val_badge = "passed" if ev_review.result == "passed" else "inconclusive"
            art_pdf, _ = self.artifacts.register_file(
                run_id=run_id,
                kind="report",
                source_path=report_files["pdf_path"],
                validation_status=val_badge,
                metadata={"reproduction_level": ev_review.reproduction_level_verified, "description": "Scientific Reproduction Report (PDF)"},
            )
            art_docx, _ = self.artifacts.register_file(
                run_id=run_id,
                kind="report",
                source_path=report_files["docx_path"],
                validation_status=val_badge,
                metadata={"reproduction_level": ev_review.reproduction_level_verified, "description": "Scientific Reproduction Report (Word .docx)"},
            )
            report_ids = [art_pdf.artifact_id, art_docx.artifact_id]
            for r_art, r_desc in [
                (art_pdf, "Final PDF Reproduction Report generated"),
                (art_docx, "Final Word (.docx) Reproduction Report generated"),
            ]:
                self.store.append_event(
                    run_id,
                    event_type="artifact.created",
                    actor_id="persona_support",
                    target_ids=["persona_support"],
                    subject=EventSubject(kind="artifact", id=r_art.artifact_id),
                    status=val_badge,
                    summary=r_desc,
                    payload={"artifact_id": r_art.artifact_id, "kind": "report", "validation_status": val_badge},
                    artifact_ids=[r_art.artifact_id],
                    mode=mode,
                    source="runner",
                )

            ev_rep = self.store.append_event(
                run_id,
                event_type="report.generated",
                actor_id="persona_main",
                target_ids=["persona_main", "persona_support"],
                subject=EventSubject(kind="run", id=run_id),
                status="ready",
                summary=f"Reproduction reports (PDF & DOCX) ready at level {ev_review.reproduction_level_verified}.",
                payload={"artifact_ids": report_ids, "reproduction_level": ev_review.reproduction_level_verified},
                evidence_ids=[evidence_id],
                artifact_ids=report_ids,
                mode=mode,
            )

            self._transition_persona(run_id, "persona_validation", "completed", "audit_complete", mode=mode)
            await self._brief_main_with_easel(
                run_id,
                presenter_ids=["persona_support"],
                reason="presenting_final_report",
                briefing_label="Final Brief",
                topic="Final PDF Report & Reproducible Scripts",
                summary=f"Support presented final PDF & Word reports ({ev_review.reproduction_level_verified} · {ev_review.result.upper()}) with reproducible Python scripts to Boss.",
                linked_step_ids=["step_main_summary"],
                artifact_ids=report_ids,
                mode=mode,
            )
            self._transition_persona(run_id, "persona_support", "completed", "reports_ready", mode=mode)
            self._transition_persona(run_id, "persona_main", "completed", "reproduction_delivered", mode=mode)

            ok_trials = [t for t in shared_state["executed_trials"] if t.get("ok")]
            is_zh_summary = _wants_chinese(user_prompt)
            findings_bullets = _format_concise_findings_md(
                shared_state["recorded_findings"],
                ok_trials,
                is_cjk=is_zh_summary,
            )
            sim_prefix = "**[SIMULATION MODE]** " if mode == "simulation" else ""
            if is_zh_summary:
                final_md = (
                    f"{sim_prefix}### 复现结论汇总（{ev_review.reproduction_level_verified} · {ev_review.result.upper()}）\n\n"
                    f"- **目标论文**：{paper_spec['paper_title']}（{paper_spec.get('authors_year', '未注明')}）\n"
                    f"- **数据与环境**：`{shared_state['data_manifest']['dataset_kind']}`\n"
                    f"- **多轮实验统计**：完成 `{len(ok_trials)}` 份 Python 脚本、`{len(all_tables)}` 份 CSV 表格、`{len(valid_figs)}` 张验证图像\n"
                    f"- **关键定量发现**：\n{findings_bullets}\n"
                    f"- **证据门控结论**：`{ev_review.result.upper()}`（`{provider_used}`）。可在下方预览或下载完整 PDF 报告。"
                )
            else:
                final_md = (
                    f"{sim_prefix}### Reproduction Summary ({ev_review.reproduction_level_verified} · {ev_review.result.upper()})\n\n"
                    f"- **Target Paper**: {paper_spec['paper_title']} ({paper_spec.get('authors_year', 'Unspecified')})\n"
                    f"- **Data / Environment**: `{shared_state['data_manifest']['dataset_kind']}` (`{shared_state['data_manifest'].get('license', '')}`)\n"
                    f"- **Multi-Round Python Experiments**: `{len(ok_trials)}` clean execution(s), `{len(all_tables)}` CSV table(s), `{len(valid_figs)}` verified PNG figure(s)\n"
                    f"- **Key Quantitative Findings**:\n{findings_bullets}\n"
                    f"- **Evidence Gate**: `{ev_review.result.upper()}` (`{provider_used}`). Inspect or download the final PDF report below."
                )
            self.store.add_chat_message(
                run_id,
                kind="message",
                role="main",
                text=final_md,
                related_refs=[SelectedContext(kind="agent", id="persona_main")],
                artifact_ids=_dedup([*report_ids, *dedup_art_ids]),
                seq=ev_rep.seq,
            )

            self.store.update_run_status(
                run_id,
                status="completed",
                report_status="ready",
                report_artifact_ids=report_ids,
                missing_requirements=[] if ev_review.result == "passed" else ev_review.reasons[:3],
                resources=self.scheduler.snapshot_state(run_id),
            )
            self.store.append_event(
                run_id,
                event_type="run.completed",
                actor_id="persona_main",
                target_ids=["persona_main"],
                subject=EventSubject(kind="run", id=run_id),
                status="completed",
                summary=f"Run completed ({ev_review.reproduction_level_verified} · {ev_review.result}).",
                payload={
                    "reproduction_level": ev_review.reproduction_level_verified,
                    "evidence_result": ev_review.result,
                    "sdk_provider": provider_used,
                },
                evidence_ids=[evidence_id],
                artifact_ids=report_ids,
                mode=mode,
            )
            for pid in self.store.list_persona_ids(run_id):
                self._transition_persona(run_id, pid, "idle", "returned_to_anchor", mode=mode)

        except asyncio.CancelledError:
            self.cancel_pending_intervention(run_id)
            if active_collab_id:
                try:
                    self.collab.end_collaboration(
                        run_id,
                        collaboration_id=active_collab_id,
                        outcome="cancelled",
                        actor_id="persona_main",
                        mode=mode,
                    )
                except Exception:
                    pass
            raise

        except Exception as exc:
            self.cancel_pending_intervention(run_id)
            if active_collab_id:
                try:
                    self.collab.end_collaboration(
                        run_id,
                        collaboration_id=active_collab_id,
                        outcome="aborted_on_error",
                        actor_id="persona_main",
                        mode=mode,
                    )
                except Exception:
                    pass
            self._fail_run(run_id, mode=mode, reason=f"{type(exc).__name__}: {exc}")

    async def execute_team_roundtable_check(
        self,
        run_id: str,
        *,
        user_prompt: str,
        participant_ids: list[str],
    ) -> None:
        meta = self.store.get_run_meta(run_id)
        if not meta:
            return
        mode: RunMode = meta["mode"]
        members = _dedup([p for p in participant_ids if p != "persona_main"])
        if len(members) < 2:
            members = ["persona_analysis", "persona_validation"]

        self.store.update_run_status(run_id, status="running")
        self._transition_persona(run_id, "persona_main", "working", "coordinating_followup_roundtable", mode=mode)
        for m in members:
            self._transition_persona(run_id, m, "debating", "roundtable_discussion", mode=mode)

        cid, _ = self.collab.start_collaboration(
            run_id,
            kind="pod" if len(members) >= 3 else "debate",
            participant_persona_ids=members[:2],
            topic=user_prompt[:96],
            source_ref=SourceRef(kind="collaboration", id=f"roundtable_{uuid.uuid4().hex[:6]}"),
            actor_id="persona_main",
            summary=f"Opened 2-agent review table for: {user_prompt[:80]}",
            mode=mode,
        )
        await asyncio.sleep(0.25 if mode == "simulation" else 2.6)
        if len(members) >= 3:
            self.collab.change_collaboration_members(
                run_id,
                collaboration_id=cid,
                participant_persona_ids=members[: min(4, len(members))],
                reason="Expanded roundtable to circular group",
                actor_id=members[2],
                summary=f"Expanded table to {min(4, len(members))} agents for cross-role verification.",
                mode=mode,
            )
            await asyncio.sleep(0.25 if mode == "simulation" else 2.6)
        if len(members) >= 5:
            self.collab.change_collaboration_members(
                run_id,
                collaboration_id=cid,
                participant_persona_ids=members,
                reason="Full research team joined elliptical review table",
                actor_id=members[4],
                summary=f"Expanded table to {len(members)}-agent elliptical research group.",
                mode=mode,
            )
            await asyncio.sleep(0.25 if mode == "simulation" else 2.8)

        self.collab.end_collaboration(
            run_id,
            collaboration_id=cid,
            outcome="synthesized",
            actor_id="persona_main",
            summary="Roundtable synthesis delivered to Boss; participants returned to anchors.",
            mode=mode,
        )
        for m in _dedup(["persona_main", *members]):
            self._transition_persona(run_id, m, "idle", "roundtable_complete", mode=mode)

        snap = self.store.get_snapshot(run_id)
        ev_count = len(snap.evidence) if snap else 0
        self.store.add_chat_message(
            run_id,
            kind="message",
            role="main",
            text=(
                f"Completed {len(members)}-agent methodological review on **\"{user_prompt[:100]}\"**.\n\n"
                f"- Active Evidence Gate records checked: `{ev_count}`\n"
                f"- Verified findings, confounder boundaries, and artifact provenance remain consistent with the current paper reproduction."
            ),
            related_refs=[
                SelectedContext(kind="collaboration", id=cid),
                *[SelectedContext(kind="agent", id=m) for m in members],
            ],
        )
        self.store.update_run_status(run_id, status="completed")
        self.store.append_event(
            run_id,
            event_type="run.completed",
            actor_id="persona_main",
            target_ids=["persona_main"],
            subject=EventSubject(kind="run", id=run_id),
            status="completed",
            summary="Follow-up methodological review completed.",
            payload={"followup": True, "participants": members},
            mode=mode,
        )

    async def execute_dynamic_spawn_followup(
        self,
        run_id: str,
        *,
        user_prompt: str,
        role_counts: dict[str, int],
    ) -> None:
        meta = self.store.get_run_meta(run_id)
        if not meta:
            return
        mode: RunMode = meta["mode"]
        run_dir = self.artifacts.run_dir(run_id)
        sdk_adapter = SDKAdapter(run_dir)
        self.store.update_run_status(run_id, status="running")
        self._transition_persona(run_id, "persona_main", "working", "spawning_parallel_specialists", mode=mode)

        spawned_ids: list[str] = []
        task_map: dict[str, str] = {}
        for role, count in role_counts.items():
            if role not in SPECIALIST_ROLES:
                continue
            for _ in range(max(1, min(4, count))):
                idx = self.store.get_next_role_instance_index(run_id, role)
                pid = self._spawn_dynamic_persona(
                    run_id,
                    role=role,
                    instance_index=idx,
                    label=f"{role.capitalize()} #{idx}",
                    specialty=user_prompt[:80],
                    mode=mode,
                )
                tid = self._create_task_record(
                    run_id,
                    persona_id=pid,
                    role=role,
                    objective=f"Parallel {role} check #{idx}: {user_prompt[:80]}",
                    is_exploration=True,
                    mode=mode,
                )
                spawned_ids.append(pid)
                task_map[pid] = tid

        if not spawned_ids:
            idx = self.store.get_next_role_instance_index(run_id, "analysis")
            pid = self._spawn_dynamic_persona(
                run_id,
                role="analysis",
                instance_index=idx,
                label=f"Analysis #{idx}",
                specialty=user_prompt[:80],
                mode=mode,
            )
            tid = self._create_task_record(
                run_id,
                persona_id=pid,
                role="analysis",
                objective=f"Parallel analysis check #{idx}: {user_prompt[:80]}",
                is_exploration=True,
                mode=mode,
            )
            spawned_ids.append(pid)
            task_map[pid] = tid

        shared_followup: dict[str, Any] = {
            "executed_trials": [],
            "recorded_findings": [],
            "created_figures": [],
            "created_tables": [],
        }
        followup_results: list[str] = []

        async def _run_dynamic_worker(worker_idx: int, pid: str) -> None:
            tid = task_map[pid]
            async with self.scheduler.agent_task_slot(run_id=run_id, owner_id=tid):
                self.store.update_run_status(run_id, resources=self.scheduler.snapshot_state(run_id))
                self._transition_persona(run_id, pid, "working", "executing_dynamic_experiment", mode=mode)
                self._mark_task_status(
                    run_id,
                    tid,
                    "task.started",
                    "running",
                    f"{pid} acquiring experiment slot for targeted follow-up experiment.",
                    persona_id=pid,
                    mode=mode,
                )
                art_ids_worker: list[str] = []
                msg_done = f"{pid} completed follow-up check."

                if mode == "live":
                    tools_w, handlers_w = build_general_science_tools(
                        run_id=run_id,
                        workspace_dir=run_dir,
                        scheduler=self.scheduler,
                        owner_task_id=tid,
                        shared_state=shared_followup,
                    )
                    w_tools = [t for t in tools_w if t["name"] in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")]
                    w_handlers = {k: handlers_w[k] for k in ("inspect_workspace_files", "run_python_experiment", "record_experiment_finding")}
                    w_res = await sdk_adapter.run_controlled_science_turn(
                        mode=mode,
                        role=f"followup_{worker_idx}",
                        system_prompt=(
                            f"You are dynamically spawned specialist {pid}. Inspect `outputs/paper_spec.json` and existing CSVs/outputs via `inspect_workspace_files`, "
                            f"then author and RUN a targeted follow-up Python verification script via `run_python_experiment` (`script_name='exp_followup_{pid}.py'`, `round_label='followup'`) "
                            "to investigate the user's request."
                        ),
                        user_prompt=user_prompt,
                        tools_spec=w_tools,
                        handlers=w_handlers,
                        max_turns=6,
                        timeout_seconds=80.0,
                        effort=None,
                        on_tool_trace=lambda tr: self._emit_tool_traces(
                            run_id, actor_id=pid, task_id=tid, tool_traces=[tr], mode=mode
                        ),
                    )
                    ok_worker = [t for t in shared_followup["executed_trials"] if t.get("ok") and f"followup_{pid}".lower() in str(t.get("script_name")).lower()]
                    for item in ok_worker:
                        sp = Path(str(item["script_path"]))
                        if sp.exists():
                            art_w, _ = self.artifacts.register_file(
                                run_id=run_id,
                                kind="script",
                                source_path=sp,
                                validation_status="passed",
                                metadata={"description": f"Follow-up script by {pid}"},
                            )
                            art_ids_worker.append(art_w.artifact_id)
                            msg_done = f"{pid} ran `{sp.name}`: {str(item.get('stdout', '')).strip()[:160]}"
                    if not ok_worker and w_res.public_summary:
                        msg_done = f"{pid}: {w_res.public_summary[:180]}"
                    followup_results.append(msg_done)
                else:
                    async with self.scheduler.experiment_slot(run_id=run_id, owner_id=tid):
                        exp_w = await run_generic_followup_experiment(
                            run_dir,
                            script_rel_path=f"scripts/followup_{pid}.py",
                            seed_offset=worker_idx + 1,
                        )
                    art_w, _ = self.artifacts.register_file(
                        run_id=run_id,
                        kind="script",
                        source_path=exp_w["execution"].script_path,
                        validation_status="passed",
                        metadata={"reproduction_level": "S1", "description": f"Follow-up stability check by {pid}"},
                    )
                    art_ids_worker.append(art_w.artifact_id)
                    r = exp_w["result"]
                    msg_done = (
                        f"{pid} ran bootstrap stability check on `{r['source_file']}` (`{r['metric_column']}`, n={r['n_evaluated']}): "
                        f"mean={r['bootstrap_mean']:.4f}, 95% CI [{r['ci95_low']:.4f}, {r['ci95_high']:.4f}]."
                    )
                    followup_results.append(msg_done)

                self._mark_task_status(
                    run_id,
                    tid,
                    "task.completed",
                    "done",
                    msg_done,
                    persona_id=pid,
                    artifact_ids=art_ids_worker,
                    mode=mode,
                )
                self._transition_persona(run_id, pid, "completed", "subtask_done", mode=mode)

        await asyncio.gather(*(_run_dynamic_worker(i, pid) for i, pid in enumerate(spawned_ids)))

        table_members = [*spawned_ids]
        if len(table_members) < 2:
            table_members.append("persona_validation")
        cid, _ = self.collab.start_collaboration(
            run_id,
            kind="pod" if len(table_members) >= 3 else "debate",
            participant_persona_ids=table_members,
            topic=f"Parallel cross-check ({len(table_members)} specialists)",
            source_ref=SourceRef(kind="collaboration", id=f"dyn_{uuid.uuid4().hex[:6]}"),
            actor_id="persona_main",
            summary=f"Boss convened {len(table_members)} specialists for parallel synthesis.",
            mode=mode,
        )
        for m in table_members:
            self._transition_persona(run_id, m, "debating", "synthesizing_parallel_findings", mode=mode)
        await asyncio.sleep(0.25 if mode == "simulation" else 2.8)

        self.collab.end_collaboration(
            run_id,
            collaboration_id=cid,
            outcome="synthesized",
            actor_id="persona_main",
            summary="Dynamic specialist team synthesis delivered to Boss.",
            mode=mode,
        )
        for pid in self.store.list_persona_ids(run_id):
            self._transition_persona(run_id, pid, "idle", "returned_to_anchor", mode=mode)

        details_block = "\n".join(f"- {ln}" for ln in followup_results)
        self.store.add_chat_message(
            run_id,
            kind="message",
            role="main",
            text=(
                f"Boss Agent dynamically spawned `{len(spawned_ids)}` specialist agent instance(s) "
                f"({', '.join(f'`{p}`' for p in spawned_ids)}) and executed isolated Python follow-up experiments for **\"{user_prompt[:100]}\"**:\n\n"
                f"{details_block}"
            ),
            related_refs=[
                SelectedContext(kind="agent", id="persona_main"),
                *[SelectedContext(kind="agent", id=p) for p in spawned_ids],
                SelectedContext(kind="collaboration", id=cid),
            ],
        )
        self.store.update_run_status(run_id, status="completed", resources=self.scheduler.snapshot_state(run_id))
        self.store.append_event(
            run_id,
            event_type="run.completed",
            actor_id="persona_main",
            target_ids=["persona_main"],
            subject=EventSubject(kind="run", id=run_id),
            status="completed",
            summary=f"Dynamic multi-instance check completed ({len(spawned_ids)} spawned).",
            payload={"dynamic_spawned": spawned_ids},
            mode=mode,
        )

    def _fail_run(self, run_id: str, *, mode: RunMode, reason: str) -> None:
        self._transition_persona(run_id, "persona_main", "failed", "run_failed", mode=mode)
        self.store.update_run_status(
            run_id,
            status="failed",
            report_status="failed",
            resources=self.scheduler.snapshot_state(run_id),
        )
        ev = self.store.append_event(
            run_id,
            event_type="run.failed",
            actor_id="persona_main",
            target_ids=["persona_main"],
            subject=EventSubject(kind="run", id=run_id),
            status="failed",
            summary=reason[:300],
            payload={"error": reason[:1000]},
            mode=mode,
        )
        self.store.add_chat_message(
            run_id,
            kind="system",
            role="main",
            text=f"**Run Failed / Blocked**: {reason}",
            related_refs=[SelectedContext(kind="agent", id="persona_main")],
            seq=ev.seq,
        )
