from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from .api import (
    RUNTIME,
    build_reproduce_sse_response,
    get_reproduce_run_or_none,
    router as reproduce_router,
)
from .schemas import Run
from .storage.db import init_reproduce_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_reproduce_db()
    RUNTIME.event_store.recover_orphaned_running_runs()
    yield


app = FastAPI(
    title="Libra Research Reproduce Harness API V2 (Standalone)",
    version="0.2.0",
    description="Standalone backend-only service implementing OpenAPI 3.1 V2 for Scientific Paper Reproduction.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def standalone_health() -> dict[str, object]:
    can_live, provider = RUNTIME.coordinator.evidence_gate and (True, "ready")
    from .harness.sdk_adapter import SDKAdapter

    live_ok, live_provider = SDKAdapter(RUNTIME.artifact_store.root_dir).detect_live_capability()
    return {
        "ok": True,
        "service": "libra-reproduce-v2",
        "live_capable": live_ok,
        "provider": live_provider,
    }


@app.get("/api/runs/{run_id}", response_model=Run)
async def standalone_get_run(run_id: str) -> Run:
    r = get_reproduce_run_or_none(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    return r


@app.get("/api/runs/{run_id}/events")
async def standalone_stream_events(
    run_id: str,
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    return await build_reproduce_sse_response(run_id, after=after, last_event_id=last_event_id)


app.include_router(reproduce_router)
