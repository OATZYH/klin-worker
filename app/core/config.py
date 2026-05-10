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
import os
import sys
import tomllib
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


def _resolve_app_version() -> str:
    """Resolve app version from env/version file/pyproject in that order."""
    env_version = os.environ.get("KLIN_APP_VERSION", "").strip()
    if env_version:
        return env_version

    candidate_files: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate_files.append(Path(meipass) / "VERSION")
        candidate_files.append(Path(sys.executable).resolve().parent / "VERSION")

    project_root = Path(__file__).resolve().parent.parent.parent
    candidate_files.append(project_root / "VERSION")

    for version_file in candidate_files:
        try:
            if version_file.exists():
                value = version_file.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue

    pyproject_path = project_root / "pyproject.toml"
    try:
        if pyproject_path.exists():
            parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            version = str(parsed.get("project", {}).get("version", "")).strip()
            if version:
                return version
    except (OSError, tomllib.TOMLDecodeError):
        pass

    return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "klin-worker"
    app_version: str = _resolve_app_version()
    debug: bool = False
    app_environment: str = "development"

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000

    # ── CORS (Tauri dev mode) ────────────────────────────────────────────
    cors_origins: list[str] = [
        "http://localhost:1420",  # Tauri dev default
        "http://localhost:5173",  # Vite fallback
        "tauri://localhost",  # Tauri production
        "http://tauri.localhost",  # Tauri production (http scheme)
        "https://tauri.localhost",  # Tauri production (https scheme)
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

    # ── llama-server / client defaults ─────────────────────────────────
    llama_server_url: str = "http://127.0.0.1:8080/"
    llama_embedding_server_url: str = "http://127.0.0.1:8081/"
    embedding_dim_size: int = 768  # must match model served by llama-server
    llm_input_max_chars: int = 24000 # accounts for tokenization overhead, varies by model and tokenizer
    llm_output_max_tokens: int = 4096
    rag_embedding_input_max_tokens: int = 2048  # must match --ctx-size of embedding llama-server

    # ── Summary service tuning ──────────────────────────────────────────
    summary_retrieval_top_k: int = 2
    summary_output_max_tokens: int = 2048

    # ── Rename service tuning ───────────────────────────────────────────
    rename_output_max_tokens: int = 96

    # ── RAG service tuning ──────────────────────────────────────────────
    rag_output_max_tokens: int = 512
    docling_parser_max_workers: int = 2
    docling_fast_do_ocr: bool = False
    docling_fast_do_table_structure: bool = False
    docling_rich_do_ocr: bool = True
    docling_rich_do_table_structure: bool = True

    # ── Classification ───────────────────────────────────────────────────
    similarity_threshold: float = 0.85
    classification_top_k: int = 5  # max categories returned per file

    # ── Background Ingestion Queue (future) ──────────────────────────────
    max_queue_size: int = 1000
    worker_concurrency: int = 2

    # ── System Logging ───────────────────────────────────────────────────
    system_log_retention_days: int = 30
    cleanup_system_logs_on_startup: bool = True

    # ── Langfuse Tracing ─────────────────────────────────────────────────
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"
    langfuse_debug: bool = False
    langfuse_sample_rate: float = 1.0
    langfuse_max_payload_chars: int = 4000

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
        "extra": "ignore",
    }

    # ── Resolved accessors ─────────────────────────────────────────────

    @property
    def embedding_dim(self) -> int:
        return self.embedding_dim_size

    @property
    def langfuse_environment(self) -> str:
        return self.app_environment or ("development" if self.debug else "production")


# Singleton – import this everywhere
settings = Settings()
