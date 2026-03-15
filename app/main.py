"""
Klin-Worker — AI Workspace backend.

FastAPI application entry point.
  • SQLite initialised at startup (lifespan)
  • RAG-Anything initialised at startup (lifespan)
  • CORS enabled for Tauri dev mode
  • Health-check at /health
  • Organize API at /api/organize
  • Summary API at /api/summary
        • Settings API at /api/settings (categories, base path)
  • History log at /api/history
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_voyager import create_voyager
import fastapi_voyager.voyager as voyager_module
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.history import router as history_router
from app.api.organize import router as organize_router
from app.api.search import router as search_router
from app.api.settings import router as settings_router
from app.api.summary import router as summary_router
from app.core.config import settings
from app.db.migrations import run_migrations
from app.db.session import engine
from app.services.background_ingest import BackgroundIngestWorker
from app.services.categories.classification_service import ClassificationService
from app.services.files.docling_parser import DoclingParser
from app.services.files.text_cache import TextCache
from app.services.ai.llm_client import llm_client
from app.services.ai.rag_service import RagService
from app.services.categories.seed_service import generate_missing_embeddings
from app.services.startup_checks import CheckResult, run_all_checks
from app.services.system_log_service import SystemLogService

# ── Logging ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Shared service instances ─────────────────────────────────────────────

_rag_service = RagService()
_system_log_service = SystemLogService()
_text_cache = TextCache()
_docling_parser = DoclingParser()
ingest_worker = BackgroundIngestWorker(parser=_docling_parser)

def _patch_fastapi_voyager_core_type_handling() -> None:
    """Prevent voyager crashes on routes without class-based response models.

    fastapi-voyager currently assumes every item returned by get_core_types()
    is a class and calls issubclass() directly, which raises TypeError for
    values like None or dict[str, Any]. Filter them out before analysis.
    """

    original_get_core_types = voyager_module.get_core_types

    def _safe_get_core_types(tp: object) -> tuple[type, ...]:
        core_types = original_get_core_types(tp)
        return tuple(item for item in core_types if isinstance(item, type))

    voyager_module.get_core_types = _safe_get_core_types

_patch_fastapi_voyager_core_type_handling()


def get_rag_service() -> RagService:
    """Accessor used by dependency injection in routers."""
    return _rag_service


def get_text_cache() -> TextCache:
    """Accessor used by dependency injection in routers."""
    return _text_cache


# ── Startup check results (populated in lifespan, read by /health) ───────

_startup_checks: list[CheckResult] = []


async def _write_system_log(
    *,
    level: str,
    event_type: str,
    message: str,
    context: dict | None = None,
) -> None:
    """Persist a lifecycle / operational event without breaking the app."""
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await _system_log_service.log(
                db=db,
                level=level,
                component="app.lifecycle",
                event_type=event_type,
                message=message,
                context=context,
            )
            await db.commit()
    except Exception:
        logger.warning(
            "Failed to persist system log event '%s'.",
            event_type,
            exc_info=True,
        )


async def _cleanup_system_logs() -> int:
    """Delete old system logs based on retention settings."""
    if not settings.cleanup_system_logs_on_startup:
        return 0

    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            deleted_count = await _system_log_service.cleanup_old_logs(
                db,
                retention_days=settings.system_log_retention_days,
            )
            await db.commit()
            return deleted_count
    except Exception:
        logger.warning("System log cleanup failed.", exc_info=True)
        return 0


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
    cleaned_system_logs = await _cleanup_system_logs()

    # ── 3. Connect to llama-server (out-of-process) ───────────────────
    try:
        await llm_client.startup()
    except Exception:
        logger.warning(
            "llama-server connection failed — the API will work without AI features.",
            exc_info=True,
        )
        await _write_system_log(
            level="WARNING",
            event_type="llm_startup_failed",
            message="llama-server connection failed at startup.",
            context={"component": "llm_client"},
        )

    # ── 4. Initialise RAG-Anything (heavy — do it once) ──────────────
    try:
        await _rag_service.setup()
    except Exception:
        logger.warning(
            "RAG-Anything failed to initialise — "
            "the API will work without semantic features."
        )
        await _write_system_log(
            level="WARNING",
            event_type="rag_startup_failed",
            message="RAG service failed to initialize at startup.",
            context={"component": "rag_service"},
        )

    # ── 4b. Start background ingest worker ───────────────────────
    if _rag_service.is_ready:
        try:
            await ingest_worker.start(_rag_service)
        except Exception:
            logger.warning(
                "Background ingest worker failed to start.",
                exc_info=True,
            )
            await _write_system_log(
                level="WARNING",
                event_type="ingest_worker_start_failed",
                message="Background ingest worker failed to start.",
                context={"component": "background_ingest"},
            )

    # ── 5. Run startup checks (DB, llama-server, RAG) ────────────────
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if _rag_service.is_ready and llm_client.supports_embeddings:
                classifier = ClassificationService(_rag_service)
                embedded = await generate_missing_embeddings(db, classifier)
                if embedded > 0:
                    logger.info(
                        "Generated %d missing category embeddings at startup.",
                        embedded,
                    )
                    await db.commit()
            _startup_checks = await run_all_checks(db, _rag_service)
    except Exception:
        logger.warning("Startup checks failed to execute.", exc_info=True)
        await _write_system_log(
            level="WARNING",
            event_type="startup_checks_failed",
            message="Startup checks failed to execute.",
            context={"component": "startup_checks"},
        )

    startup_ok = all(r.ok for r in _startup_checks) if _startup_checks else False
    await _write_system_log(
        level="INFO" if startup_ok else "WARNING",
        event_type="app_startup",
        message="Application startup completed.",
        context={
            "version": settings.app_version,
            "rag_ready": _rag_service.is_ready,
            "llm_loaded": llm_client.is_ready,
            "cleaned_system_logs": cleaned_system_logs,
            "checks": {
                result.name: {"ok": result.ok, "detail": result.detail}
                for result in _startup_checks
            },
        },
    )

    # NOTE: Category seeding is done via PUT /api/settings/default-base-path,
    # which the Tauri frontend calls on launch.

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────
    await ingest_worker.stop()
    await llm_client.shutdown()
    await _write_system_log(
        level="INFO",
        event_type="app_shutdown",
        message="Application shutdown completed.",
        context={
            "version": settings.app_version,
            "rag_ready": _rag_service.is_ready,
        },
    )
    logger.info("👋  Shutting down %s", settings.app_name)


# ── App factory ──────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# Voyager
voyager_app = create_voyager(app)
app.mount("/voyager", voyager_app)

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
app.include_router(summary_router)
app.include_router(settings_router)
app.include_router(history_router)
app.include_router(search_router)


# ── Health check ─────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    checks = {r.name: {"ok": r.ok, "detail": r.detail} for r in _startup_checks}
    checks["FastAPI"] = {
        "ok": True,
        "detail": f"{settings.app_name} v{settings.app_version} is running",
    }
    all_ok = all(r.ok for r in _startup_checks) if _startup_checks else False
    return {
        "status": "ok" if all_ok else "degraded",
        "version": settings.app_version,
        "services": checks,
    }
