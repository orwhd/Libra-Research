from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import MAX_RETRIES, RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..services.sandbox_runner import run_python_sandbox
from .capability_catalog import DATASET_SCHEMA_GUIDE, discover_python_sandbox_capabilities
from .llm_client import LLMClient, extract_code_block


class AnalysisAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def get_analysis_run(self, analysis_run_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE analysis_run_id = ?",
                (analysis_run_id,),
            ).fetchone()
            return dict(row) if row else None

    async def run_analysis(
        self,
        workspace_id: str,
        snapshot: dict[str, Any],
        analytical_goal: str,
        chart_kind: str,
        revision_notes: str = "",
        reuse_analysis_run_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generates and executes real Python analytical computation (Pandas / NetworkX / SciPy)
        against an immutable DatasetSnapshot inside `python -I` sandbox.
        """
        if reuse_analysis_run_id:
            existing = self.get_analysis_run(reuse_analysis_run_id)
            if existing and existing.get("status") == "completed":
                return existing

        snapshot_id = snapshot["snapshot_id"]
        records_path = RUNTIME_DIR / snapshot["records_ref"]
        snapshot_payload = json.loads(records_path.read_text(encoding="utf-8"))
        sample_records = snapshot_payload.get("records", [])[:3]
        is_uploaded = snapshot.get("retrieval_mode") in ("user_uploaded_files", "user_attached_url")
        sandbox_capabilities = discover_python_sandbox_capabilities()
        schema_guide_section = (
            f"Data Source: User-uploaded dataset (`{snapshot.get('source_name')}`). Rely strictly on the Snapshot Schema & Sample records above."
            if is_uploaded
            else DATASET_SCHEMA_GUIDE
        )

        analysis_run_id = f"ar_{uuid.uuid4().hex[:10]}"
        run_dir = RUNTIME_DIR / "analyses" / analysis_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result_path = run_dir / "result.json"

        last_error = ""
        script_code = ""
        sandbox_res = None

        for attempt in range(MAX_RETRIES + 1):
            error_feedback = (
                f"\nPrevious attempt failed with error:\n{last_error}\nFix the Python code carefully."
                if last_error
                else ""
            )
            prompt = f"""
You are the Analysis Agent in a scientific multi-agent data science framework.
Write Python 3.13 analysis code that reads `sys.argv[1]` (the DatasetSnapshot JSON `{{"records": [...], ...}}`) and writes the computed analytical output JSON to `sys.argv[2]`.

Analytical Goal:
{analytical_goal}

Requested Visualization Paradigm / Context (for structuring appropriate computed artifacts):
{chart_kind}

{f"Revision / Comment Considerations impacting computation:{chr(10)}{revision_notes}" if revision_notes else ""}

Snapshot ID: {snapshot_id} ({snapshot['retained_record_count']} records)
Snapshot Schema:
{json.dumps(loads(snapshot.get("schema_json"), {}), ensure_ascii=False, indent=2)}

Sample records from `data["records"]`:
{json.dumps(sample_records, ensure_ascii=False, indent=2)[:4500]}

{schema_guide_section}

{sandbox_capabilities}

Output JSON contract written to `sys.argv[2]`:
{{
  "analysis_algorithm": "<concise name of the statistical, clustering, graph, temporal, or matrix algorithm you selected>",
  "params": {{ "<key>": <value>, ... }},
  "summary": {{
    "headline": "<1-sentence concise empirical finding grounded in computed numbers, written in the SAME human language as the Analytical Goal (no internal IDs or variable names)>",
    "key_metrics": {{ "<metric>": <number_or_string>, ... }}
  }},
  "unit_transform": {{
    "input_count": {snapshot['retained_record_count']},
    "input_unit": "records | works | rows",
    "output_count": <int count of primary output items/nodes/buckets>,
    "output_unit": "<e.g. entities & links | time buckets | matrix cells | hierarchy leaves>",
    "unit_changed": <true if unit aggregated or transformed from input records, else false>,
    "conversion_explanation": "<concise explanation of how input records were aggregated, projected, or filtered into output entities>"
  }},
  "data": <structured JSON tailored to the analytical & visual requirements>
}}

Generalizable Analytical Design Principles (NO hardcoded templates):
- Compute EVERY value, entity, relationship, distribution, and metric strictly from `data["records"]`. Never invent synthetic records or numbers.
- Autonomously select the best-suited algorithms from the discovered sandbox packages (`pandas`, `numpy`, `scipy`, `networkx`, `scikit-learn`, etc.) based on the data schema and analytical goal.
- Structure `data` to support rich visual legibility and interactive exploration (e.g., bound extreme cardinalities to high-signal subsets when projecting graphs/hierarchies/matrices so views remain readable, include rich per-item attributes for tooltips/filtering, and guarantee referential integrity so every link/edge endpoint exists in the emitted node/leaf set).
- Convert all numpy/pandas scalar types to native Python `int`/`float` and replace or guard against `NaN`/`Infinity`.
- Output ONLY valid Python code inside a single ```python ... ``` block.
{error_feedback}
"""
            raw_resp = await self.llm.complete_text(
                system_prompt="You are a quantitative research scientist & Python data engineer. Output only runnable Python code.",
                user_content=prompt,
                max_tokens=12288,
                effort_override="low" if attempt > 0 else None,
            )
            script_code = extract_code_block(raw_resp, ("python", "py"))
            sandbox_res = await asyncio.to_thread(
                run_python_sandbox,
                script_code=script_code,
                work_dir=run_dir / f"attempt_{attempt}",
                input_path=records_path,
                output_path=result_path,
            )
            if sandbox_res.ok and isinstance(sandbox_res.output_data, dict):
                d_val = sandbox_res.output_data.get("data")
                if d_val is not None and (isinstance(d_val, (dict, list)) and len(d_val) > 0):
                    break
                last_error = "Computed 'data' field is empty; verify filtering thresholds so non-empty analytical output is produced."
            else:
                last_error = sandbox_res.error or "Analysis script failed."

        if not sandbox_res or not sandbox_res.ok or not isinstance(sandbox_res.output_data, dict):
            now = utc_now_iso()
            with get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO analysis_runs (
                        analysis_run_id, workspace_id, dataset_snapshot_id,
                        analysis_algorithm, params_json, script_text,
                        summary_json, result_ref, stdout, stderr, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_run_id,
                        workspace_id,
                        snapshot_id,
                        "failed_analysis",
                        "{}",
                        script_code,
                        "{}",
                        "",
                        sandbox_res.stdout if sandbox_res else "",
                        last_error,
                        "failed",
                        now,
                    ),
                )
            raise RuntimeError(f"AnalysisAgent failed after {MAX_RETRIES + 1} attempts: {last_error}")

        out = sandbox_res.output_data
        now = utc_now_iso()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO analysis_runs (
                    analysis_run_id, workspace_id, dataset_snapshot_id,
                    analysis_algorithm, params_json, script_text,
                    summary_json, result_ref, stdout, stderr, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_run_id,
                    workspace_id,
                    snapshot_id,
                    str(out.get("analysis_algorithm") or chart_kind),
                    dumps(out.get("params", {})),
                    script_code,
                    dumps(out.get("summary", {})),
                    str(result_path.relative_to(RUNTIME_DIR)),
                    sandbox_res.stdout,
                    sandbox_res.stderr,
                    "completed",
                    now,
                ),
            )

        return self.get_analysis_run(analysis_run_id)  # type: ignore[return-value]
