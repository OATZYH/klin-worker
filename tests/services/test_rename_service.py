from __future__ import annotations

import asyncio

from app.services.ai.llm_client import llm_client
from app.services.organize.rename_service import RenameService


def test_suggest_names_filters_original_and_normalizes_output() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        return "sarun-khumthai_20260226_1111.pdf\nsarun_khumthai_data_migration_engineer\nSenior-Fullstack-Developer\nsenior fullstack developer\nprofile_resume"

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            suggestions = await RenameService().suggest_names(
                original_name="sarun-khumthai_20260226_1111.pdf",
                extension=".pdf",
                summary="Senior full-stack developer profile",
                count=3,
            )
        finally:
            llm_client.achat = original_achat

        assert suggestions == [
            "data_migration_engineer.pdf",
            "senior_fullstack_developer.pdf",
            "profile_resume.pdf",
        ]

    asyncio.run(run())