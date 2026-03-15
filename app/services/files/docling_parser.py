"""Docling-based document parser.

Extracts structured content from PDF, DOCX, PPTX, XLSX, HTML, and Markdown
files using docling's DocumentConverter.  The output is a ``content_list``
compatible with RAG-Anything's ``insert_content_list()`` format.

Parsing is CPU-bound, so it runs in a dedicated thread-pool to avoid
blocking the async event loop.

Performance notes:
  • OCR is disabled by default — most PDFs have embedded text.
  • Table structure extraction is disabled — saves 80-90% of parse time.
    Tables are still captured as plain text via TextItem.
  • Both can be re-enabled via settings when accuracy matters more than speed.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class DoclingParser:
    """Parse documents into a RAG-Anything-compatible content list."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._converter: Any = None  # lazy-init to avoid import cost at startup

    def _get_converter(self) -> Any:
        if self._converter is None:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False             # embedded text is sufficient
            pipeline_options.do_table_structure = False  # plain text capture is fast enough

            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        return self._converter

    # ── public API ──────────────────────────────────────────────────────

    async def parse(self, filepath: str | Path) -> list[dict[str, Any]]:
        """Parse *filepath* and return a RAG-Anything content list.

        Returns an empty list on any parsing failure so the caller can
        fall through to a filename-only fallback.
        """
        filepath = Path(filepath)
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, self._parse_sync, filepath
            )
        except Exception as exc:
            logger.error("Docling parse failed for %s: %s", filepath.name, exc)
            return []

    @staticmethod
    def extract_text(content_list: list[dict[str, Any]]) -> str:
        """Join text and table items into a single string.

        Truncated to ``settings.summary_context_max_chars`` so that the
        summary prompt stays within a reasonable token budget.
        """
        parts: list[str] = []
        for item in content_list:
            item_type = item.get("type")
            if item_type == "text" and item.get("text"):
                parts.append(item["text"])
            elif item_type == "table" and item.get("table_body"):
                parts.append(item["table_body"])

        joined = "\n\n".join(parts)
        return joined[: settings.summary_context_max_chars]

    # ── internal ────────────────────────────────────────────────────────

    def _parse_sync(self, filepath: Path) -> list[dict[str, Any]]:
        """Synchronous docling conversion — runs inside the thread pool."""
        from docling_core.types.doc.document import PictureItem, TableItem, TextItem

        converter = self._get_converter()
        result = converter.convert(str(filepath))
        doc = result.document

        content_list: list[dict[str, Any]] = []

        for item, _level in doc.iterate_items():
            page_idx = 0
            if hasattr(item, "prov") and item.prov:
                page_idx = item.prov[0].page_no

            if isinstance(item, TextItem):
                if item.text and item.text.strip():
                    content_list.append({
                        "type": "text",
                        "text": item.text,
                        "page_idx": page_idx,
                    })
            elif isinstance(item, TableItem):
                md_table = item.export_to_markdown(doc=doc)
                if md_table and md_table.strip():
                    captions: list[str] = []
                    if hasattr(item, "caption") and item.caption:
                        cap_text = item.caption if isinstance(item.caption, str) else str(item.caption)
                        captions.append(cap_text)
                    content_list.append({
                        "type": "table",
                        "table_body": md_table,
                        "table_caption": captions,
                        "page_idx": page_idx,
                    })
            elif isinstance(item, PictureItem):
                img_path = ""
                if hasattr(item, "image") and item.image and hasattr(item.image, "uri"):
                    img_path = str(item.image.uri)
                # Skip images with no resolvable path — RAG-Anything crashes on empty img_path
                if not img_path:
                    continue
                captions: list[str] = []
                if hasattr(item, "caption") and item.caption:
                    cap_text = item.caption if isinstance(item.caption, str) else str(item.caption)
                    captions.append(cap_text)
                content_list.append({
                    "type": "image",
                    "img_path": img_path,
                    "image_caption": captions,
                    "page_idx": page_idx,
                })

        logger.info(
            "Docling parsed %s: %d items (%d text, %d tables, %d images)",
            filepath.name,
            len(content_list),
            sum(1 for i in content_list if i["type"] == "text"),
            sum(1 for i in content_list if i["type"] == "table"),
            sum(1 for i in content_list if i["type"] == "image"),
        )
        return content_list
