from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import uuid
from pathlib import Path

import uvicorn

from .api import RUNTIME
from .schemas import ChatCommand
from .storage.db import init_reproduce_db

DEFAULT_REPRODUCE_PROMPT = (
    "Reproduce the core methodology, quantitative claims, and robustness ablations of the target paper."
)


async def _run_headless(
    *,
    prompt: str,
    paper_id: str,
    mode: str,
    output_dir: Path | None,
) -> int:
    init_reproduce_db()
    run = RUNTIME.event_store.create_run(
        title=prompt[:72],
        paper_id=paper_id,
        mode="simulation" if mode == "simulation" else "live",
    )
    cmd = ChatCommand(
        request_id=f"cli_{uuid.uuid4().hex[:10]}",
        text=prompt,
    )
    status_code, chat_resp = await RUNTIME.gateway.handle_chat(run.run_id, cmd)
    print(f"[CLI] Created run={run.run_id} mode={run.mode} HTTP={status_code} status={chat_resp.status}")

    # Wait for background coordinator task to reach terminal state
    while True:
        cur = RUNTIME.event_store.get_run(run.run_id)
        if cur and cur.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.25)

    final_run = RUNTIME.event_store.get_run(run.run_id)
    artifacts = RUNTIME.artifact_store.list_run_artifacts_rich(run.run_id)
    events = RUNTIME.event_store.list_events(run.run_id)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for art in artifacts:
            resolved = RUNTIME.artifact_store.resolve_artifact_bytes(art["artifact_id"])
            if resolved:
                src_path, fname, _ = resolved
                shutil.copy2(src_path, output_dir / fname)
        (output_dir / "events.jsonl").write_text(
            "\n".join(e.model_dump_json() for e in events) + "\n",
            encoding="utf-8",
        )
        (output_dir / "artifacts_manifest.json").write_text(
            json.dumps(artifacts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_payload = {
        "run_id": run.run_id,
        "mode": run.mode,
        "final_status": final_run.status if final_run else "unknown",
        "event_count": len(events),
        "artifacts": [
            {
                "artifact_id": a["artifact_id"],
                "kind": a["kind"],
                "filename": a["filename"],
                "sha256": a["sha256"],
                "validation_status": a["validation_status"],
            }
            for a in artifacts
        ],
        "exported_to": str(output_dir.resolve()) if output_dir else None,
    }
    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))
    return 0 if (final_run and final_run.status == "completed") else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="libra-reproduce",
        description="Standalone Backend CLI & API Server for Libra Research Project 2 (Paper Reproduction).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="Execute an end-to-end scientific reproduction run headlessly.")
    p_run.add_argument("--prompt", default=DEFAULT_REPRODUCE_PROMPT, help="Research question or paper instruction.")
    p_run.add_argument("--paper-id", default="paper_A", help="Target paper identifier (default: paper_A).")
    p_run.add_argument("--mode", choices=["live", "simulation"], default="live", help="Execution mode.")
    p_run.add_argument(
        "--output-dir",
        default="",
        help="Optional directory to export generated reports, figures, scripts, CSVs, and events.jsonl.",
    )

    p_serve = sub.add_parser("serve", help="Start standalone FastAPI OpenAPI V2 server.")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8001)

    args = parser.parse_args()
    if args.subcommand == "serve":
        uvicorn.run("libra_reproduce.main:app", host=args.host, port=args.port, reload=False)
    elif args.subcommand == "run":
        out = Path(args.output_dir) if args.output_dir else None
        code = asyncio.run(
            _run_headless(
                prompt=args.prompt,
                paper_id=args.paper_id,
                mode=args.mode,
                output_dir=out,
            )
        )
        raise SystemExit(code)


if __name__ == "__main__":
    main()
