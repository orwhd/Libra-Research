from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import MAX_RETRIES, RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..services.sandbox_runner import run_python_sandbox
from .capability_catalog import DATASET_SCHEMA_GUIDE, PYTHON_SANDBOX_CAPABILITIES
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
        if reuse_analysis_run_id and not revision_notes:
            existing = self.get_analysis_run(reuse_analysis_run_id)
            if existing and existing.get("status") == "completed":
                return existing

        snapshot_id = snapshot["snapshot_id"]
        records_path = RUNTIME_DIR / snapshot["records_ref"]
        snapshot_payload = json.loads(records_path.read_text(encoding="utf-8"))
        sample_records = snapshot_payload.get("records", [])[:3]
        is_uploaded = snapshot.get("retrieval_mode") == "user_uploaded_files"
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

{PYTHON_SANDBOX_CAPABILITIES}

Output JSON contract written to `sys.argv[2]`:
{{
  "analysis_algorithm": "<concise method name, e.g. 'Yearly Publication & Citation Aggregation', 'Co-authorship Network + Louvain Communities', 'Taxonomic Hierarchical Edge Bundling Graph', 'Topic-by-Year Contingency Matrix'>",
  "params": {{ "<key>": <value>, ... }},
  "summary": {{
    "headline": "<1-sentence empirical finding grounded in computed numbers>",
    "key_metrics": {{ "<metric>": <number_or_string>, ... }}
  }},
  "data": <structured JSON tailored to the analytical goal (e.g. series rows; or nodes + links with community/degree/citations; or nested hierarchy tree + bundle_links; or matrix cells + row/col totals)>
}}

Strict analytical & anti-hallucination rules:
- Compute EVERY value, node, edge, hierarchy path, and metric strictly from `data["records"]`. Never invent synthetic nodes or numbers!
- If computing a co-authorship or collaboration network: build real co-author edges from `authorships`, keep a readable high-signal subgraph (e.g. top 40-65 authors by degree/papers/citations who actually participate in co-authorship edges so there are no huge unreadable clouds, include `degree`, `papers`, `citations`, `institution`, `community` per node, and `weight`, `shared_papers` per edge).
- If computing a hierarchical bundled network: build a genuine 3+ level tree (`name`, `id`, `children`) from OpenAlex `primary_topic` / `topics` taxonomy (`domain -> field -> subfield -> topic` or `subfield -> topic` depending on diversity; check actual distinct values so there are >= 15 leaf nodes across >= 3 groups!) and derive real `bundle_links` (`source`, `target`, `weight`, `shared_papers`) from topics or subfields co-occurring on the same works (or cross-topic author overlap). Ensure EVERY `source` and `target` in `bundle_links` matches a real leaf node `id` in `hierarchy`!
- Convert all numpy/pandas int64/float64 values to native Python `int`/`float`. Avoid `NaN`/`Infinity`.
- Output ONLY valid Python code inside a single ```python ... ``` block.
{error_feedback}
"""
            raw_resp = await self.llm.complete_text(
                system_prompt="You are a quantitative research scientist & Python data engineer. Output only runnable Python code.",
                user_content=prompt,
                max_tokens=16384,
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
