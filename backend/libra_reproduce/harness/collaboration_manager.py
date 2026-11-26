from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from ..schemas import (
    CollaborationKindLiteral,
    EventSubject,
    HarnessEvent,
    RunMode,
    SelectedContext,
    SourceRef,
)
from ..storage.db import jdumps, jloads, utc_now_iso
from ..storage.event_store import EventStore


class CollaborationManager:
    """
    Domain & visual-projection state machine for Debate, Research Pod, and Temporary Research Table
    (`collaboration.started`, `collaboration.members_changed`, `collaboration.ended`).
    Guarantees:
    - Minimum 2 non-Main participants to open a table (`is_main` never joins ordinary research tables).
    - Monotonic `revision` (`1 -> 2 -> 3 ...`) per `collaboration_id`, conforming strictly to
      `contracts/collaboration-payload.schema.json`.
    - `debate.opened` / `pod.proposed` record domain truth without duplicating tables;
      a table exists ONLY while `collaboration.started` .. `collaboration.ended` is active.
    """

    def __init__(self, event_store: EventStore) -> None:
        self.store = event_store

    def open_debate(
        self,
        run_id: str,
        *,
        claim_id: str,
        claim: str,
        participants: list[str],
        actor_id: str,
        mode: RunMode | None = None,
        debate_id: str | None = None,
    ) -> tuple[str, HarnessEvent]:
        did = debate_id or f"debate_{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                INSERT INTO reproduce_debates (
                    debate_id, run_id, claim_id, claim, participants_json,
                    turns_json, proposed_experiments_json, status,
                    resolution_summary, updated_seq, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '[]', '[]', 'opened', '', ?, ?, ?)
                """,
                (did, run_id, claim_id, claim, jdumps(participants), seq, now, now),
            )

        ev = self.store.append_event(
            run_id,
            event_type="debate.opened",
            actor_id=actor_id,
            target_ids=participants,
            subject=EventSubject(kind="debate", id=did),
            status="opened",
            summary=f"Methodological review opened: {claim}",
            payload={"debate_id": did, "claim_id": claim_id, "claim": claim},
            mode=mode,
            state_mutator=_mut,
        )
        return did, ev

    def add_debate_turn(
        self,
        run_id: str,
        *,
        debate_id: str,
        claim_id: str,
        speaker_persona_id: str,
        stance: str,
        public_summary: str,
        evidence_ref: str | None = None,
        target_ids: list[str] | None = None,
        mode: RunMode | None = None,
    ) -> HarnessEvent:
        turn_obj = {
            "speaker": speaker_persona_id,
            "stance": stance,
            "public_summary": public_summary,
            "evidence_ref": evidence_ref,
            "at": utc_now_iso(),
        }

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            row = conn.execute(
                "SELECT turns_json FROM reproduce_debates WHERE debate_id = ?", (debate_id,)
            ).fetchone()
            turns = jloads(row["turns_json"], []) if row else []
            turns.append(turn_obj)
            conn.execute(
                "UPDATE reproduce_debates SET turns_json = ?, status = 'active', updated_seq = ?, updated_at = ? "
                "WHERE debate_id = ?",
                (jdumps(turns), seq, utc_now_iso(), debate_id),
            )

        return self.store.append_event(
            run_id,
            event_type="debate.turn_added",
            actor_id=speaker_persona_id,
            target_ids=target_ids or [speaker_persona_id],
            subject=EventSubject(kind="debate", id=debate_id),
            status="active",
            summary=public_summary,
            payload={
                "debate_id": debate_id,
                "claim_id": claim_id,
                "stance": stance,
                "public_summary": public_summary,
                "evidence_ref": evidence_ref,
            },
            evidence_ids=[evidence_ref] if evidence_ref else [],
            mode=mode,
            state_mutator=_mut,
        )

    def close_debate(
        self,
        run_id: str,
        *,
        debate_id: str,
        outcome_status: str,
        resolution_summary: str,
        actor_id: str,
        target_ids: list[str],
        evidence_ids: list[str] | None = None,
        mode: RunMode | None = None,
    ) -> HarnessEvent:
        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                "UPDATE reproduce_debates SET status = ?, resolution_summary = ?, updated_seq = ?, updated_at = ? "
                "WHERE debate_id = ?",
                (outcome_status, resolution_summary, seq, utc_now_iso(), debate_id),
            )

        return self.store.append_event(
            run_id,
            event_type="debate.closed",
            actor_id=actor_id,
            target_ids=target_ids,
            subject=EventSubject(kind="debate", id=debate_id),
            status=outcome_status,
            summary=resolution_summary,
            payload={"debate_id": debate_id, "outcome": outcome_status, "resolution_summary": resolution_summary},
            evidence_ids=evidence_ids or [],
            mode=mode,
            state_mutator=_mut,
        )

    def start_pod(
        self,
        run_id: str,
        *,
        research_question: str,
        members: list[str],
        budget_fraction: float,
        actor_id: str,
        mode: RunMode | None = None,
        pod_id: str | None = None,
    ) -> tuple[str, HarnessEvent]:
        pid = pod_id or f"pod_{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                INSERT INTO reproduce_pods (
                    pod_id, run_id, research_question, members_json, budget_fraction,
                    status, active_tasks_json, outcome_summary, updated_seq, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', '[]', '', ?, ?, ?)
                """,
                (pid, run_id, research_question, jdumps(members), budget_fraction, seq, now, now),
            )

        ev = self.store.append_event(
            run_id,
            event_type="pod.started",
            actor_id=actor_id,
            target_ids=members,
            subject=EventSubject(kind="pod", id=pid),
            status="active",
            summary=f"Research Pod started ({int(budget_fraction * 100)}% exploration budget): {research_question}",
            payload={
                "pod_id": pid,
                "research_question": research_question,
                "members": members,
                "budget_fraction": budget_fraction,
            },
            mode=mode,
            state_mutator=_mut,
        )
        return pid, ev

    def close_pod(
        self,
        run_id: str,
        *,
        pod_id: str,
        outcome_summary: str,
        members: list[str],
        actor_id: str,
        artifact_ids: list[str] | None = None,
        mode: RunMode | None = None,
    ) -> HarnessEvent:
        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                "UPDATE reproduce_pods SET status = 'closed', outcome_summary = ?, updated_seq = ?, updated_at = ? "
                "WHERE pod_id = ?",
                (outcome_summary, seq, utc_now_iso(), pod_id),
            )

        return self.store.append_event(
            run_id,
            event_type="pod.closed",
            actor_id=actor_id,
            target_ids=members,
            subject=EventSubject(kind="pod", id=pod_id),
            status="closed",
            summary=outcome_summary,
            payload={"pod_id": pod_id, "outcome_summary": outcome_summary},
            artifact_ids=artifact_ids or [],
            mode=mode,
            state_mutator=_mut,
        )

    def start_collaboration(
        self,
        run_id: str,
        *,
        kind: CollaborationKindLiteral,
        participant_persona_ids: list[str],
        topic: str,
        source_ref: SourceRef,
        actor_id: str,
        summary: str | None = None,
        mode: RunMode | None = None,
        collaboration_id: str | None = None,
    ) -> tuple[str, HarnessEvent]:
        clean_members = [p for p in dict.fromkeys(participant_persona_ids) if p != "persona_main"]
        if len(clean_members) < 2:
            raise ValueError("Temporary research table requires at least 2 non-Main personas.")
        cid = collaboration_id or f"collab_{uuid.uuid4().hex[:8]}"

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                INSERT INTO reproduce_collaborations (
                    collaboration_id, run_id, kind, status, topic,
                    participant_persona_ids_json, ref_json, revision, last_event_seq
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, 1, ?)
                """,
                (cid, run_id, kind, topic, jdumps(clean_members), source_ref.model_dump_json(), seq),
            )

        ev = self.store.append_event(
            run_id,
            event_type="collaboration.started",
            actor_id=actor_id,
            target_ids=clean_members,
            subject=EventSubject(kind="collaboration", id=cid),
            status="active",
            summary=summary or f"Temporary {kind} table formed: {topic}",
            payload={
                "collaboration_id": cid,
                "kind": kind,
                "participant_persona_ids": clean_members,
                "topic": topic,
                "source_ref": source_ref.model_dump(),
                "revision": 1,
            },
            mode=mode,
            state_mutator=_mut,
        )
        self.store.add_chat_message(
            run_id,
            kind="activity",
            role="activity",
            text=ev.summary,
            related_refs=[
                SelectedContext(kind="collaboration", id=cid),
                SelectedContext(kind=source_ref.kind, id=source_ref.id),
                *[SelectedContext(kind="agent", id=pid) for pid in clean_members],
            ],
            seq=ev.seq,
        )
        return cid, ev

    def change_collaboration_members(
        self,
        run_id: str,
        *,
        collaboration_id: str,
        participant_persona_ids: list[str],
        reason: str,
        actor_id: str,
        summary: str | None = None,
        mode: RunMode | None = None,
    ) -> HarnessEvent:
        clean_members = [p for p in dict.fromkeys(participant_persona_ids) if p != "persona_main"]
        if len(clean_members) < 2:
            raise ValueError("Active collaboration must keep >=2 non-Main personas; call end_collaboration instead.")

        next_rev_holder = [2]

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            row = conn.execute(
                "SELECT revision FROM reproduce_collaborations WHERE run_id = ? AND collaboration_id = ?",
                (run_id, collaboration_id),
            ).fetchone()
            if not row:
                raise KeyError(f"Collaboration '{collaboration_id}' not found.")
            next_rev = int(row["revision"]) + 1
            next_rev_holder[0] = next_rev
            conn.execute(
                """
                UPDATE reproduce_collaborations
                SET participant_persona_ids_json = ?, revision = ?, last_event_seq = ?
                WHERE run_id = ? AND collaboration_id = ?
                """,
                (jdumps(clean_members), next_rev, seq, run_id, collaboration_id),
            )

        # Read current revision beforehand so payload carries exact next revision
        meta = self._get_collab_row(run_id, collaboration_id)
        expected_rev = int(meta["revision"]) + 1 if meta else 2

        ev = self.store.append_event(
            run_id,
            event_type="collaboration.members_changed",
            actor_id=actor_id,
            target_ids=clean_members,
            subject=EventSubject(kind="collaboration", id=collaboration_id),
            status="active",
            summary=summary or reason,
            payload={
                "collaboration_id": collaboration_id,
                "participant_persona_ids": clean_members,
                "revision": expected_rev,
                "reason": reason,
            },
            mode=mode,
            state_mutator=_mut,
        )
        self.store.add_chat_message(
            run_id,
            kind="activity",
            role="activity",
            text=ev.summary,
            related_refs=[
                SelectedContext(kind="collaboration", id=collaboration_id),
                *[SelectedContext(kind="agent", id=pid) for pid in clean_members],
            ],
            seq=ev.seq,
        )
        return ev

    def end_collaboration(
        self,
        run_id: str,
        *,
        collaboration_id: str,
        outcome: str,
        actor_id: str,
        summary: str | None = None,
        mode: RunMode | None = None,
    ) -> HarnessEvent | None:
        meta = self._get_collab_row(run_id, collaboration_id)
        if not meta or meta["status"] == "ended":
            return None
        expected_rev = int(meta["revision"]) + 1
        members = jloads(meta["participant_persona_ids_json"], [])
        sref = jloads(meta["ref_json"], {"kind": "collaboration", "id": collaboration_id})

        def _mut(conn: sqlite3.Connection, seq: int) -> None:
            conn.execute(
                """
                UPDATE reproduce_collaborations
                SET status = 'ended', revision = ?, last_event_seq = ?
                WHERE run_id = ? AND collaboration_id = ?
                """,
                (expected_rev, seq, run_id, collaboration_id),
            )

        ev = self.store.append_event(
            run_id,
            event_type="collaboration.ended",
            actor_id=actor_id,
            target_ids=members,
            subject=EventSubject(kind="collaboration", id=collaboration_id),
            status="ended",
            summary=summary or f"Research table concluded ({outcome}).",
            payload={
                "collaboration_id": collaboration_id,
                "outcome": outcome,
                "revision": expected_rev,
            },
            mode=mode,
            state_mutator=_mut,
        )
        self.store.add_chat_message(
            run_id,
            kind="activity",
            role="activity",
            text=ev.summary,
            related_refs=[
                SelectedContext(kind="collaboration", id=collaboration_id),
                SelectedContext(kind=sref["kind"], id=sref["id"]),
                *[SelectedContext(kind="agent", id=pid) for pid in members],
            ],
            seq=ev.seq,
        )
        return ev

    def _get_collab_row(self, run_id: str, collaboration_id: str) -> dict[str, Any] | None:
        from ..storage.db import get_reproduce_conn

        with get_reproduce_conn(self.store.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reproduce_collaborations WHERE run_id = ? AND collaboration_id = ?",
                (run_id, collaboration_id),
            ).fetchone()
        return dict(row) if row else None
