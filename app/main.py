"""
Klin-Worker — AI File Organizer backend.

FastAPI application entry point.
  • CORS enabled for Tauri dev mode
  • RAG-Anything initialised at startup (lifespan)
  • Health-check at /health
  • Organize API at /api/organize
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.organize import router as organize_router
from app.core.config import settings
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

    # Initialise RAG-Anything (heavy — do it once)
    try:
        await _rag_service.setup()
    except Exception:
        logger.warning(
            "RAG-Anything failed to initialise — "
            "the /api/organize endpoint will work without semantic features."
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


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "rag_ready": _rag_service.is_ready,
    }
