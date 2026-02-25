"""
Application configuration.

Centralized settings using pydantic-settings for environment variable support.
Ready to scale with similarity thresholds, background queue config, and watcher settings.
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # ── App ──────────────────────────────────────────────────────────────
    app_name: str = "klin-worker"
    app_version: str = "0.1.0"
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
    rag_working_dir: str = str(Path.home() / ".klin" / "rag_storage")

    # ── Ollama (local LLM backend) ───────────────────────────────────────
    ollama_host: str = "http://localhost:11434"
    ollama_llm_model: str = "gemma3:1b"
    ollama_embed_model: str = "embeddinggemma:300m"
    ollama_embedding_dim: int = 768
    ollama_max_token_size: int = 2048
    ollama_timeout: int = 300

    # ── Semantic Duplicate Detection (future) ────────────────────────────
    similarity_threshold: float = 0.85
    duplicate_check_enabled: bool = True

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


# Singleton – import this everywhere
settings = Settings()
