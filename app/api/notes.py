"""Notes API router."""

from pathlib import Path
from time import perf_counter

from fastapi import APIRouter

from app.models.request import NotesSummarizeRequest
from app.models.response import NotesSummarizeResponse

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.post("/summarize", response_model=NotesSummarizeResponse)
async def summarize_notes(body: NotesSummarizeRequest) -> NotesSummarizeResponse:
    """Return a deterministic mock summary from selected file paths."""

    started = perf_counter()
    file_names = [Path(path).name for path in body.filePaths]

    if not file_names:
        return NotesSummarizeResponse(summary="No files were selected.", suggested_title="Quick-Note", processing_time_ms=0)

    summary_parts = [
        "This note combines key points from the selected files.",
        f"Files included: {', '.join(file_names)}.",
        "Use this draft as a starting point and refine details before saving.",
    ]

    if body.context and body.context.strip():
        summary_parts.append(f"Additional context: {body.context.strip()}.")

    summary = " ".join(summary_parts)
    first_name = Path(file_names[0]).stem.replace("_", " ").strip() or "Quick Note"
    suggested_title = f"Summary - {first_name}"[:120]

    elapsed_ms = int((perf_counter() - started) * 1000)

    return NotesSummarizeResponse(
        summary=summary,
        suggested_title=suggested_title,
        processing_time_ms=elapsed_ms,
    )
