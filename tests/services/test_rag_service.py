from __future__ import annotations

import asyncio
import sys
import types

from app.core.config import settings
from app.services.ai import rag_service as rag_service_module
from app.services.ai.rag_service import RagService
from app.services.ai.rag_service import configure_lightrag_for_file_search
from app.services.ai.rag_service import ensure_rag_doc_status_compatible


class FakeDocStatusStorage:
    def __init__(self, data: dict[str, dict]) -> None:
        self._data = data
        self._storage_lock = asyncio.Lock()
        self.upsert_calls: list[dict[str, dict]] = []

    async def upsert(self, data: dict[str, dict]) -> None:
        self.upsert_calls.append(data)
        self._data.update(data)


class FakeLightRAG:
    def __init__(self, doc_status: FakeDocStatusStorage) -> None:
        self.doc_status = doc_status


class FakeRagEngine:
    def __init__(self, doc_status: FakeDocStatusStorage) -> None:
        self.lightrag = FakeLightRAG(doc_status)
        self.ensure_calls = 0
        self.extract_calls = 0

    async def _ensure_lightrag_initialized(self) -> dict[str, bool]:
        self.ensure_calls += 1
        return {"success": True}

    async def _batch_extract_entities_lightrag_style_type_aware(self, chunks: dict) -> list:
        self.extract_calls += 1
        return [chunks]


def test_ensure_rag_doc_status_compatible_drops_unknown_fields() -> None:
    async def run() -> None:
        doc_status = FakeDocStatusStorage(
            {
                "doc-1": {
                    "content_summary": "resume",
                    "content_length": 1200,
                    "file_path": "/tmp/resume.pdf",
                    "status": "processed",
                    "created_at": "2026-04-22T00:00:00+00:00",
                    "updated_at": "2026-04-22T00:00:00+00:00",
                    "multimodal_processed": True,
                }
            }
        )
        rag_engine = FakeRagEngine(doc_status)

        changed = await ensure_rag_doc_status_compatible(rag_engine)

        assert changed == 1
        assert rag_engine.ensure_calls == 1
        assert len(doc_status.upsert_calls) == 1
        sanitized = doc_status.upsert_calls[0]["doc-1"]
        assert "multimodal_processed" not in sanitized
        assert sanitized["file_path"] == "/tmp/resume.pdf"
        assert sanitized["metadata"] == {}
        assert sanitized["error_msg"] is None
        assert sanitized["chunks_list"] == []

    asyncio.run(run())


def test_ensure_rag_doc_status_compatible_noops_when_storage_is_clean() -> None:
    async def run() -> None:
        doc_status = FakeDocStatusStorage(
            {
                "doc-1": {
                    "content_summary": "resume",
                    "content_length": 1200,
                    "file_path": "/tmp/resume.pdf",
                    "status": "processed",
                    "created_at": "2026-04-22T00:00:00+00:00",
                    "updated_at": "2026-04-22T00:00:00+00:00",
                    "metadata": {},
                    "error_msg": None,
                    "chunks_list": [],
                }
            }
        )
        rag_engine = FakeRagEngine(doc_status)

        changed = await ensure_rag_doc_status_compatible(rag_engine)

        assert changed == 0
        assert rag_engine.ensure_calls == 1
        assert doc_status.upsert_calls == []

    asyncio.run(run())


def test_rag_setup_passes_embedding_budget_to_lightrag(monkeypatch) -> None:
    async def run() -> None:
        original_chunk_size = settings.rag_chunk_token_size
        original_dim = settings.embedding_dim_size
        original_image_processing = settings.rag_enable_image_processing
        original_table_processing = settings.rag_enable_table_processing
        original_equation_processing = settings.rag_enable_equation_processing
        original_content_format = settings.rag_content_format
        original_embedding_timeout = settings.rag_embedding_timeout_seconds
        settings.rag_chunk_token_size = 384
        settings.embedding_dim_size = 1024
        settings.rag_enable_image_processing = True
        settings.rag_enable_table_processing = False
        settings.rag_enable_equation_processing = False
        settings.rag_content_format = "auto"
        settings.rag_embedding_timeout_seconds = 300
        captured: dict[str, object] = {}

        class FakeEmbeddingFunc:
            def __init__(self, **kwargs) -> None:
                captured["embedding_func_kwargs"] = kwargs

        class FakeConfig:
            def __init__(self, **kwargs) -> None:
                captured["config_kwargs"] = kwargs

        class FakeRAGAnything:
            def __init__(self, **kwargs) -> None:
                captured["rag_kwargs"] = kwargs

        fake_lightrag = types.ModuleType("lightrag")
        fake_lightrag_utils = types.ModuleType("lightrag.utils")
        fake_lightrag_utils.EmbeddingFunc = FakeEmbeddingFunc
        fake_raganything = types.ModuleType("raganything")
        fake_raganything.RAGAnything = FakeRAGAnything
        fake_raganything_config = types.ModuleType("raganything.config")
        fake_raganything_config.RAGAnythingConfig = FakeConfig

        monkeypatch.setitem(sys.modules, "lightrag", fake_lightrag)
        monkeypatch.setitem(sys.modules, "lightrag.utils", fake_lightrag_utils)
        monkeypatch.setitem(sys.modules, "raganything", fake_raganything)
        monkeypatch.setitem(sys.modules, "raganything.config", fake_raganything_config)

        async def fake_aembed(texts: list[str]) -> list[list[float]]:
            return [[0.0] * settings.embedding_dim_size for _ in texts]

        async def fake_achat(*args, **kwargs) -> str:
            return "ok"

        async def fake_achat_with_vision(*args, **kwargs) -> str:
            return "ok"

        monkeypatch.setattr(rag_service_module.llm_client, "aembed", fake_aembed)
        monkeypatch.setattr(rag_service_module.llm_client, "achat", fake_achat)
        monkeypatch.setattr(rag_service_module.llm_client, "achat_with_vision", fake_achat_with_vision)

        try:
            service = RagService()
            await service.setup()
        finally:
            settings.rag_chunk_token_size = original_chunk_size
            settings.embedding_dim_size = original_dim
            settings.rag_enable_image_processing = original_image_processing
            settings.rag_enable_table_processing = original_table_processing
            settings.rag_enable_equation_processing = original_equation_processing
            settings.rag_content_format = original_content_format
            settings.rag_embedding_timeout_seconds = original_embedding_timeout

        embedding_kwargs = captured["embedding_func_kwargs"]
        config_kwargs = captured["config_kwargs"]
        rag_kwargs = captured["rag_kwargs"]
        lightrag_kwargs = rag_kwargs["lightrag_kwargs"]  # type: ignore[index]
        assert config_kwargs["parser"] == "docling"  # type: ignore[index]
        assert config_kwargs["content_format"] == "auto"  # type: ignore[index]
        assert config_kwargs["use_full_path"] is True  # type: ignore[index]
        assert config_kwargs["enable_image_processing"] is True  # type: ignore[index]
        assert config_kwargs["enable_table_processing"] is False  # type: ignore[index]
        assert config_kwargs["enable_equation_processing"] is False  # type: ignore[index]
        assert embedding_kwargs["max_token_size"] == 384  # type: ignore[index]
        assert embedding_kwargs["embedding_dim"] == 1024  # type: ignore[index]
        assert lightrag_kwargs["chunk_token_size"] == 384
        assert lightrag_kwargs["chunk_overlap_token_size"] == 64
        assert lightrag_kwargs["embedding_func_max_async"] == 1
        assert lightrag_kwargs["embedding_batch_num"] == 1
        assert lightrag_kwargs["max_parallel_insert"] == 1
        assert lightrag_kwargs["default_embedding_timeout"] == 300

    asyncio.run(run())


def test_configure_lightrag_for_file_search_disables_kg_extraction(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(settings, "rag_enable_kg_extraction", False)
        doc_status = FakeDocStatusStorage({})
        rag_engine = FakeRagEngine(doc_status)

        lightrag = await configure_lightrag_for_file_search(rag_engine)

        assert lightrag is rag_engine.lightrag
        assert rag_engine.ensure_calls == 1
        assert rag_engine.lightrag._klin_kg_extraction_disabled is True
        assert await rag_engine.lightrag._process_extract_entities({}) == []
        assert rag_engine._klin_multimodal_kg_extraction_disabled is True
        assert await rag_engine._batch_extract_entities_lightrag_style_type_aware({}) == []
        assert rag_engine.extract_calls == 0

    asyncio.run(run())
