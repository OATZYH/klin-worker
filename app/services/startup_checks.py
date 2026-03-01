"""
Startup Checks — verify all services are operational after boot.

Runs diagnostic checks for:
  • Database connectivity (SQLite read/write)
  • llama-cpp-python model (in-process GGUF loaded)
  • RAG system readiness (RAG-Anything initialised)

Each check returns a `CheckResult` with status + detail message.
`run_all_checks()` runs them all and logs a summary table.
"""

import logging
from dataclasses import dataclass

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import Category
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)


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
        return CheckResult(name=name, ok=False, detail=str(exc))


async def check_llamacpp() -> CheckResult:
    """
    Verify the llama-cpp-python model is loaded in-process.

    Checks that the `LlmClient` singleton has a loaded model
    and reports the model path and embedding dimension.
    """
    name = "llama-cpp-python"

    try:
        if llm_client.is_loaded:
            return CheckResult(
                name=name,
                ok=True,
                detail=f"Model loaded — {settings.model_path}",
            )

        return CheckResult(
            name=name,
            ok=False,
            detail="Model not loaded. LlmClient.startup() may have failed.",
        )

    except Exception as exc:
        return CheckResult(name=name, ok=False, detail=str(exc))


async def check_rag(rag_service) -> CheckResult:  # type: ignore[type-arg]
    """
    Verify RAG-Anything is initialised and ready.

    Simply reads the `is_ready` flag set by `RagService.setup()`.
    """
    name = "RAG-Anything"
    try:
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
        await check_llamacpp(),
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
