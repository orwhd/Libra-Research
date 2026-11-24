from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import MAX_RETRIES, RUNTIME_DIR
from ..db import dumps, get_conn, loads, utc_now_iso
from ..services.openalex_client import DEFAULT_FILTER, fetch_openalex_corpus
from ..services.sandbox_runner import run_python_sandbox
from .capability_catalog import DATASET_SCHEMA_GUIDE, PYTHON_SANDBOX_CAPABILITIES
from .llm_client import LLMClient, extract_code_block


def _load_uploaded_bundle(upload_id: str) -> dict[str, Any]:
    upload_dir = RUNTIME_DIR / "uploads" / upload_id
    manifest_path = upload_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Upload {upload_id} manifest not found.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_dir = upload_dir / "files"

    parsed_files: list[dict[str, Any]] = []
    for rel in manifest.get("files", []):
        fp = files_dir / rel
        if not fp.exists():
            continue
        ext = fp.suffix.lower()
        entry: dict[str, Any] = {
            "relative_path": rel,
            "extension": ext,
            "size_bytes": fp.stat().st_size,
        }
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
            if ext == ".csv":
                rows = list(csv.DictReader(io.StringIO(text)))[:5000]
                entry["format"] = "csv_rows"
                entry["rows"] = rows
            elif ext == ".tsv":
                rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))[:5000]
                entry["format"] = "tsv_rows"
                entry["rows"] = rows
            elif ext == ".json":
                entry["format"] = "json"
                entry["data"] = json.loads(text)
            elif ext == ".jsonl":
                entry["format"] = "jsonl"
                entry["rows"] = [json.loads(line) for line in text.splitlines() if line.strip()][:5000]
            else:
                entry["format"] = "text"
                entry["text"] = text[:100000]
        except Exception as exc:  # noqa: BLE001
            entry["format"] = "unparsed"
            entry["parse_error"] = str(exc)
        parsed_files.append(entry)

    return {
        "upload_id": upload_id,
        "label": manifest.get("label", "Uploaded Files"),
        "source_url": manifest.get("source_url"),
        "source_kind": manifest.get("source_kind", "files"),
        "created_at": manifest.get("created_at", utc_now_iso()),
        "files": parsed_files,
    }


class DataAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            return dict(row) if row else None

    async def prepare_snapshot(
        self,
        workspace_id: str,
        data_scope: str,
        reuse_snapshot_id: str | None = None,
        upload_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Either reuses an immutable DatasetSnapshot, ingests user-uploaded files/folder,
        or retrieves OpenAlex works and runs an LLM-generated Python transformation script
        inside `python -I` sandbox to produce a new immutable DatasetSnapshot.
        """
        if reuse_snapshot_id:
            existing = self.get_snapshot(reuse_snapshot_id)
            if existing:
                return existing

        snapshot_id = f"ds_{uuid.uuid4().hex[:10]}"
        snap_dir = RUNTIME_DIR / "snapshots" / snapshot_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        records_path = snap_dir / "records.json"

        is_uploaded = bool(upload_id)
        if upload_id:
            bundle = _load_uploaded_bundle(upload_id)
            raw_path = snap_dir / "raw_upload.json"
            raw_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            source_url = bundle.get("source_url")
            prov = {
                "source_name": (
                    f"Attached URL ({bundle['label']})"
                    if source_url
                    else f"User Upload ({bundle['label']})"
                ),
                "source_url": str(source_url) if source_url else f"upload://{upload_id}",
                "retrieval_time": bundle["created_at"],
                "retrieval_mode": "user_attached_url" if source_url else "user_uploaded_files",
                "query_filter": f"files={len(bundle['files'])} | label={bundle['label']}",
                "raw_record_count": sum(
                    len(f.get("rows", [])) if "rows" in f else 1 for f in bundle["files"]
                ),
                "api_total_matching_works": None,
                "fallback_reason": None,
            }
            preview_files = [
                {
                    "relative_path": f["relative_path"],
                    "format": f.get("format"),
                    "row_count": len(f.get("rows", [])) if "rows" in f else None,
                    "sample": (
                        f.get("rows", [])[:2]
                        if "rows" in f
                        else (f.get("data")[:2] if isinstance(f.get("data"), list) else str(f.get("text", f.get("data", "")))[:600])
                    ),
                }
                for f in bundle["files"][:12]
            ]
        else:
            corpus = await fetch_openalex_corpus(filter_expr=DEFAULT_FILTER, max_records=400)
            works = corpus["works"]
            prov = corpus["provenance"]
            raw_path = snap_dir / "raw_works.json"
            raw_path.write_text(json.dumps(works, ensure_ascii=False), encoding="utf-8")
            preview_files = []

        last_error = ""
        script_code = ""
        sandbox_res = None

        for attempt in range(MAX_RETRIES + 1):
            error_feedback = (
                f"\nPrevious attempt failed with error:\n{last_error}\nFix the script carefully."
                if last_error
                else ""
            )
            if is_uploaded:
                prompt = f"""
You are the Data Agent in a scientific data analysis harness.
The user uploaded local files/folder. Write a clean Python 3.13 ingestion & cleaning script that reads the uploaded bundle JSON from `sys.argv[1]` and writes a normalized snapshot JSON to `sys.argv[2]`.

Uploaded Bundle Summary:
{json.dumps(preview_files, ensure_ascii=False, indent=2)[:5500]}

Target data scope for this research task:
{data_scope}

{PYTHON_SANDBOX_CAPABILITIES}

Input JSON schema (`sys.argv[1]`):
{{"upload_id": "...", "label": "...", "files": [{{"relative_path": "...", "extension": "...", "format": "csv_rows|tsv_rows|json|jsonl|text", "rows": [...], "data": ..., "text": "..."}}]}}

Requirements for the output JSON written to `sys.argv[2]`:
{{
  "records": [ ... non-empty list of cleaned structured dicts extracted from the uploaded files (e.g. tabular rows, JSON records, or parsed document summaries with file_path/line/section fields) ... ],
  "schema": {{ "<field_name>": "<type & description>", ... }},
  "missingness_quality": {{
    "total_raw": <int>,
    "retained": <int>,
    "files_parsed": <int>
  }},
  "transform_steps": [ "<concise step 1>", "<concise step 2>", ... ]
}}

Rules:
- Convert numeric strings cleanly when appropriate, preserve original keys, never return an empty `records` list.
- Output ONLY valid Python code inside a single ```python ... ``` block.
{error_feedback}
"""
            else:
                prompt = f"""
You are the Data Agent in a scientific data analysis harness.
Write a clean Python 3.13 transformation script that reads raw OpenAlex work records from `sys.argv[1]` (a JSON list of works) and writes a structured cleaned snapshot JSON to `sys.argv[2]`.

Target data scope for this research task:
{data_scope}

{DATASET_SCHEMA_GUIDE}

{PYTHON_SANDBOX_CAPABILITIES}

Requirements for the output JSON written to `sys.argv[2]`:
{{
  "records": [ ... cleaned list of OpenAlex work dicts preserving nested authorships, primary_topic, topics, concepts, referenced_works, cited_by_count, publication_year ... ],
  "schema": {{ "<field_name>": "<type & description>", ... }},
  "missingness_quality": {{
    "total_raw": <int>,
    "retained": <int>,
    "missing_year": <int>,
    "missing_primary_topic": <int>,
    "missing_authorships": <int>
  }},
  "transform_steps": [ "<concise step 1>", "<concise step 2>", ... ]
}}

Rules:
- Unless `data_scope` explicitly restricts years/topics/minimum citations, retain all valid records with a valid `publication_year` (never drop records unnecessarily).
- Deduplicate by work `id` if duplicates exist.
- Output ONLY valid Python code inside a single ```python ... ``` block.
{error_feedback}
"""
            raw_resp = await self.llm.complete_text(
                system_prompt="You are a meticulous scientific data engineer. Output only runnable Python code.",
                user_content=prompt,
                max_tokens=16384,
            )
            script_code = extract_code_block(raw_resp, ("python", "py"))
            sandbox_res = await asyncio.to_thread(
                run_python_sandbox,
                script_code=script_code,
                work_dir=snap_dir / f"attempt_{attempt}",
                input_path=raw_path,
                output_path=records_path,
            )
            if sandbox_res.ok and isinstance(sandbox_res.output_data, dict) and sandbox_res.output_data.get("records"):
                break
            last_error = sandbox_res.error or "Output JSON missing non-empty 'records' list."

        if not sandbox_res or not sandbox_res.ok or not isinstance(sandbox_res.output_data, dict):
            raise RuntimeError(f"DataAgent failed after {MAX_RETRIES + 1} attempts: {last_error}")

        out = sandbox_res.output_data
        retained = len(out.get("records", []))
        now = utc_now_iso()
        upstream_deps = [
            {
                "name": prov.get("source_name"),
                "url": prov.get("source_url"),
                "api_total_matching_works": prov.get("api_total_matching_works"),
                "fallback_reason": prov.get("fallback_reason"),
            }
        ]

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO dataset_snapshots (
                    snapshot_id, workspace_id, source_name, source_url,
                    retrieval_time, retrieval_mode, query_filter,
                    raw_record_count, retained_record_count,
                    schema_json, missingness_json, transform_steps_json,
                    upstream_dependencies_json, script_text, records_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    workspace_id,
                    prov["source_name"],
                    prov["source_url"],
                    prov["retrieval_time"],
                    prov["retrieval_mode"],
                    f"{prov['query_filter']} | task_scope={data_scope[:160]}",
                    prov["raw_record_count"],
                    retained,
                    dumps(out.get("schema", {})),
                    dumps(out.get("missingness_quality", {})),
                    dumps(out.get("transform_steps", [])),
                    dumps(upstream_deps),
                    script_code,
                    str(records_path.relative_to(RUNTIME_DIR)),
                    now,
                ),
            )

        return self.get_snapshot(snapshot_id)  # type: ignore[return-value]
