from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── Async DB fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine with full schema. Disposed after each test."""
    import app.db.models  # noqa: F401  — register models

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Async session bound to the in-memory engine."""
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def seeded_db(async_session: AsyncSession) -> AsyncSession:
    """Session preloaded with one Category, one File, and one FileAnalysis row."""
    from app.db.models import Category, File, FileAnalysis

    category = Category(
        id="cat-1",
        name="Finance",
        description="Invoices and receipts",
        color="#22c55e",
        icon="Receipt",
        destination_path="/tmp/Finance",
        is_active=True,
        is_default=False,
    )
    file_row = File(
        id="file-1",
        original_path="/tmp/source/doc.pdf",
        current_path="/tmp/source/doc.pdf",
        hash="abc123",
        size=1024,
        extension=".pdf",
    )
    analysis = FileAnalysis(
        id="analysis-1",
        file_id=file_row.id,
        summary="Sample summary",
        suggested_names='["doc.pdf"]',
    )
    async_session.add_all([category, file_row, analysis])
    await async_session.flush()
    return async_session


# ── FastAPI client fixtures ──────────────────────────────────────────────


@pytest.fixture
def fastapi_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """
    Import the FastAPI app with heavy startup hooks neutralised.

    The lifespan still runs when the TestClient enters the context manager,
    so we patch the entry points before importing `app.main`.
    """
    import app.core.config as config_module

    monkeypatch.setattr(
        config_module.settings,
        "database_path",
        str(tmp_path / "klin.db"),
        raising=False,
    )
    monkeypatch.setattr(
        config_module.settings,
        "cleanup_system_logs_on_startup",
        False,
        raising=False,
    )

    # Stub out heavy startup before the app module captures references.
    import app.observability.tracing as tracing_module
    import app.services.ai.llm_client as llm_module
    import app.services.ai.rag_service as rag_module
    import app.services.organize.background_ingest as ingest_module
    import app.db.migrations as migrations_module

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(llm_module.llm_client, "startup", _noop_async)
    monkeypatch.setattr(llm_module.llm_client, "shutdown", _noop_async)
    monkeypatch.setattr(rag_module.RagService, "setup", _noop_async)
    monkeypatch.setattr(ingest_module.BackgroundIngestWorker, "start", _noop_async)
    monkeypatch.setattr(ingest_module.BackgroundIngestWorker, "stop", _noop_async)
    monkeypatch.setattr(migrations_module, "run_migrations", _noop_async)
    # Keep tracing off so the lifespan doesn't poison the global langfuse
    # client (which would leak trace IDs into subsequent tests).
    monkeypatch.setattr(tracing_module, "init_langfuse", lambda: None)
    monkeypatch.setattr(tracing_module, "flush_langfuse", lambda: None)
    monkeypatch.setattr(
        config_module.settings, "langfuse_enabled", False, raising=False
    )

    import importlib

    if "app.main" in sys.modules:
        del sys.modules["app.main"]
    main_module = importlib.import_module("app.main")

    # Force the app's DB engine onto the tmp_path database so the lifespan's
    # system-log writes don't trip on a missing schema.
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    new_engine = _create(
        f"sqlite+aiosqlite:///{tmp_path / 'klin.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr("app.db.session.engine", new_engine, raising=True)
    monkeypatch.setattr("app.main.engine", new_engine, raising=True)

    # Create the schema in the tmp DB so lifespan writes work.
    import asyncio

    async def _make_schema() -> None:
        import app.db.models  # noqa: F401
        async with new_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    asyncio.run(_make_schema())

    return main_module.app


@pytest.fixture
def test_client(fastapi_app: Any):
    """Synchronous FastAPI TestClient with neutered lifespan."""
    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        yield client
