"""
Application configuration.

Centralized settings using pydantic-settings for environment variable support.
Covers: SQLite, llama.cpp, RAG, security, classification, and future features.

Storage path resolution:
  - Dev  (plain Python)  → .storage/  inside the project directory
  - Prod (PyInstaller)   → KLIN_APP_DATA_DIR env var injected by Tauri sidecar
                           Falls back to ~/.klin if the var is not set.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def _resolve_default_storage_dir() -> Path:
    """
    Return the base storage directory depending on the runtime context.

    - PyInstaller bundle  → use KLIN_APP_DATA_DIR (set by Tauri before spawning
                            the sidecar).  Falls back to ~/.klin so the binary
                            is still usable standalone.
    - Plain Python (dev)  → .storage/ next to the project root (this file lives
                            at app/core/config.py, so go up two levels).
    """
    if getattr(sys, "frozen", False):
        # Running inside a PyInstaller bundle
        import os
        app_data = os.environ.get("KLIN_APP_DATA_DIR")
        if app_data:
            return Path(app_data)
        # Fallback: stable user-level location when env injection is unavailable.
        return Path.home() / ".klin"

    # Dev: .storage/ at the project root
    _project_root = Path(__file__).resolve().parent.parent.parent
    return _project_root / ".storage"


_KLIN_DIR = _resolve_default_storage_dir()


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "klin-worker"
    app_version: str = "0.2.0"
    debug: bool = False

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000

    # ── CORS (Tauri dev mode) ────────────────────────────────────────────
    cors_origins: list[str] = [
        "http://localhost:1420",   # Tauri dev default
        "http://localhost:5173",   # Vite fallback
        "tauri://localhost",       # Tauri production
    ]

    # ── Voyager UI ───────────────────────────────────────────────────────
    voyager_enabled: bool = True
    voyager_mount_path: str = "/voyager"
    voyager_swagger_url: str | None = None
    voyager_module_prefix: str | None = None
    voyager_online_repo_url: str | None = None
    voyager_initial_page_policy: str = "first"
    voyager_enable_pydantic_resolve_meta: bool = False
    voyager_module_colors: dict[str, str] = {}

    # ── SQLite ───────────────────────────────────────────────────────────
    database_path: str = str(_KLIN_DIR / "klin.db")

    @property
    def database_url(self) -> str:
        """Async SQLite connection string for SQLAlchemy."""
        return f"sqlite+aiosqlite:///{self.database_path}"

    # ── File System Security ─────────────────────────────────────────────
    allowed_roots: list[str] = []  # populated at runtime or via env
    blocked_directories: list[str] = [
        "/System",
        "/Library",
        "/usr",
        "/bin",
        "/sbin",
        "/private",
        # Windows equivalents handled at validation time
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    ]

    # ── RAG-Anything ─────────────────────────────────────────────────────
    rag_working_dir: str = str(_KLIN_DIR / "rag_storage")

    # ── llama-server (out-of-process, managed by Tauri) ────────────────
    llama_server_url: str = "http://127.0.0.1:8080/"
    embedding_dim_size: int = 2048        # must match model served by llama-server
    max_token_limit: int = 4096           # used for RAG chunking

    # ── Organize pipeline tuning ────────────────────────────────────────
    summary_rag_top_k: int = 2
    summary_context_max_chars: int = 1200
    summary_max_tokens: int = 64
    rename_max_tokens: int = 48
    rag_llm_max_tokens: int = 256

    # ── Classification ───────────────────────────────────────────────────
    similarity_threshold: float = 0.85
    classification_top_k: int = 5  # max categories returned per file

    # ── Background Ingestion Queue (future) ──────────────────────────────
    max_queue_size: int = 1000
    worker_concurrency: int = 2

    # ── System Logging ───────────────────────────────────────────────────
    system_log_retention_days: int = 30
    cleanup_system_logs_on_startup: bool = True

    # ── File Watcher (future) ────────────────────────────────────────────
    watch_directories: list[str] = []
    watch_poll_interval_seconds: int = 5

    # ── Embedding Cache (future) ─────────────────────────────────────────
    embedding_cache_dir: Optional[str] = None
    embedding_cache_max_items: int = 10_000

    model_config = {
        "env_prefix": "KLIN_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    # ── Resolved accessors ─────────────────────────────────────────────

    @property
    def embedding_dim(self) -> int:
        return self.embedding_dim_size

    @property
    def max_token_size(self) -> int:
        return self.max_token_limit


# Singleton – import this everywhere
settings = Settings()
