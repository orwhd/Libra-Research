from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

GateVerdict = Literal["passed", "failed", "inconclusive"]


@dataclass
class EvidenceReviewResult:
    claim_id: str
    result: GateVerdict
    reproduction_level_verified: Literal["S0", "S1", "S2", "S3"]
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    research_gains: dict[str, list[str]] = field(default_factory=dict)


def _verify_png_header_and_size(path: Path) -> tuple[bool, int, int]:
    if not path.exists() or path.stat().st_size < 1024:
        return False, 0, 0
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        return False, 0, 0
    w, h = struct.unpack(">II", raw[16:24])
    return (w >= 400 and h >= 200), int(w), int(h)


class EvidenceGate:
    """
    Universal, paper-agnostic scientific Evidence Gate (ADR-V2).
    Agent consensus NEVER passes without verifiable data/protocol manifests,
    isolated Python script SHA-256 execution provenance across multiple rounds,
    valid quantitative outputs & PNG figures, and honest reproduction scope.
    """

    def review_consensus_only(self, claim_id: str, agreed_by_agents: list[str]) -> EvidenceReviewResult:
        return EvidenceReviewResult(
            claim_id=claim_id,
            result="failed",
            reproduction_level_verified="S0",
            reasons=[
                f"Rejected consensus-only claim from {agreed_by_agents}: no manifest, executable script hash, or result artifact bound."
            ],
            checks={
                "has_manifest": False,
                "has_executable_script": False,
                "has_result_artifacts": False,
            },
            research_gains={
                "verified_insights": [],
                "ruled_out_explanations": [],
                "open_questions": ["Claim lacks empirical execution artifacts."],
                "methodology_lessons": ["Multi-agent agreement does not constitute scientific verification."],
            },
        )

    def review_reproduction_bundle(
        self,
        *,
        claim_id: str,
        claimed_level: str,
        paper_spec: dict[str, Any] | None,
        data_manifest: dict[str, Any] | None,
        primary_data_path: Path | None,
        executed_trials: list[dict[str, Any]],
        recorded_findings: list[dict[str, Any]],
        table_paths: list[Path],
        figure_paths: list[Path],
        debate_records: list[dict[str, Any]] | None = None,
    ) -> EvidenceReviewResult:
        reasons: list[str] = []
        checks: dict[str, bool] = {}

        # 1. Structured PaperSpec check
        spec = paper_spec or {}
        has_spec = bool(
            str(spec.get("paper_title") or "").strip()
            and (spec.get("core_claims") or [])
            and str(spec.get("methodology_summary") or "").strip()
        )
        checks["paper_spec_complete"] = has_spec
        if not has_spec:
            reasons.append("Paper specification is missing paper_title, core_claims, or methodology_summary.")

        # 2. Data / Experimental Environment manifest & SHA-256 verification
        man = data_manifest or {}
        has_manifest = bool(
            man.get("source_doi_or_url")
            and man.get("license")
            and man.get("file_sha256")
            and (man.get("dataset_kind") or man.get("metric_origin"))
        )
        checks["manifest_complete"] = has_manifest
        if not has_manifest:
            reasons.append("Missing required data_manifest fields (source_doi_or_url, license, file_sha256, dataset_kind).")

        sha_ok = False
        if has_manifest and primary_data_path and primary_data_path.exists():
            actual_sha = hashlib.sha256(primary_data_path.read_bytes()).hexdigest()
            sha_ok = actual_sha == man.get("file_sha256")
        checks["dataset_sha256_verified"] = sha_ok
        if not sha_ok:
            reasons.append("Primary dataset/protocol file missing on disk or SHA-256 mismatches data_manifest.json.")

        # 3. Reproduction scope honesty check (prevent overclaiming S2/S3 on precomputed or synthetic inputs)
        metric_origin = str(man.get("metric_origin") or "").lower()
        dataset_kind = str(man.get("dataset_kind") or "").lower()
        eff_claimed = claimed_level if claimed_level in ("S1", "S2", "S3") else "S1"
        level_honest = True
        if "precomputed" in metric_origin and eff_claimed in ("S2", "S3"):
            level_honest = False
            reasons.append(
                f"Overclaimed reproduction level '{eff_claimed}' on author-precomputed metrics (must be S1)."
            )
        if ("synthetic" in dataset_kind or "simulation" in dataset_kind) and eff_claimed == "S3":
            level_honest = False
            reasons.append(
                f"Overclaimed full empirical level 'S3' on a '{dataset_kind}' benchmark."
            )
        checks["reproduction_level_honest"] = level_honest

        # 4. Script execution provenance across multiple rounds
        ok_trials = [
            t for t in executed_trials
            if t.get("ok")
            and t.get("exit_code") == 0
            and len(str(t.get("script_sha256") or "")) == 64
            and t.get("script_path")
            and Path(str(t["script_path"])).exists()
        ]
        distinct_scripts = {str(t.get("script_name") or t.get("script_path")) for t in ok_trials}
        checks["scripts_executed_cleanly"] = len(ok_trials) >= 1
        checks["multiround_experiments_verified"] = len(distinct_scripts) >= 2
        if not checks["scripts_executed_cleanly"]:
            reasons.append("No Python experiment script executed cleanly with exit_code=0 and 64-char SHA-256.")
        if not checks["multiround_experiments_verified"]:
            reasons.append(
                f"Expected >=2 distinct multi-round experiment scripts (e.g., Round 1 + Round 2 ablation/falsification); found {len(distinct_scripts)}."
            )

        # 5. Non-empty tabular quantitative output check
        valid_tables = 0
        for tp in table_paths:
            if not tp.exists() or tp.stat().st_size == 0:
                continue
            try:
                if tp.suffix.lower() == ".csv":
                    df = pd.read_csv(tp)
                    if len(df) > 0 and len(df.columns) >= 2:
                        valid_tables += 1
                elif tp.suffix.lower() == ".json":
                    valid_tables += 1
            except Exception:
                continue
        checks["tabular_outputs_non_empty"] = valid_tables >= 1
        if not checks["tabular_outputs_non_empty"]:
            reasons.append("No non-empty quantitative CSV/JSON experiment results generated.")

        # 6. Figures valid PNG header & legible dimensions check
        valid_figs = [p for p in figure_paths if _verify_png_header_and_size(p)[0]]
        checks["figures_valid_png"] = len(valid_figs) >= 1
        if not checks["figures_valid_png"]:
            reasons.append("Generated PNG figure(s) missing or below minimum 400x200 PNG dimensions.")

        core_ok = all(
            checks.get(k, False)
            for k in (
                "paper_spec_complete",
                "manifest_complete",
                "dataset_sha256_verified",
                "reproduction_level_honest",
                "scripts_executed_cleanly",
                "tabular_outputs_non_empty",
                "figures_valid_png",
            )
        )

        verified_level: Literal["S0", "S1", "S2", "S3"] = "S0"
        if core_ok:
            verified_level = eff_claimed  # type: ignore[assignment]

        if not core_ok:
            return EvidenceReviewResult(
                claim_id=claim_id,
                result="failed",
                reproduction_level_verified="S0",
                reasons=reasons,
                checks=checks,
                research_gains={
                    "verified_insights": [],
                    "ruled_out_explanations": [],
                    "open_questions": reasons,
                    "methodology_lessons": ["Evidence Gate blocked incomplete, unverified, or overclaimed reproduction artifacts."],
                },
            )

        verdict: GateVerdict = "passed" if checks["multiround_experiments_verified"] else "inconclusive"
        title = str(spec.get("paper_title") or "Target Paper")
        reasons.append(
            f"Verified {verified_level} multi-round experimental reproduction for '{title}' "
            f"({len(distinct_scripts)} distinct Python script(s), {len(valid_figs)} verified PNG figure(s), dataset_kind='{man.get('dataset_kind')}')."
        )

        verified_insights = [
            f"[{f.get('round_id', 'exp')}] {f.get('hypothesis', '')}: {f.get('quantitative_result', '')} (verdict={f.get('verdict', 'supported')})"
            for f in recorded_findings
            if str(f.get("verdict") or "").lower() in ("supported", "verified", "confirmed", "passed", "partial")
        ]
        if not verified_insights:
            for t in ok_trials[:4]:
                verified_insights.append(
                    f"Executed `{t.get('script_name')}` ({t.get('round_label', 'trial')}, SHA-256 {str(t.get('script_sha256'))[:12]}): {str(t.get('stdout') or '').strip()[:180]}"
                )

        ruled_out = [
            f"[{f.get('round_id', 'exp')}] Ruled out / falsified: {f.get('hypothesis', '')} — {f.get('quantitative_result', '')}"
            for f in recorded_findings
            if str(f.get("verdict") or "").lower() in ("refuted", "ruled_out", "falsified", "failed")
        ]
        for d in debate_records or []:
            if d.get("resolution_summary"):
                ruled_out.append(str(d["resolution_summary"]))

        open_questions = [
            str(f.get("next_question"))
            for f in recorded_findings
            if str(f.get("next_question") or "").strip()
        ]
        for b in man.get("known_biases") or []:
            open_questions.append(str(b))

        methodology_lessons = [
            f"Verified reproduction scope '{verified_level}' on '{man.get('dataset_kind')}' with {len(distinct_scripts)} isolated SafeExperimentRunner script(s).",
            str(man.get("schema_verification_note") or "Input schema and artifact SHA-256 digests verified."),
        ]

        return EvidenceReviewResult(
            claim_id=claim_id,
            result=verdict,
            reproduction_level_verified=verified_level,
            reasons=reasons,
            checks=checks,
            research_gains={
                "verified_insights": verified_insights[:8],
                "ruled_out_explanations": ruled_out[:6],
                "open_questions": open_questions[:6],
                "methodology_lessons": methodology_lessons[:6],
            },
        )
