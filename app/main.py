"""
Klin-Worker — AI Workspace backend.

FastAPI application entry point.
  • SQLite initialised at startup (lifespan)
  • RAG-Anything initialised at startup (lifespan)
  • CORS enabled for Tauri dev mode
  • Health-check at /health
  • Organize API at /api/organize
  • Categories CRUD at /api/categories
  • History log at /api/history
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.categories import router as categories_router
from app.api.history import router as history_router
from app.api.organize import router as organize_router
from app.core.config import settings
from app.db.migrations import run_migrations
from app.services.rag_service import RagService

# ── Logging ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Shared service instances ─────────────────────────────────────────────

_rag_service = RagService()


def get_rag_service() -> RagService:
    """Accessor used by dependency injection in routers."""
    return _rag_service


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle hook."""
    logger.info("🚀  Starting %s v%s", settings.app_name, settings.app_version)

    # Ensure data directory exists
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialise SQLite (create tables)
    await run_migrations()

    # Initialise RAG-Anything (heavy — do it once)
    try:
        await _rag_service.setup()
    except Exception:
        logger.warning(
            "RAG-Anything failed to initialise — "
            "the API will work without semantic features."
        )

    yield  # ← application runs here

    logger.info("👋  Shutting down %s", settings.app_name)


# ── App factory ──────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS — allow Tauri frontend in dev mode
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(organize_router)
app.include_router(categories_router)
app.include_router(history_router)


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "rag_ready": _rag_service.is_ready,
    }
