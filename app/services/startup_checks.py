"""
Startup Checks — verify all services are operational after boot.

Runs diagnostic checks for:
  • Database connectivity (SQLite read/write)
  • llama-server reachability (out-of-process LLM)
  • RAG system readiness (RAG-Anything initialised)

Each check returns a `CheckResult` with status + detail message.
`run_all_checks()` runs them all and logs a summary table.
"""

import logging
from dataclasses import dataclass

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.db.models import Category
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)


# ── Error classification ─────────────────────────────────────────────────


def _classify_db_error(exc: Exception) -> str:
    """
    Return a safe, user-facing error description based on exception type.

    Full details are logged separately — this never exposes SQL,
    column names, or internal paths to the API consumer.
    """
    err_str = str(exc).lower()

    # Schema mismatch (missing column / table)
    if "no such column" in err_str or "no such table" in err_str:
        return "schema_mismatch — run migrations to update the database."

    # DB file cannot be opened (permissions, missing dir, corrupt)
    if "unable to open database" in err_str:
        return "connection_failed — database file is not accessible."

    # Database is locked (another process holds the lock)
    if "database is locked" in err_str:
        return "database_locked — another process is using the database."

    # Read-only filesystem / permission denied
    if "readonly" in err_str or "permission denied" in err_str:
        return "permission_denied — cannot write to the database file."

    # Disk full
    if "disk" in err_str and "full" in err_str:
        return "disk_full — not enough disk space for database operations."

    # Corrupt database
    if "malformed" in err_str or "corrupt" in err_str:
        return "database_corrupt — the database file may be damaged."

    # Fallback: generic with the exception class name only
    return f"unexpected_error — {type(exc).__name__}"


@dataclass
class CheckResult:
    """Outcome of a single startup check."""

    name: str
    ok: bool
    detail: str


# ── Individual Checks ────────────────────────────────────────────────────


async def check_database(db: AsyncSession) -> CheckResult:
    """
    Verify SQLite is reachable and the schema exists.

    Queries the categories table to confirm both connectivity
    and that migrations have been applied.
    """
    name = "Database (SQLite)"
    try:
        result = await db.execute(select(Category).limit(1))
        # If we get here without error, the DB is reachable and the table exists
        row = result.scalar_one_or_none()
        count_detail = "connected, schema OK"
        if row is not None:
            count_detail = "connected, has data"
        return CheckResult(name=name, ok=True, detail=f"{settings.database_path} — {count_detail}")
    except Exception as exc:
        # Log full error for debugging, but expose only error type to the API
        logger.error("Database check failed: %s", exc, exc_info=True)
        detail = _classify_db_error(exc)
        return CheckResult(name=name, ok=False, detail=detail)


async def check_llm_server() -> CheckResult:
    """
    Verify the llama-server is reachable.

    Checks that the `LlmClient` singleton has an initialised HTTP client
    connected to the external llama-server.
    """
    name = "LLM Server"

    try:
        await llm_client.ensure_general_available()
    except AiCapabilityUnavailableError as exc:
        return CheckResult(name=name, ok=False, detail=exc.detail)
    except Exception as exc:
        return CheckResult(name=name, ok=False, detail=str(exc))

    detail = f"Connected — {settings.llama_server_url}"
    try:
        await llm_client.ensure_embedding_available(require_general_check=False)
    except AiCapabilityUnavailableError:
        detail = f"{detail} (embeddings unavailable)"

    return CheckResult(name=name, ok=True, detail=detail)


async def check_rag(rag_service) -> CheckResult:  # type: ignore[type-arg]
    """
    Verify RAG-Anything is initialised and ready.

    Simply reads the `is_ready` flag set by `RagService.setup()`.
    """
    name = "RAG-Anything"
    try:
        rag_service.ensure_ready()
        if rag_service.is_ready:
            return CheckResult(
                name=name,
                ok=True,
                detail=f"Ready — storage: {settings.rag_working_dir}",
            )
        return CheckResult(
            name=name,
            ok=False,
            detail="Not initialised. RAG-Anything setup() may have failed.",
        )
    except AiCapabilityUnavailableError as exc:
        return CheckResult(name=name, ok=False, detail=exc.detail)
    except Exception as exc:
        return CheckResult(name=name, ok=False, detail=str(exc))


# ── Run All ──────────────────────────────────────────────────────────────


async def run_all_checks(db: AsyncSession, rag_service) -> list[CheckResult]:  # type: ignore[type-arg]
    """
    Execute all startup checks and log a summary table.

    Returns the list of results so the caller can react to failures.
    """
    results = [
        await check_database(db),
        await check_llm_server(),
        await check_rag(rag_service),
    ]

    # ── Pretty log output ────────────────────────────────────────────
    header = "\n┌──────────────────────────────────────────────────────────┐"
    footer = "└──────────────────────────────────────────────────────────┘"

    lines = [header, "│  🔍  Startup Service Checks                              │"]
    lines.append("├──────────────────────────────────────────────────────────┤")

    for r in results:
        icon = "✅" if r.ok else "❌"
        status = "OK" if r.ok else "FAIL"
        lines.append(f"│  {icon} {r.name:<22} [{status:<4}]  {r.detail[:40]:<40} │")  # noqa: E501

    lines.append(footer)
    summary = "\n".join(lines)

    all_ok = all(r.ok for r in results)
    if all_ok:
        logger.info(summary)
    else:
        logger.warning(summary)
        failed = [r for r in results if not r.ok]
        for f in failed:
            logger.warning("  ⚠️  %s: %s", f.name, f.detail)

    return results
