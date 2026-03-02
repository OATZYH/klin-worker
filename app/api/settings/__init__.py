"""
Settings API package.

Groups all configuration-related routers under /api/settings:
  • /api/settings/categories       — CRUD for user-defined categories
  • /api/settings/default-base-path — change base folder later
  • /api/settings/initial-base-path — Tauri startup: set base path + seed

Import `router` from this package to register all settings routes at once.
"""

from fastapi import APIRouter

from app.api.settings.base_path import router as base_path_router
from app.api.settings.categories import router as categories_router
from app.api.settings.init_base_path import router as init_base_path_router

router = APIRouter(prefix="/api/settings", tags=["settings"])

router.include_router(categories_router)
router.include_router(base_path_router)
router.include_router(init_base_path_router)
