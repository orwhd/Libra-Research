from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import validate

from app.main import app as fullstack_app
from libra_reproduce.config import HarnessResourceLimits, REPO_ROOT
from libra_reproduce.harness.scheduler import ResourceDeniedError, ResourceScheduler
from libra_reproduce.main import app as standalone_reproduce_app
from libra_reproduce.science.cd_index import compute_cd_index, verify_cd_toy_graphs
from libra_reproduce.science.evidence_gate import EvidenceGate
from libra_reproduce.security.experiment_runner import SafeExperimentRunner
from libra_reproduce.security.tool_permissions import (
    ToolPermissionDeniedError,
    ToolPermissionGate,
)
from libra_reproduce.storage.db import init_reproduce_db

CONTRACTS_DIR = REPO_ROOT / "contracts"


def _load_schema(name: str) -> dict:
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_scheduler_dual_pools_and_release_on_failure() -> None:
    """HAR-01 & HAR-02: Agent task pool (4) and parallel experiment pool (2) are strictly independent."""
    init_reproduce_db()
    sched = ResourceScheduler(
        HarnessResourceLimits(
            max_active_agent_tasks=4,
            max_parallel_experiments=2,
            max_subagent_depth=2,
            max_debate_turns=6,
            exploration_budget_fraction=0.20,
            max_task_retries=2,
            experiment_timeout_seconds=30,
        )
    )

    # Acquire 4 active agent task slots
    leases = []
    for i in range(4):
        lease, outcome, _ = await sched.try_acquire_agent_task(
            run_id="run_sched", owner_id=f"ag_{i}", wait_if_full=False
        )
        assert lease is not None
        assert outcome == "granted"
        leases.append(lease)

    # 5th agent task cannot exceed cap
    fifth, out5, _ = await sched.try_acquire_agent_task(
        run_id="run_sched", owner_id="ag_5", wait_if_full=False
    )
    assert fifth is None
    assert out5 == "queued"

    # Parallel experiment pool is independent: even with 4/4 agent tasks active, 2 experiments can run
    exp1, eout1, _ = await sched.try_acquire_experiment(
        run_id="run_sched", owner_id="exp_1", wait_if_full=False
    )
    exp2, eout2, _ = await sched.try_acquire_experiment(
        run_id="run_sched", owner_id="exp_2", wait_if_full=False
    )
    exp3, eout3, _ = await sched.try_acquire_experiment(
        run_id="run_sched", owner_id="exp_3", wait_if_full=False
    )
    assert exp1 is not None and eout1 == "granted"
    assert exp2 is not None and eout2 == "granted"
    assert exp3 is None and eout3 == "queued"

    await sched.release_experiment(exp1.lease_id)
    await sched.release_experiment(exp2.lease_id)
    for ls in leases:
        await sched.release_agent_task(ls.lease_id)

    # Failure inside context manager must release the lease so next task succeeds
    with pytest.raises(RuntimeError):
        async with sched.agent_task_slot(run_id="run_sched", owner_id="boom"):
            assert sched.snapshot_state("run_sched")["active_agent_tasks"] == 1
            raise RuntimeError("simulated agent crash")
    assert sched.snapshot_state("run_sched")["active_agent_tasks"] == 0

    # Exploration budget > 0.20 denied
    with pytest.raises(ResourceDeniedError):
        async with sched.agent_task_slot(
            run_id="run_sched",
            owner_id="pod_over",
            is_exploration=True,
            budget_fraction=0.35,
        ):
            pass


def test_security_permissions_and_ast_sandbox(tmp_path: Path) -> None:
    """HAR-03: Built-in shell/write tools, path traversal, network imports, and os.environ are blocked."""
    gate = ToolPermissionGate(tmp_path / "run_box")
    with pytest.raises(ToolPermissionDeniedError):
        gate.authorize_tool_call("Bash", {"command": "cat ~/.ssh/id_rsa"})
    with pytest.raises(ToolPermissionDeniedError):
        gate.authorize_tool_call("inspect_paper_scope", {"file_path": "../../etc/passwd"})

    # Audit log redacts any secret-like strings
    gate.authorize_tool_call("inspect_paper_scope", {"paper_id": "paper_A", "api_key": "FAKE_SECRET_FOR_REDACTION_TEST"})
    assert gate.audit_log[-1].sanitized_args["api_key"] == "[REDACTED]"

    runner = SafeExperimentRunner(tmp_path / "run_box", timeout_seconds=10)
    bad_env = runner.run_sync("bad_env.py", "import os\nprint(os.environ)\n")
    assert not bad_env.ok and bad_env.exit_code == -2

    bad_net = runner.run_sync("bad_net.py", "import urllib.request\n")
    assert not bad_net.ok and bad_net.exit_code == -2


def test_science_s2_cd_toy_graph_and_evidence_gate(tmp_path: Path) -> None:
    """SCI-02 & SCI-03: Toy citation graph hand-calculated checks + universal Evidence Gate consensus/overclaim rejection."""
    toy = verify_cd_toy_graphs()
    assert toy["passed"] is True

    # Only-focal citing paper => D = +1.0; both-focal-and-ref => D = -1.0; only-ref => D = 0.0
    years = {"r": 1990, "p": 2000, "c": 2003}
    assert compute_cd_index("p", years, [("p", "r"), ("c", "p")]).disruption == pytest.approx(1.0)
    assert compute_cd_index("p", years, [("p", "r"), ("c", "p"), ("c", "r")]).disruption == pytest.approx(-1.0)
    assert compute_cd_index("p", years, [("p", "r"), ("c", "r")]).disruption == pytest.approx(0.0)

    gate = EvidenceGate()
    # Consensus alone NEVER passes Evidence Gate
    consensus_res = gate.review_consensus_only("claim_1", ["persona_analysis", "persona_validation"])
    assert consensus_res.result == "failed"

    # Overclaiming S2 on precomputed S1 metrics fails universal Evidence Gate
    fake_csv = tmp_path / "s.csv"
    fake_csv.write_text("regime,metric_mean\nbaseline,0.5\n", encoding="utf-8")
    overclaim = gate.review_reproduction_bundle(
        claim_id="claim_over",
        claimed_level="S2",
        paper_spec={
            "paper_title": "Test Method Paper",
            "core_claims": ["Method improves score"],
            "methodology_summary": "Controlled comparison",
        },
        data_manifest={
            "source_doi_or_url": "https://example.org/data",
            "license": "CC-BY-4.0",
            "dataset_kind": "controlled_synthetic_benchmark",
            "file_sha256": __import__("hashlib").sha256(fake_csv.read_bytes()).hexdigest(),
            "metric_origin": "precomputed_by_authors",
        },
        primary_data_path=fake_csv,
        executed_trials=[],
        recorded_findings=[],
        table_paths=[fake_csv],
        figure_paths=[],
    )
    assert overclaim.result == "failed"
    assert overclaim.checks["reproduction_level_honest"] is False

    # Overclaiming full S3 empirical status on a synthetic benchmark also fails
    overclaim_s3 = gate.review_reproduction_bundle(
        claim_id="claim_over_s3",
        claimed_level="S3",
        paper_spec={
            "paper_title": "Test Method Paper",
            "core_claims": ["Method improves score"],
            "methodology_summary": "Controlled comparison",
        },
        data_manifest={
            "source_doi_or_url": "https://example.org/sim",
            "license": "MIT",
            "dataset_kind": "controlled_synthetic_benchmark",
            "file_sha256": __import__("hashlib").sha256(fake_csv.read_bytes()).hexdigest(),
            "metric_origin": "computed_from_experiment",
        },
        primary_data_path=fake_csv,
        executed_trials=[],
        recorded_findings=[],
        table_paths=[fake_csv],
        figure_paths=[],
    )
    assert overclaim_s3.result == "failed"
    assert overclaim_s3.checks["reproduction_level_honest"] is False


@pytest.mark.asyncio
async def test_end_to_end_simulation_idempotency_contracts_and_sse_resume(tmp_path: Path) -> None:
    """
    API-01..API-05 & Contract Compliance on BOTH standalone_reproduce_app AND fullstack_app:
    - Homepage multipart PDF upload (>15 MB regression fixed!) -> import-upload/{upload_id} -> Paper Agent PDF ingestion
    - Paper-agnostic reproduction on arbitrary non-Nature paper prompt (e.g. DPO preference optimization)
    - POST /api/runs/{id}/chat idempotency, OpenAPI V2 event & canvas schema validation,
      2->3->5 collaboration morphing, dynamic multi-instance spawning, multi-round PNG/CSV artifacts & DOCX/PDF download.
    """
    init_reproduce_db()
    event_schema = _load_schema("event.schema.json")
    collab_schema = _load_schema("collaboration-payload.schema.json")
    canvas_schema = _load_schema("canvas-projection.schema.json")

    # Build a valid real PDF (>16 MB) via reportlab + padding comment so we directly verify the >15 MB upload fix!
    from reportlab.platypus import Paragraph as RLPara, SimpleDocTemplate as RLDOC
    from reportlab.lib.styles import getSampleStyleSheet as RLStyles

    pdf_file = tmp_path / "dpo_paper_large.pdf"
    RLDOC(str(pdf_file)).build([RLPara("Direct Preference Optimization: Test Paper PDF", RLStyles()["Normal"])])
    base_pdf_bytes = pdf_file.read_bytes()
    padded_pdf_bytes = base_pdf_bytes + b"\n% " + (b"0" * (16 * 1024 * 1024))

    transport = httpx.ASGITransport(app=standalone_reproduce_app)
    fs_transport = httpx.ASGITransport(app=fullstack_app)
    async with httpx.AsyncClient(transport=fs_transport, base_url="http://testserver") as fs_up_client:
        up_resp = await fs_up_client.post(
            "/api/uploads/multipart",
            data={"label": "dpo_paper_large.pdf", "relative_paths": "dpo_paper_large.pdf"},
            files={"files": ("dpo_paper_large.pdf", padded_pdf_bytes, "application/pdf")},
        )
        assert up_resp.status_code == 200, up_resp.text
        homepage_upload_id = up_resp.json()["upload_id"]

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Create run without starting paid work
        r_create = await client.post(
            "/api/runs?mode=simulation",
            json={"title": "DPO Paper Reproduction Test", "paper_id": "paper_dpo"},
        )
        assert r_create.status_code == 201
        run_obj = r_create.json()
        run_id = run_obj["run_id"]
        assert run_obj["mode"] == "simulation"
        assert run_obj["status"] == "created"

        # Import >16 MB homepage PDF upload into Reproduce run without triggering paid work
        imp_resp = await client.post(f"/api/runs/{run_id}/import-upload/{homepage_upload_id}")
        assert imp_resp.status_code == 201, imp_resp.text
        imported_receipts = imp_resp.json()
        assert len(imported_receipts) == 1
        assert imported_receipts[0]["filename"] == "dpo_paper_large.pdf"
        assert imported_receipts[0]["size_bytes"] > 16 * 1024 * 1024
        pdf_art_id = imported_receipts[0]["artifact_id"]

        # Initial snapshot has all 6 personas idle, runtime_agent_id=None, at_seq=0
        snap0 = (await client.get(f"/api/runs/{run_id}/snapshot")).json()
        assert snap0["at_seq"] == 0
        validate(snap0["canvas"], canvas_schema)
        assert len(snap0["canvas"]["personas"]) == 6
        assert all(p["runtime_agent_id"] is None and p["state"] == "idle" for p in snap0["canvas"]["personas"])

        # Attachment validation: unsupported extension -> 415; upload alone does NOT start run
        bad_up = await client.post(
            f"/api/runs/{run_id}/attachments",
            files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
        )
        assert bad_up.status_code == 415
        good_up = await client.post(
            f"/api/runs/{run_id}/attachments",
            files={"file": ("notes.txt", b"DPO beta ablation notes", "text/plain")},
        )
        assert good_up.status_code == 201
        att_id = good_up.json()["artifact_id"]
        assert (await client.get(f"/api/runs/{run_id}")).json()["status"] == "created"

        # 2. Submit ChatCommand + replay identical request_id (idempotency API-01)
        chat_payload = {
            "request_id": "req-idem-001",
            "text": 'Reproduce "Direct Preference Optimization" preference loss, baseline comparison, and beta ablations.',
            "selected_context": None,
            "attachment_ids": [pdf_art_id, att_id],
        }
        c1 = await client.post(f"/api/runs/{run_id}/chat", json=chat_payload)
        c2 = await client.post(f"/api/runs/{run_id}/chat", json=chat_payload)
        assert c1.status_code == 202
        assert c2.status_code == 202
        assert c1.json()["command_id"] == c2.json()["command_id"]

        # Wait for simulation run to complete
        for _ in range(180):
            cur = (await client.get(f"/api/runs/{run_id}")).json()
            if cur["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.15)
        assert cur["status"] == "completed", f"Unexpected run status: {cur}"

        # 3. Stream SSE events and resume from mid-sequence (`after=5`)
        sse_full = await client.get(f"/api/runs/{run_id}/events")
        assert sse_full.status_code == 200
        events_all = [
            json.loads(line[len("data:") :].strip())
            for line in sse_full.text.splitlines()
            if line.startswith("data:")
        ]
        assert len(events_all) >= 12
        seqs = [e["seq"] for e in events_all]
        assert seqs == list(range(1, len(events_all) + 1))

        for ev in events_all:
            validate(ev, event_schema)
            assert ev["mode"] == "simulation"
            if ev["type"].startswith("collaboration."):
                validate(ev, collab_schema)

        sse_resumed = await client.get(f"/api/runs/{run_id}/events?after=5")
        resumed_events = [
            json.loads(line[len("data:") :].strip())
            for line in sse_resumed.text.splitlines()
            if line.startswith("data:")
        ]
        assert [e["seq"] for e in resumed_events] == list(range(6, len(events_all) + 1))

        # 4. Historical snapshot replay at active collaboration seq (`at_seq`)
        started_seq = next(e["seq"] for e in events_all if e["type"] == "collaboration.started")
        expanded_seq = next(e["seq"] for e in events_all if e["type"] == "collaboration.members_changed")

        snap_2p = (await client.get(f"/api/runs/{run_id}/snapshot?at_seq={started_seq}")).json()
        validate(snap_2p["canvas"], canvas_schema)
        assert len(snap_2p["canvas"]["collaborations"]) == 1
        assert len(snap_2p["canvas"]["collaborations"][0]["participant_persona_ids"]) == 2

        snap_3p = (await client.get(f"/api/runs/{run_id}/snapshot?at_seq={expanded_seq}")).json()
        validate(snap_3p["canvas"], canvas_schema)
        assert len(snap_3p["canvas"]["collaborations"][0]["participant_persona_ids"]) == 3

        # Final snapshot clears temporary tables while preserving all 6 role classes AND dynamically spawned specialists!
        snap_end = (await client.get(f"/api/runs/{run_id}/snapshot")).json()
        validate(snap_end["canvas"], canvas_schema)
        assert snap_end["canvas"]["collaborations"] == []
        personas_end = snap_end["canvas"]["personas"]
        assert len(personas_end) >= 10
        assert sum(1 for p in personas_end if p["is_main"]) == 1
        assert sum(1 for p in personas_end if p["role"] == "data") >= 2
        assert sum(1 for p in personas_end if p["role"] == "analysis") >= 3
        assert sum(1 for p in personas_end if p["role"] == "validation") >= 2
        assert all(p["state"] == "idle" for p in personas_end)

        # Multi-round generic artifacts verified by universal Evidence Gate
        ev_items = snap_end["evidence"]
        assert len(ev_items) >= 1
        assert ev_items[-1]["details"]["checks"].get("multiround_experiments_verified") is True
        arts = (await client.get(f"/api/runs/{run_id}/artifacts-detail")).json()
        art_names = {a["filename"]: a["validation_status"] for a in arts}
        assert art_names.get("figure_round1_reproduction.png") == "passed"
        assert art_names.get("figure_round2_robustness.png") == "passed"
        assert art_names.get("round1_results.csv") == "passed"
        assert art_names.get("round2_experiments.csv") == "passed"

        # Verify 2 -> 3 -> 5 continuous morphing collaborations occurred monotonically
        collab_events = [e for e in events_all if e["type"].startswith("collaboration.")]
        assert any(
            e["type"] == "collaboration.members_changed"
            and len(e["payload"].get("participant_persona_ids", [])) == 5
            for e in collab_events
        )

        # Dynamic multi-instance spawn via user Chat command runs real isolated Python experiments!
        dyn_resp = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "req-dyn-spawn-003",
                "text": "请再动态调用2个Analysis agent并行做稳健性复核",
                "selected_context": {"kind": "agent", "id": "persona_main"},
            },
        )
        assert dyn_resp.status_code == 202
        for _ in range(120):
            cur2 = (await client.get(f"/api/runs/{run_id}")).json()
            if cur2["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        assert cur2["status"] == "completed"
        snap_after_dyn = (await client.get(f"/api/runs/{run_id}/snapshot")).json()
        assert sum(1 for p in snap_after_dyn["canvas"]["personas"] if p["role"] == "analysis") >= 5

        # 5. Artifacts & PDF/DOCX reports exist and never expose raw host paths
        rep_status = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert rep_status["status"] == "ready"
        assert len(rep_status["artifact_ids"]) == 2
        for raid in rep_status["artifact_ids"]:
            art_meta = (await client.get(f"/api/artifacts/{raid}")).json()
            assert "/Users/" not in json.dumps(art_meta)
            dl = await client.get(art_meta["download_url"])
            assert dl.status_code == 200
            assert len(dl.content) > 5000

        # 6. Read-only Milestone Timeline, Brief Table Dialogues & Vertical Chat Spine (`GET /api/runs/{id}/timeline`)
        tl_resp = await client.get(f"/api/runs/{run_id}/timeline")
        assert tl_resp.status_code == 200
        tl = tl_resp.json()
        snaps = tl["snapshots"]
        assert any(s["kind"] == "run_start" for s in snaps)
        huddle_starts = [s for s in snaps if s["kind"] == "huddle_start"]
        huddle_ends = [s for s in snaps if s["kind"] == "huddle_end"]
        assert len(huddle_starts) >= 1 and huddle_starts[0]["reason"]
        assert len(huddle_ends) >= 1 and huddle_ends[0]["report"]
        assert any(s["linked_step_ids"] for s in huddle_starts)

        dialogues = tl["table_dialogues"]
        assert len(dialogues) >= 1
        first_diag = next(iter(dialogues.values()))
        assert len(first_diag["turns"]) >= 4
        assert first_diag["start_reason"]
        assert first_diag["end_report"]

        briefings = tl.get("briefings") or {}
        assert len(briefings) >= 1
        brief_snaps = [s for s in snaps if s["kind"] == "briefing"]
        assert len(brief_snaps) >= 1
        brief_snap_at = (await client.get(f"/api/runs/{run_id}/snapshot?at_seq={brief_snaps[0]['seq']}")).json()
        assert any(
            not p["is_main"] and p["state"] == "reporting"
            for p in brief_snap_at["canvas"]["personas"]
        )

        chat_steps = tl["chat_steps"]
        assert chat_steps[0]["actor_label"] == "Main agent"
        assert chat_steps[-1]["actor_label"] == "Main agent"
        assert any(len(st["artifact_ids"]) >= 1 for st in chat_steps)

        # 7. Silent read-only message filtering & inquiry
        msg_page = (
            await client.get(
                f"/api/runs/{run_id}/messages?ref_kind=agent&ref_id=persona_validation"
            )
        ).json()
        assert len(msg_page["messages"]) >= 1
        seq_before_read = (await client.get(f"/api/runs/{run_id}/snapshot")).json()["at_seq"]

        inq = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "req-readonly-002",
                "text": "解释一下刚才验证实验的结论和尚未解决的限制",
                "selected_context": {"kind": "agent", "id": "persona_validation"},
            },
        )
        assert inq.status_code == 200
        assert inq.json()["status"] == "answered"
        seq_after_read = (await client.get(f"/api/runs/{run_id}/snapshot")).json()["at_seq"]
        assert seq_after_read == seq_before_read

    async with httpx.AsyncClient(transport=fs_transport, base_url="http://testserver") as fs_client:
        fs_run = await fs_client.get(f"/api/runs/{run_id}")
        assert fs_run.status_code == 200
        assert fs_run.json()["run_id"] == run_id


@pytest.mark.asyncio
async def test_orphan_recovery_cancel_and_paper_context_brief(tmp_path: Path) -> None:
    """Verify EventStore orphan running recovery, POST /api/runs/{id}/cancel, and paper context brief."""
    from libra_reproduce.api import RUNTIME
    from libra_reproduce.science.paper_ingestor import build_paper_context_brief

    init_reproduce_db()
    txt_file = tmp_path / "data" / "attachments" / "demo_extracted.txt"
    txt_file.parent.mkdir(parents=True, exist_ok=True)
    txt_file.write_text(
        "Title: Graph Scaling\n\nAbstract\nWe evaluate graph scaling.\n\nMethods\nWe compare proposed vs baseline using Spearman rho.",
        encoding="utf-8",
    )
    brief = build_paper_context_brief(
        {
            "user_prompt": "Reproduce Graph Scaling",
            "scholarly_metadata": {"title": "Graph Scaling", "year": "2024"},
            "attachments": [{"rel_path": "data/demo.pdf", "kind": "pdf", "size_bytes": 4096}],
            "extracted_text_rel_paths": ["data/attachments/demo_extracted.txt"],
        },
        tmp_path,
    )
    assert "Graph Scaling" in brief
    assert "Spearman rho" in brief

    # Create stuck `running` and `paused` runs and verify recover_orphaned_running_runs() unlocks both cleanly
    stuck = RUNTIME.event_store.create_run(title="Stuck Orphan Run", mode="simulation")
    RUNTIME.event_store.update_run_status(stuck.run_id, status="running", report_status="generating")
    stuck_paused = RUNTIME.event_store.create_run(title="Stuck Paused Run", mode="simulation")
    RUNTIME.event_store.update_run_status(stuck_paused.run_id, status="paused")
    recovered = RUNTIME.event_store.recover_orphaned_running_runs()
    assert stuck.run_id in recovered and stuck_paused.run_id in recovered
    unlocked = RUNTIME.event_store.get_run(stuck.run_id)
    unlocked_paused = RUNTIME.event_store.get_run(stuck_paused.run_id)
    assert unlocked is not None and unlocked.status == "failed"
    assert unlocked_paused is not None and unlocked_paused.status == "failed"
    snap_unlocked = RUNTIME.event_store.get_snapshot(stuck.run_id)
    assert snap_unlocked is not None
    assert all(p.state == "idle" for p in snap_unlocked.canvas.personas)

    # Verify POST /api/runs/{id}/cancel unlocks an active run via API
    transport = httpx.ASGITransport(app=standalone_reproduce_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r2 = (await client.post("/api/runs?mode=simulation", json={"title": "Cancel Test"})).json()
        RUNTIME.event_store.update_run_status(r2["run_id"], status="running")
        c_resp = await client.post(f"/api/runs/{r2['run_id']}/cancel")
        assert c_resp.status_code == 200
        assert c_resp.json()["status"] == "failed"

        why_resp = await client.post(
            f"/api/runs/{r2['run_id']}/chat",
            json={
                "request_id": "req-why-failed-1",
                "text": "为什么失败了",
                "selected_context": {"kind": "agent", "id": "persona_main"},
            },
        )
        assert why_resp.status_code == 200
        assert why_resp.json()["status"] == "answered"
        assert "cancelled" in why_resp.json()["message"].lower() or "stopped" in why_resp.json()["message"].lower()
        assert (await client.get(f"/api/runs/{r2['run_id']}")).json()["status"] == "failed"
        msgs_after = (await client.get(f"/api/runs/{r2['run_id']}/messages?limit=20")).json()["messages"]
        user_main_msgs = [m for m in msgs_after if m["role"] in ("user", "main")]
        assert len(user_main_msgs) >= 2
        assert user_main_msgs[-2]["role"] == "user" and user_main_msgs[-2]["text"] == "为什么失败了"
        assert user_main_msgs[-1]["role"] == "main"


@pytest.mark.asyncio
async def test_human_intervention_dissent_email_and_resume() -> None:
    """Verify debate dissent -> email escalation -> paused state -> Chat human reply resumes coordinator."""
    from libra_reproduce.api import RUNTIME
    from libra_reproduce.schemas import EventSubject, SourceRef

    init_reproduce_db()
    transport = httpx.ASGITransport(app=standalone_reproduce_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        run_obj = (await client.post("/api/runs?mode=simulation", json={"title": "Intervention Flow"})).json()
        rid = run_obj["run_id"]

        debate_id, _ = RUNTIME.coordinator.collab.open_debate(
            rid,
            claim_id="claim_est",
            claim="Estimator discrepancy requires human choice",
            participants=["persona_analysis", "persona_validation"],
            actor_id="persona_validation",
            mode="simulation",
        )
        collab_id, _ = RUNTIME.coordinator.collab.start_collaboration(
            rid,
            kind="debate",
            participant_persona_ids=["persona_analysis", "persona_validation"],
            topic="Estimator audit",
            source_ref=SourceRef(kind="debate", id=debate_id),
            actor_id="persona_validation",
            mode="simulation",
        )
        RUNTIME.event_store.update_run_status(rid, status="paused")
        RUNTIME.event_store.append_event(
            rid,
            event_type="debate.human_intervention_requested",
            actor_id="persona_validation",
            target_ids=["persona_main"],
            subject=EventSubject(kind="debate", id=debate_id),
            status="waiting",
            summary="Debating agents sent intervention email to Main: estimator conflict.",
            payload={
                "intervention_id": "int_test_01",
                "debate_id": debate_id,
                "collaboration_id": collab_id,
                "from_persona_ids": ["persona_validation", "persona_analysis"],
                "to_persona_id": "persona_main",
                "dissent_summary": "## Critique of Round 1\nPaired SE vs **pooled SE** estimator discrepancy detected in `data_manifest.json`.",
                "question": "Which estimator should Round 2 lock onto?",
                "options": [{"label": "Use paired SE", "prompt": "Lock Round 2 to paired SE."}],
            },
            mode="simulation",
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        RUNTIME.coordinator._pending_interventions[rid] = {
            "intervention_id": "int_test_01",
            "debate_id": debate_id,
            "collaboration_id": collab_id,
            "from_persona_ids": ["persona_validation", "persona_analysis"],
            "future": fut,
        }

        tl_before = (await client.get(f"/api/runs/{rid}/timeline")).json()
        assert tl_before["active_intervention"] is not None
        assert tl_before["active_intervention"]["intervention_id"] == "int_test_01"
        int_snaps = [s for s in tl_before["snapshots"] if s["kind"] == "intervention"]
        assert len(int_snaps) >= 1
        assert "##" not in int_snaps[0]["reason"] and "**" not in int_snaps[0]["reason"] and "`" not in int_snaps[0]["reason"]
        assert "…" not in int_snaps[0]["reason"] and "..." not in int_snaps[0]["reason"]
        assert all("duration_sec" in s and "stage" in s for s in tl_before["snapshots"])

        reply = await client.post(
            f"/api/runs/{rid}/chat",
            json={
                "request_id": "req-human-guide-1",
                "text": "Lock Round 2 to paired SE.",
                "selected_context": {"kind": "agent", "id": "persona_main"},
            },
        )
        assert reply.status_code == 202
        assert fut.done() and fut.result() == "Lock Round 2 to paired SE."

        tl_after = (await client.get(f"/api/runs/{rid}/timeline")).json()
        assert tl_after["active_intervention"] is None
        turns = tl_after["table_dialogues"][collab_id]["turns"]
        main_guidance_turns = [t for t in turns if t["speaker_id"] == "persona_main" and t["stance"] == "human_guidance"]
        assert len(main_guidance_turns) == 1
        escalate_turns = [t for t in turns if t["stance"] == "escalate"]
        assert len(escalate_turns) == 1 and escalate_turns[0]["summary"].startswith("Sent intervention email to Main:")
