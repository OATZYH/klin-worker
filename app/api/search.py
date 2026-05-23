"""Search API router."""

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.session import get_db
from app.models.request import FileSearchRequest
from app.models.response import FileSearchResponse
from app.observability.tracing import observe
from app.services.ai.rag_service import RagService
from app.services.organize.background_ingest import BackgroundIngestWorker
from app.services.search_service import search_files as search_files_service

router = APIRouter(prefix="/api/search", tags=["search"])


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_ingest_worker() -> BackgroundIngestWorker:
    from app.main import ingest_worker

    return ingest_worker


@router.post("/files", response_model=FileSearchResponse)
@observe(name="search.files", capture_input=False, capture_output=False)
async def search_files(
    body: FileSearchRequest,
    db: AsyncSession = Depends(get_db),
    rag: RagService = Depends(_get_rag),
    ingest: BackgroundIngestWorker = Depends(_get_ingest_worker),
) -> FileSearchResponse:
    """Search known files."""
    return await search_files_service(
        db=db,
        rag=rag,
        ingest=ingest,
        raw_query=body.query,
    )
