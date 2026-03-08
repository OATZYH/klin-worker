"""
Database migration helpers.

Runs Alembic migrations programmatically at application startup.
Alembic manages all schema evolution via versioned migration scripts
stored in `alembic/versions/`.
"""

import logging
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

def _resolve_project_root() -> Path:
    """Resolve project root in both source and frozen runtime modes."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled_root = Path(meipass)
            if (bundled_root / "alembic.ini").exists():
                return bundled_root

        exe_root = Path(sys.executable).resolve().parent
        if (exe_root / "alembic.ini").exists():
            return exe_root

        return exe_root

    return Path(__file__).resolve().parent.parent.parent


_PROJECT_ROOT = _resolve_project_root()
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
