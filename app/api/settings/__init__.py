"""
Settings API package.

Groups all configuration-related routers under /api/settings:
  • /api/settings/categories       — canonical CRUD + batch categories API
  • /api/settings/default-base-path — first-run setup + later base-folder updates

Import `router` from this package to register all settings routes at once.
"""

from fastapi import APIRouter

from app.api.settings.base_path import router as base_path_router
from app.api.settings.categories import router as categories_router

router = APIRouter(prefix="/api/settings", tags=["settings"])

router.include_router(categories_router)
router.include_router(base_path_router)
