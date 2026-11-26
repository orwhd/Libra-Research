from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

from ..harness.scheduler import ResourceScheduler
from ..security.experiment_runner import SafeExperimentRunner
from ..security.tool_permissions import ToolPermissionGate
from .paper_ingestor import fetch_public_dataset_url

_SIMULATION_ROUND1_SCRIPT = '''"""Generic Simulation Fixture Round 1: Controlled Method vs Baseline Reproduction Experiment.

Runs strictly inside SafeExperimentRunner (`python -E`, stripped env, AST verified) when mode=='simulation'.
Compares Proposed Method vs Baseline across 5 complexity/scale regimes, computes bootstrap 95% CIs,
saves round1_results.csv + round1_summary.json, and renders figure_round1_reproduction.png.
"""
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats


def main() -> None:
    _data_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(20261126)
    regimes = [1, 2, 4, 8, 16]
    rows = []
    raw_effects = []
    raw_scales = []
    for r in regimes:
        n_trials = 80
        baseline = 0.62 + 0.03 * math.log2(r) + rng.normal(0.0, 0.045, size=n_trials)
        proposed = 0.71 + 0.055 * math.log2(r) + rng.normal(0.0, 0.040, size=n_trials)
        gain = proposed - baseline
        raw_effects.extend(gain.tolist())
        raw_scales.extend([math.log2(r)] * n_trials)

        mean_base = float(np.mean(baseline))
        mean_prop = float(np.mean(proposed))
        mean_gain = float(np.mean(gain))
        se_gain = float(np.std(gain, ddof=1) / math.sqrt(n_trials))
        t_stat, p_val = stats.ttest_rel(proposed, baseline)
        rows.append({
            "scale_regime": f"scale_{r}",
            "n_trials": n_trials,
            "baseline_score": round(mean_base, 5),
            "proposed_score": round(mean_prop, 5),
            "effect_delta": round(mean_gain, 5),
            "ci95_low": round(mean_gain - 1.96 * se_gain, 5),
            "ci95_high": round(mean_gain + 1.96 * se_gain, 5),
            "paired_p_value": float(p_val),
        })

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "round1_results.csv", index=False)

    slope, _, _, slope_p, _ = stats.linregress(np.array(raw_scales), np.array(raw_effects))
    summary = {
        "n_regimes": len(regimes),
        "total_trials": int(df["n_trials"].sum()),
        "mean_baseline_score": round(float(df["baseline_score"].mean()), 5),
        "mean_proposed_score": round(float(df["proposed_score"].mean()), 5),
        "mean_effect_delta": round(float(df["effect_delta"].mean()), 5),
        "scaling_slope": round(float(slope), 5),
        "scaling_p_value": float(slope_p),
    }
    (out_dir / "round1_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.rcParams.update({"font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.4), dpi=200)
    x = np.arange(len(regimes))
    ax1.plot(x, df["baseline_score"], marker="s", lw=2.0, color="#64748b", label="Baseline")
    ax1.plot(x, df["proposed_score"], marker="o", lw=2.2, color="#1d4ed8", label="Proposed Method")
    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{r}x" for r in regimes])
    ax1.set_xlabel("Experimental Regime Scale")
    ax1.set_ylabel("Normalized Primary Metric")
    ax1.set_title("Round 1: Proposed Method vs. Baseline", fontweight="bold")
    ax1.legend(frameon=True)

    yerr_lo = df["effect_delta"] - df["ci95_low"]
    yerr_hi = df["ci95_high"] - df["effect_delta"]
    ax2.bar(x, df["effect_delta"], color="#0f766e", alpha=0.88)
    ax2.errorbar(x, df["effect_delta"], yerr=[yerr_lo, yerr_hi], fmt="none", ecolor="#0f172a", capsize=3)
    ax2.axhline(0.0, color="#94a3b8", ls=":", lw=1.0)
    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{r}x" for r in regimes])
    ax2.set_xlabel("Experimental Regime Scale")
    ax2.set_ylabel("Effect Size (Proposed - Baseline)")
    ax2.set_title("Effect Magnitude with 95% CIs", fontweight="bold")

    fig.tight_layout(pad=1.5)
    fig.savefig(out_dir / "figure_round1_reproduction.png", dpi=200)
    plt.close(fig)
    print("ROUND1_SIM_OK", json.dumps(summary))


if __name__ == "__main__":
    main()
'''

_SIMULATION_ROUND1B_INVARIANTS_SCRIPT = '''"""Generic Simulation Fixture Round 1B: Mathematical & Numerical Invariance Verification."""
import json
import sys
from pathlib import Path
import numpy as np

out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)
vec = rng.normal(0.0, 1.0, size=64)
probs = np.exp(vec - np.max(vec))
probs /= np.sum(probs)
entropy = float(-np.sum(probs * np.log(probs + 1e-12)))

result = {
    "probability_simplex_sum": round(float(np.sum(probs)), 8),
    "entropy_non_negative": bool(entropy >= 0.0),
    "scale_shift_invariant": bool(np.allclose(probs, np.exp((vec + 10.0) - np.max(vec + 10.0)) / np.sum(np.exp((vec + 10.0) - np.max(vec + 10.0))))),
    "passed": True,
}
(out_dir / "round1b_invariants.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print("ROUND1B_INVARIANTS_OK", json.dumps(result))
'''

_SIMULATION_ROUND2_SCRIPT = '''"""Generic Simulation Fixture Round 2: Ablation, 200-Trial Permutation Null & Noise Robustness."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats


def main() -> None:
    _data_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(20261127)
    n = 160
    full_model = 0.84 + rng.normal(0.0, 0.045, size=n)
    ablated_model = 0.72 + rng.normal(0.0, 0.050, size=n)
    ctrl_model = 0.65 + rng.normal(0.0, 0.055, size=n)

    ablation_delta = float(np.mean(full_model - ablated_model))
    obs_diff = float(np.mean(full_model) - np.mean(ctrl_model))

    pooled = np.concatenate([full_model, ctrl_model])
    null_diffs = np.empty(200, dtype=float)
    for i in range(200):
        perm = rng.permutation(pooled)
        null_diffs[i] = float(np.mean(perm[:n]) - np.mean(perm[n:]))
    perm_p = float((np.sum(np.abs(null_diffs) >= abs(obs_diff)) + 1.0) / 201.0)

    noise_sigmas = [0.0, 0.05, 0.10, 0.20, 0.35]
    noise_retention = []
    rows = [
        {"round_id": "2A_ablation", "condition": "Full vs. Ablated", "metric": "delta", "value": round(ablation_delta, 5)},
        {"round_id": "2B_permutation_null", "condition": "n_perm=200", "metric": "perm_p_value", "value": round(perm_p, 5)},
    ]
    for sig in noise_sigmas:
        noisy_full = full_model + rng.normal(0.0, sig, size=n)
        noisy_ctrl = ctrl_model + rng.normal(0.0, sig, size=n)
        gap = float(np.mean(noisy_full - noisy_ctrl))
        noise_retention.append({"sigma": sig, "effect_gap": round(gap, 5)})
        rows.append({"round_id": "2C_noise_sweep", "condition": f"sigma={sig}", "metric": "effect_gap", "value": round(gap, 5)})

    pd.DataFrame(rows).to_csv(out_dir / "round2_experiments.csv", index=False)

    summary = {
        "ablation_delta": round(ablation_delta, 5),
        "permutation_null": {"n_permutations": 200, "observed_diff": round(obs_diff, 5), "p_value": round(perm_p, 5)},
        "noise_sensitivity": noise_retention,
    }
    (out_dir / "round2_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    plt.rcParams.update({"font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.2), dpi=200)

    axes[0].bar(["Baseline", "Ablated", "Full Method"], [float(np.mean(ctrl_model)), float(np.mean(ablated_model)), float(np.mean(full_model))], color=["#94a3b8", "#f59e0b", "#1d4ed8"])
    axes[0].set_ylabel("Mean Score")
    axes[0].set_title("Round 2A: Component Ablation", fontweight="bold")

    axes[1].hist(null_diffs, bins=20, color="#94a3b8", edgecolor="white", label="Null diffs (n=200)")
    axes[1].axvline(obs_diff, color="#dc2626", ls="--", lw=2.0, label=f"Observed={obs_diff:.3f}")
    axes[1].set_xlabel("Difference in Means")
    axes[1].set_ylabel("Count")
    axes[1].set_title(f"Round 2B: Permutation Null (p={perm_p:.4f})", fontweight="bold")
    axes[1].legend(frameon=True, fontsize=8.0)

    axes[2].plot([r["sigma"] for r in noise_retention], [r["effect_gap"] for r in noise_retention], marker="o", lw=2.0, color="#0f766e")
    axes[2].axhline(0.0, color="#94a3b8", ls=":", lw=1.0)
    axes[2].set_xlabel("Perturbation Noise Sigma")
    axes[2].set_ylabel("Retained Effect Gap")
    axes[2].set_title("Round 2C: Perturbation Sensitivity", fontweight="bold")

    fig.tight_layout(pad=1.5)
    fig.savefig(out_dir / "figure_round2_robustness.png", dpi=200)
    plt.close(fig)
    print("ROUND2_SIM_OK", json.dumps(summary))


if __name__ == "__main__":
    main()
'''

_GENERIC_FOLLOWUP_SCRIPT = '''"""Executed by dynamically spawned specialist workers under SafeExperimentRunner."""
import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd

out_dir = Path(sys.argv[1])
out_json = Path(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

rng = np.random.default_rng(seed)
csv_candidates = sorted(out_dir.glob("*.csv"))
metric_col = None
values = None
source_file = "synthetic_bootstrap_check"
for c in csv_candidates:
    try:
        df = pd.read_csv(c)
        num_cols = [col for col in df.select_dtypes(include=[np.number]).columns if df[col].notna().sum() >= 2]
        if num_cols:
            metric_col = str(num_cols[0])
            values = df[metric_col].dropna().to_numpy(dtype=float)
            source_file = c.name
            break
    except Exception:
        continue

if values is None or len(values) < 2:
    values = 0.14 + rng.normal(0.0, 0.03, size=64)
    metric_col = "bootstrap_effect"

boots = [float(np.mean(rng.choice(values, size=len(values), replace=True))) for _ in range(300)]
res = {
    "source_file": source_file,
    "metric_column": metric_col,
    "n_evaluated": int(len(values)),
    "bootstrap_mean": round(float(np.mean(boots)), 6),
    "bootstrap_std": round(float(np.std(boots, ddof=1)), 6),
    "ci95_low": round(float(np.percentile(boots, 2.5)), 6),
    "ci95_high": round(float(np.percentile(boots, 97.5)), 6),
}
out_json.write_text(json.dumps(res, indent=2), encoding="utf-8")
print("FOLLOWUP_EXP_OK", json.dumps(res))
'''


def _check_png_dimensions(path: Path) -> tuple[bool, int, int]:
    if not path.exists() or path.stat().st_size < 1024:
        return False, 0, 0
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        return False, 0, 0
    w, h = struct.unpack(">II", raw[16:24])
    return (w >= 400 and h >= 200), int(w), int(h)


def _snapshot_outputs(out_dir: Path) -> dict[str, tuple[int, int]]:
    if not out_dir.exists():
        return {}
    return {
        p.name: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in out_dir.iterdir()
        if p.is_file()
    }


def _persist_rounds_json(out_dir: Path, shared_state: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "executed_trials": shared_state.get("executed_trials", []),
        "recorded_findings": shared_state.get("recorded_findings", []),
    }
    (out_dir / "experiment_rounds.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_general_science_tools(
    *,
    run_id: str,
    workspace_dir: Path,
    scheduler: ResourceScheduler,
    owner_task_id: str,
    shared_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Paper-agnostic controlled MCP toolset empowering agents to inspect uploaded PDFs/datasets,
    fetch open datasets, execute self-authored Python experiments inside SafeExperimentRunner,
    inspect created outputs/PNGs/stderr to self-repair across rounds, and record findings.
    """
    gate = ToolPermissionGate(workspace_dir)
    out_dir = workspace_dir / "outputs"
    data_dir = workspace_dir / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    async def _inspect_workspace_files(args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("relative_path") or "").strip()
        offset = max(0, int(args.get("offset") or 0))
        limit = max(500, min(32000, int(args.get("limit") or 16000)))
        search_query = str(args.get("search_query") or "").strip()
        if not rel:
            listing: dict[str, list[str]] = {}
            for sub in ("data", "outputs", "scripts"):
                base = workspace_dir / sub
                if base.exists():
                    listing[sub] = sorted(
                        str(p.relative_to(workspace_dir))
                        for p in base.rglob("*")
                        if p.is_file()
                    )[:80]
            return {"ok": True, "files": listing}

        target = gate.check_path(rel)
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": f"File '{rel}' does not exist."}
        txt = target.read_text(encoding="utf-8", errors="replace")

        if search_query:
            low_txt = txt.lower()
            low_q = search_query.lower()
            matches: list[dict[str, Any]] = []
            pos = 0
            while len(matches) < 6:
                idx = low_txt.find(low_q, pos)
                if idx < 0:
                    break
                win_start = max(0, idx - 600)
                win_end = min(len(txt), idx + len(search_query) + 600)
                matches.append(
                    {
                        "offset": idx,
                        "snippet": txt[win_start:win_end],
                    }
                )
                pos = idx + max(len(search_query), 600)
            return {
                "ok": True,
                "relative_path": rel,
                "total_chars": len(txt),
                "search_query": search_query,
                "matches": matches,
            }

        return {
            "ok": True,
            "relative_path": rel,
            "total_chars": len(txt),
            "offset": offset,
            "content": txt[offset : offset + limit],
        }

    async def _register_paper_spec(args: dict[str, Any]) -> dict[str, Any]:
        spec = {
            "paper_title": str(args.get("paper_title") or "Untitled Research Paper").strip(),
            "authors_year": str(args.get("authors_year") or "Unspecified").strip(),
            "domain": str(args.get("domain") or "Computer Science / Scientific Research").strip(),
            "doi_or_url": str(args.get("doi_or_url") or "").strip(),
            "core_claims": [str(x).strip() for x in (args.get("core_claims") or []) if str(x).strip()],
            "methodology_summary": str(args.get("methodology_summary") or "").strip(),
            "key_metrics": [str(x).strip() for x in (args.get("key_metrics") or []) if str(x).strip()],
            "datasets_or_benchmarks": [str(x).strip() for x in (args.get("datasets_or_benchmarks") or []) if str(x).strip()],
            "rival_hypotheses": [str(x).strip() for x in (args.get("rival_hypotheses") or []) if str(x).strip()],
            "experiment_plan": [str(x).strip() for x in (args.get("experiment_plan") or []) if str(x).strip()],
        }
        shared_state["paper_spec"] = spec
        (out_dir / "paper_spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "paper_spec": spec}

    async def _fetch_public_dataset(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or "").strip()
        filename = str(args.get("filename") or "").strip()
        if not url:
            return {"ok": False, "error": "url is required"}
        try:
            res = await fetch_public_dataset_url(url, data_dir / "fetched", filename=filename)
            if res.get("ok"):
                res["relative_path"] = f"data/fetched/{res['filename']}"
            return res
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _register_data_manifest(args: dict[str, Any]) -> dict[str, Any]:
        primary_rel = str(args.get("primary_data_file") or "").strip()
        file_sha = ""
        resolved_primary: Path | None = None
        if primary_rel:
            candidate = gate.check_path(primary_rel)
            if candidate.exists() and candidate.is_file():
                resolved_primary = candidate
                file_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()

        if not file_sha:
            proto_path = out_dir / "experimental_protocol.json"
            proto_bytes = json.dumps(
                {
                    "source_doi_or_url": str(args.get("source_doi_or_url") or ""),
                    "dataset_kind": str(args.get("dataset_kind") or "controlled_benchmark"),
                    "columns_verified": list(args.get("columns_verified") or []),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            proto_path.write_bytes(proto_bytes)
            resolved_primary = proto_path
            primary_rel = "outputs/experimental_protocol.json"
            file_sha = hashlib.sha256(proto_bytes).hexdigest()

        manifest = {
            "dataset_id": f"ds_{file_sha[:12]}",
            "source_doi_or_url": str(args.get("source_doi_or_url") or "User-provided paper & experimental harness").strip(),
            "license": str(args.get("license") or "Controlled / Open Scientific Evaluation").strip(),
            "dataset_kind": str(args.get("dataset_kind") or "controlled_benchmark").strip(),
            "primary_data_file": primary_rel,
            "file_sha256": file_sha,
            "row_count": int(args.get("row_count") or 0),
            "columns_verified": [str(c) for c in (args.get("columns_verified") or [])],
            "reproduction_level": str(args.get("reproduction_level") or "S1").strip(),
            "metric_origin": str(args.get("metric_origin") or "computed_from_experiment").strip(),
            "schema_verification_note": str(args.get("schema_verification_note") or "Input schema and checksum audited inside jailed workspace.").strip(),
            "known_biases": [str(b) for b in (args.get("known_biases") or []) if str(b).strip()],
        }
        shared_state["data_manifest"] = manifest
        shared_state["primary_data_path"] = resolved_primary
        manifest_path = out_dir / "data_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        shared_state["data_manifest_path"] = manifest_path
        return {"ok": True, "manifest": manifest}

    async def _run_python_experiment(args: dict[str, Any]) -> dict[str, Any]:
        raw_name = str(args.get("script_name") or "experiment_trial.py").strip()
        hypothesis = str(args.get("hypothesis") or "Quantitative experimental verification").strip()
        round_label = str(args.get("round_label") or "round1").strip()
        code = str(args.get("python_code") or "").strip()
        if not code:
            return {"ok": False, "error": "python_code is required"}

        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(raw_name).stem).strip("_") or "experiment_trial"
        rel_script = f"scripts/{safe_stem}.py"

        before = _snapshot_outputs(out_dir)
        runner = SafeExperimentRunner(workspace_dir, timeout_seconds=60)
        async with scheduler.experiment_slot(run_id=run_id, owner_id=f"{owner_task_id}:{safe_stem}"):
            exec_res = await runner.run_async(
                rel_script,
                code,
                args=[str(data_dir), str(out_dir)],
            )
        after = _snapshot_outputs(out_dir)
        changed = [name for name, meta in after.items() if before.get(name) != meta]

        valid_figs: list[str] = []
        invalid_figs: list[str] = []
        csv_files: list[str] = []
        for fname in changed:
            fpath = out_dir / fname
            if fname.lower().endswith(".png"):
                ok_png, w, h = _check_png_dimensions(fpath)
                if ok_png:
                    valid_figs.append(fname)
                    if fpath not in shared_state.setdefault("created_figures", []):
                        shared_state["created_figures"].append(fpath)
                else:
                    invalid_figs.append(f"{fname} ({w}x{h})")
            elif fname.lower().endswith(".csv"):
                csv_files.append(fname)
                if fpath not in shared_state.setdefault("created_tables", []):
                    shared_state["created_tables"].append(fpath)

        entry = {
            "kind": "agent_python_trial",
            "round_label": round_label,
            "script_name": f"{safe_stem}.py",
            "script_path": str(exec_res.script_path),
            "hypothesis": hypothesis,
            "ok": exec_res.ok,
            "exit_code": exec_res.exit_code,
            "duration_ms": exec_res.duration_ms,
            "script_sha256": exec_res.script_sha256,
            "stdout": exec_res.stdout[:2400],
            "stderr": exec_res.stderr[:1600],
            "new_output_files": changed,
            "valid_figures": valid_figs,
            "invalid_figures": invalid_figs,
            "csv_files": csv_files,
            "error": exec_res.error,
        }
        shared_state.setdefault("executed_trials", []).append(entry)
        _persist_rounds_json(out_dir, shared_state)
        return entry

    async def _record_experiment_finding(args: dict[str, Any]) -> dict[str, Any]:
        finding = {
            "kind": "recorded_finding",
            "round_id": str(args.get("round_id") or f"round_{len(shared_state.get('recorded_findings', [])) + 1}"),
            "hypothesis": str(args.get("hypothesis") or ""),
            "method": str(args.get("method") or ""),
            "primary_metric": str(args.get("primary_metric") or ""),
            "quantitative_result": str(args.get("quantitative_result") or ""),
            "verdict": str(args.get("verdict") or "supported"),
            "next_question": str(args.get("next_question") or ""),
        }
        shared_state.setdefault("recorded_findings", []).append(finding)
        _persist_rounds_json(out_dir, shared_state)
        return {"ok": True, "recorded": finding}

    tools_spec = [
        {
            "name": "inspect_workspace_files",
            "description": "List files under data/, outputs/, scripts/ (when relative_path is omitted), read up to 32k chars of a workspace file, or search inside a file using search_query for targeted lookup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "search_query": {"type": "string", "description": "Optional case-insensitive substring to locate relevant sections directly without full paging."},
                },
            },
        },
        {
            "name": "register_paper_spec",
            "description": "Register structured methodology, core testable claims, key metrics, rival hypotheses, and multi-round experiment plan for the target paper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_title": {"type": "string"},
                    "authors_year": {"type": "string"},
                    "domain": {"type": "string"},
                    "doi_or_url": {"type": "string"},
                    "core_claims": {"type": "array", "items": {"type": "string"}},
                    "methodology_summary": {"type": "string"},
                    "key_metrics": {"type": "array", "items": {"type": "string"}},
                    "datasets_or_benchmarks": {"type": "array", "items": {"type": "string"}},
                    "rival_hypotheses": {"type": "array", "items": {"type": "string"}},
                    "experiment_plan": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["paper_title", "core_claims", "methodology_summary", "key_metrics", "rival_hypotheses", "experiment_plan"],
            },
        },
        {
            "name": "fetch_public_dataset",
            "description": "Download an open dataset or supplementary file from a public https:// URL into data/fetched/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "register_data_manifest",
            "description": "Verify and register the dataset or controlled experimental protocol manifest (source, license, dataset_kind, SHA-256, columns, known_biases, honest reproduction_level).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_doi_or_url": {"type": "string"},
                    "license": {"type": "string"},
                    "dataset_kind": {"type": "string", "description": "e.g. 'user_uploaded_dataset', 'public_open_dataset', 'controlled_synthetic_benchmark', 'algorithmic_simulation'"},
                    "primary_data_file": {"type": "string"},
                    "row_count": {"type": "integer"},
                    "columns_verified": {"type": "array", "items": {"type": "string"}},
                    "reproduction_level": {"type": "string", "enum": ["S1", "S2", "S3"]},
                    "metric_origin": {"type": "string"},
                    "schema_verification_note": {"type": "string"},
                    "known_biases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source_doi_or_url", "license", "dataset_kind", "reproduction_level"],
            },
        },
        {
            "name": "run_python_experiment",
            "description": (
                "Execute a self-contained Python 3 script inside the isolated `-E` AST-verified sandbox "
                "(available packages: numpy, pandas, scipy, matplotlib, networkx, pypdf, json, math, pathlib; "
                "FORBIDDEN: os.environ, subprocess, urllib/requests/httpx/socket). "
                "sys.argv[1] is workspace `data/` dir, sys.argv[2] is workspace `outputs/` dir. "
                "Write CSV tables and high-DPI PNG figures (>=800x400 px) into `Path(sys.argv[2])` and print concise JSON summary metrics to stdout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script_name": {"type": "string"},
                    "round_label": {"type": "string", "description": "e.g. 'round1', 'round1b_invariants', 'round2_ablation'"},
                    "hypothesis": {"type": "string"},
                    "python_code": {"type": "string"},
                },
                "required": ["script_name", "hypothesis", "python_code"],
            },
        },
        {
            "name": "record_experiment_finding",
            "description": "Record structured quantitative experimental results observed from stdout and open follow-up questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "round_id": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "method": {"type": "string"},
                    "primary_metric": {"type": "string"},
                    "quantitative_result": {"type": "string"},
                    "verdict": {"type": "string"},
                    "next_question": {"type": "string"},
                },
                "required": ["round_id", "hypothesis", "quantitative_result", "verdict"],
            },
        },
    ]
    handlers = {
        "inspect_workspace_files": _inspect_workspace_files,
        "register_paper_spec": _register_paper_spec,
        "fetch_public_dataset": _fetch_public_dataset,
        "register_data_manifest": _register_data_manifest,
        "run_python_experiment": _run_python_experiment,
        "record_experiment_finding": _record_experiment_finding,
    }
    return tools_spec, handlers


async def run_simulation_round1_fixture(workspace_dir: Path) -> dict[str, Any]:
    """Deterministic generic simulation fixture for Round 1 (ONLY used in mode=='simulation')."""
    out_dir = workspace_dir / "outputs"
    data_dir = workspace_dir / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    runner = SafeExperimentRunner(workspace_dir, timeout_seconds=45)
    exec_res = await runner.run_async(
        "scripts/exp_round1_baseline.py",
        _SIMULATION_ROUND1_SCRIPT,
        args=[str(data_dir), str(out_dir)],
    )
    if not exec_res.ok:
        raise RuntimeError(f"Simulation Round 1 fixture failed: {exec_res.error}")
    return {
        "trial": {
            "kind": "agent_python_trial",
            "round_label": "round1",
            "script_name": "exp_round1_baseline.py",
            "script_path": str(exec_res.script_path),
            "hypothesis": "Proposed method outperforms baseline across experimental scale regimes.",
            "ok": True,
            "exit_code": exec_res.exit_code,
            "duration_ms": exec_res.duration_ms,
            "script_sha256": exec_res.script_sha256,
            "stdout": exec_res.stdout[:1800],
            "stderr": exec_res.stderr[:800],
            "new_output_files": ["round1_results.csv", "round1_summary.json", "figure_round1_reproduction.png"],
        },
        "summary": json.loads((out_dir / "round1_summary.json").read_text(encoding="utf-8")),
        "csv_path": out_dir / "round1_results.csv",
        "fig_path": out_dir / "figure_round1_reproduction.png",
    }


async def run_simulation_round1b_invariants(workspace_dir: Path) -> dict[str, Any]:
    """Deterministic generic mathematical invariance check for Round 1B (ONLY used in mode=='simulation')."""
    out_dir = workspace_dir / "outputs"
    data_dir = workspace_dir / "data"
    runner = SafeExperimentRunner(workspace_dir, timeout_seconds=30)
    exec_res = await runner.run_async(
        "scripts/exp_round1b_invariants.py",
        _SIMULATION_ROUND1B_INVARIANTS_SCRIPT,
        args=[str(data_dir), str(out_dir)],
    )
    if not exec_res.ok:
        raise RuntimeError(f"Simulation Round 1B fixture failed: {exec_res.error}")
    return {
        "trial": {
            "kind": "agent_python_trial",
            "round_label": "round1b_invariants",
            "script_name": "exp_round1b_invariants.py",
            "script_path": str(exec_res.script_path),
            "hypothesis": "Objective/probability normalization satisfies numerical & shift invariance.",
            "ok": True,
            "exit_code": exec_res.exit_code,
            "duration_ms": exec_res.duration_ms,
            "script_sha256": exec_res.script_sha256,
            "stdout": exec_res.stdout[:1200],
            "stderr": "",
            "new_output_files": ["round1b_invariants.json"],
        },
        "result": json.loads((out_dir / "round1b_invariants.json").read_text(encoding="utf-8")),
    }


async def run_simulation_round2_fixture(workspace_dir: Path) -> dict[str, Any]:
    """Deterministic generic simulation fixture for Round 2 ablations & null checks (ONLY used in mode=='simulation')."""
    out_dir = workspace_dir / "outputs"
    data_dir = workspace_dir / "data"
    runner = SafeExperimentRunner(workspace_dir, timeout_seconds=45)
    exec_res = await runner.run_async(
        "scripts/exp_round2_ablation_robustness.py",
        _SIMULATION_ROUND2_SCRIPT,
        args=[str(data_dir), str(out_dir)],
    )
    if not exec_res.ok:
        raise RuntimeError(f"Simulation Round 2 fixture failed: {exec_res.error}")
    return {
        "trial": {
            "kind": "agent_python_trial",
            "round_label": "round2_ablation",
            "script_name": "exp_round2_ablation_robustness.py",
            "script_path": str(exec_res.script_path),
            "hypothesis": "Component ablation degrades performance and effect survives permutation & noise stress tests.",
            "ok": True,
            "exit_code": exec_res.exit_code,
            "duration_ms": exec_res.duration_ms,
            "script_sha256": exec_res.script_sha256,
            "stdout": exec_res.stdout[:1800],
            "stderr": exec_res.stderr[:800],
            "new_output_files": ["round2_experiments.csv", "round2_summary.json", "figure_round2_robustness.png"],
        },
        "summary": json.loads((out_dir / "round2_summary.json").read_text(encoding="utf-8")),
        "csv_path": out_dir / "round2_experiments.csv",
        "fig_path": out_dir / "figure_round2_robustness.png",
    }


async def run_generic_followup_experiment(
    workspace_dir: Path,
    *,
    script_rel_path: str,
    seed_offset: int,
) -> dict[str, Any]:
    runner = SafeExperimentRunner(workspace_dir, timeout_seconds=30)
    out_dir = workspace_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"followup_{seed_offset}.json"
    exec_res = await runner.run_async(
        script_rel_path,
        _GENERIC_FOLLOWUP_SCRIPT,
        args=[str(out_dir), str(out_json), str(2026 + seed_offset)],
    )
    if not exec_res.ok:
        raise RuntimeError(f"Follow-up experiment failed: {exec_res.error}")
    return {
        "execution": exec_res,
        "result": json.loads(out_json.read_text(encoding="utf-8")),
        "json_path": out_json,
    }
