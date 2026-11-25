from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .config import VENDOR_DIR
from .db import init_db
from .services.weekly_trending_charts import (
    get_weekly_trending_charts,
    has_fresh_weekly_cache,
)


async def _warm_weekly_trending() -> None:
    try:
        await get_weekly_trending_charts()
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    warm_task: asyncio.Task[None] | None = None
    if not os.getenv("PYTEST_CURRENT_TEST") and not has_fresh_weekly_cache():
        warm_task = asyncio.create_task(_warm_weekly_trending())
    try:
        yield
    finally:
        if warm_task is not None and not warm_task.done():
            warm_task.cancel()


app = FastAPI(
    title="WHALES Lab Project 1 — Research Canvas API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/vendor", StaticFiles(directory=str(VENDOR_DIR)), name="vendor")
app.include_router(api_router)
