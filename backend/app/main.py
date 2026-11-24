from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .config import VENDOR_DIR
from .db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    yield


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
