"""
Database migration helpers.

Runs Alembic migrations programmatically at application startup.
Alembic manages all schema evolution via versioned migration scripts
stored in `alembic/versions/`.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

# Locate alembic.ini relative to the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


def _get_alembic_config() -> Config:
    """Build an Alembic Config object pointing at our alembic.ini."""
    cfg = Config(str(_ALEMBIC_INI))
    # Override script_location so it works regardless of cwd
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    return cfg


async def run_migrations() -> None:
    """
    Run pending Alembic migrations (upgrade to head).

    Called at application startup via the FastAPI lifespan hook.
    """
    logger.info("Running Alembic migrations …")
    cfg = _get_alembic_config()
    command.upgrade(cfg, "head")
    logger.info("Migrations complete.")
