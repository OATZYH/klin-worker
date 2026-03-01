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
from app.services.classification_service import ClassificationService
from app.services.llm_client import llm_client
from app.services.rag_service import RagService
from app.services.seed_service import generate_missing_embeddings, seed_default_categories
from app.services.startup_checks import CheckResult, run_all_checks

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


# ── Startup check results (populated in lifespan, read by /health) ───────

_startup_checks: list[CheckResult] = []


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle hook."""
    global _startup_checks

    logger.info("🚀  Starting %s v%s", settings.app_name, settings.app_version)

    # ── 1. Ensure data directory exists ──────────────────────────────
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)

    # ── 2. Initialise SQLite (run Alembic migrations) ────────────────
    await run_migrations()

    # ── 3. Load GGUF model in-process (llama-cpp-python) ─────────────
    try:
        llm_client.startup()
    except Exception:
        logger.warning(
            "llama-cpp-python model failed to load — "
            "the API will work without AI features."
        )

    # ── 4. Initialise RAG-Anything (heavy — do it once) ──────────────
    try:
        await _rag_service.setup()
    except Exception:
        logger.warning(
            "RAG-Anything failed to initialise — "
            "the API will work without semantic features."
        )

    # ── 5. Run startup checks (DB, llama-cpp-python, RAG) ───────────
    try:
        from app.db.session import AsyncSession, engine

        async with AsyncSession(engine, expire_on_commit=False) as db:
            _startup_checks = await run_all_checks(db, _rag_service)
    except Exception:
        logger.warning("Startup checks failed to execute.", exc_info=True)

    # Check if llama-cpp-python model is loaded (needed for embeddings)
    _llamacpp_ok = any(r.name == "llama-cpp-python" and r.ok for r in _startup_checks)

    # ── 5. Seed default categories & generate embeddings ─────────────
    try:
        from app.db.session import AsyncSession, engine

        async with AsyncSession(engine, expire_on_commit=False) as db:
            seeded = await seed_default_categories(db)
            await db.commit()

            if seeded > 0:
                logger.info("First run detected — %d default categories created.", seeded)

            if _rag_service.is_ready and _llamacpp_ok:
                classifier = ClassificationService(_rag_service)
                embedded = await generate_missing_embeddings(db, classifier)
                await db.commit()
                if embedded > 0:
                    logger.info("Generated embeddings for %d categories.", embedded)
            elif not _llamacpp_ok:
                logger.warning(
                    "Skipping category embedding generation — GGUF model is not loaded. "
                    "Embeddings will be generated on next startup when the model is available."
                )
    except Exception:
        logger.warning("Category seeding / embedding generation failed.", exc_info=True)

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────
    llm_client.shutdown()
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
    checks = {
        r.name: {"ok": r.ok, "detail": r.detail}
        for r in _startup_checks
    }
    all_ok = all(r.ok for r in _startup_checks) if _startup_checks else False
    return {
        "status": "ok" if all_ok else "degraded",
        "version": settings.app_version,
        "services": checks,
        "rag_ready": _rag_service.is_ready,
    }
