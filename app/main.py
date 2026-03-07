"""
FactGuard FastAPI application entry point.

Run:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api.routes import router
from app.database.db import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised.")
    yield


app = FastAPI(
    title="FactGuard",
    description="AI-powered misinformation detection pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (allow all localhost origins during development) ────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ───────────────────────────────────────────────────────────────
app.include_router(router)

# ── Serve frontend static files ──────────────────────────────────────────────
_frontend = Path(__file__).parent.parent / "frontend"

if (_frontend / "static").exists():
    app.mount("/static", StaticFiles(directory=str(_frontend / "static")), name="static")

@app.get("/", include_in_schema=False)
async def serve_index():
    index = _frontend / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "FactGuard API is running. See /docs for API reference."}

@app.get("/health")
async def health():
    return {"status": "ok"}
