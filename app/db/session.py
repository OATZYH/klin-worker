"""
SQLite database session management.

Provides:
  • Async engine + session factory
  • `get_db()` — FastAPI dependency for request-scoped sessions
  • `init_db()` — create all tables on startup
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Engine (singleton) ───────────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False},  # SQLite needs this
)


# ── Dependency ───────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a scoped async session."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Bootstrap ────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables if they don't exist (called at startup)."""
    # Import models so SQLModel.metadata registers them
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("SQLite database initialised at %s", settings.database_url)
