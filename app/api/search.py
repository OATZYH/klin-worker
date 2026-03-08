"""
Search API router.

Mock file search endpoint used by the desktop AppShell search bar.
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.models.request import FileSearchRequest
from app.models.response import FileSearchResponse, FileSearchResultItem

router = APIRouter(prefix="/api/search", tags=["search"])


MOCK_SEARCH_RESULTS: list[FileSearchResultItem] = [
    FileSearchResultItem(
        id="search-1",
        file_name="Invoice-2026-02.pdf",
        file_type="pdf",
        size_bytes=245_760,
        folder="C:/Users/supak/Documents/KLIN/Finance",
        last_edited=datetime(2026, 3, 2, 8, 30, tzinfo=timezone.utc),
        path="C:/Users/supak/Documents/KLIN/Finance/Invoice-2026-02.pdf",
    ),
    FileSearchResultItem(
        id="search-2",
        file_name="Project-Alpha-Meeting-Notes.txt",
        file_type="txt",
        size_bytes=12_504,
        folder="C:/Users/supak/Documents/KLIN/Work/Meetings",
        last_edited=datetime(2026, 3, 6, 10, 15, tzinfo=timezone.utc),
        path="C:/Users/supak/Documents/KLIN/Work/Meetings/Project-Alpha-Meeting-Notes.txt",
    ),
    FileSearchResultItem(
        id="search-3",
        file_name="Travel-Budget-March.xlsx",
        file_type="xlsx",
        size_bytes=84_120,
        folder="C:/Users/supak/Documents/KLIN/Personal/Budgets",
        last_edited=datetime(2026, 3, 1, 4, 45, tzinfo=timezone.utc),
        path="C:/Users/supak/Documents/KLIN/Personal/Budgets/Travel-Budget-March.xlsx",
    ),
    FileSearchResultItem(
        id="search-4",
        file_name="Roadmap-Q2-2026.docx",
        file_type="docx",
        size_bytes=65_932,
        folder="C:/Users/supak/Documents/KLIN/Work/Planning",
        last_edited=datetime(2026, 3, 7, 2, 5, tzinfo=timezone.utc),
        path="C:/Users/supak/Documents/KLIN/Work/Planning/Roadmap-Q2-2026.docx",
    ),
]


@router.post("/files", response_model=FileSearchResponse)
async def search_files(body: FileSearchRequest) -> FileSearchResponse:
    """Return mock file rows matching a simple text query."""

    query = body.query.strip().lower()
    if not query:
        return FileSearchResponse(results=[])

    filtered = [
        item
        for item in MOCK_SEARCH_RESULTS
        if query in item.file_name.lower()
        or query in item.file_type.lower()
        or query in item.folder.lower()
        or query in item.path.lower()
    ]

    return FileSearchResponse(results=filtered)