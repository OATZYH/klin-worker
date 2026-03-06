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

    # ── llama-cpp-python (in-process GGUF model) ───────────────────────
    llamacpp_model_path: str = "models/Qwen2.5-VL-3B-Instruct-IQ4_XS.gguf"
    llamacpp_n_ctx: int = 4096
    llamacpp_n_gpu_layers: int = -1           # -1 = offload all layers to GPU
    llamacpp_n_batch: int = 512
    llamacpp_n_threads: Optional[int] = None  # None = auto-detect
    llamacpp_embedding_dim: int = 2048        # model-native embedding dim
    llamacpp_max_token_size: int = 4096
    llamacpp_verbose: bool = False

    # ── Organize pipeline tuning ────────────────────────────────────────
    summary_rag_top_k: int = 2
    summary_context_max_chars: int = 1200
    summary_max_tokens: int = 192
    rename_max_tokens: int = 48

    # ── Classification ───────────────────────────────────────────────────
    similarity_threshold: float = 0.85
    classification_top_k: int = 5  # max categories returned per file

    # ── Background Ingestion Queue (future) ──────────────────────────────
    max_queue_size: int = 1000
    worker_concurrency: int = 2

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
    def model_path(self) -> str:
        return self.llamacpp_model_path

    @property
    def embedding_dim(self) -> int:
        return self.llamacpp_embedding_dim

    @property
    def max_token_size(self) -> int:
        return self.llamacpp_max_token_size


# Singleton – import this everywhere
settings = Settings()
