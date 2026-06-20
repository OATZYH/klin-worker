"""
Settings API package.

Groups all configuration-related routers under /api/settings:
  • /api/settings/categories       — canonical CRUD + batch categories API
  • /api/settings/default-base-path — first-run setup + later base-folder updates
  • /api/settings/onboarding       — first-run onboarding + seed status
  • /api/settings/auto-organize    — watcher folders + frequency settings
  • /api/settings/locks            — locked file/folder paths for AI safety

Import `router` from this package to register all settings routes at once.
"""

from fastapi import APIRouter

from app.api.settings.auto_organize import router as auto_organize_router
from app.api.settings.base_path import router as base_path_router
from app.api.settings.categories import router as categories_router
from app.api.settings.locks import router as locks_router

router = APIRouter(prefix="/api/settings", tags=["settings"])

router.include_router(categories_router)
router.include_router(base_path_router)
router.include_router(auto_organize_router)
router.include_router(locks_router)
