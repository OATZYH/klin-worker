"""Integration tests for /api/settings/auto-organize routes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_master_switch_defaults_to_false(test_client) -> None:
    response = test_client.get("/api/settings/auto-organize")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}


def test_master_switch_update_round_trip(test_client) -> None:
    put_response = test_client.put(
        "/api/settings/auto-organize",
        json={"enabled": True},
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"enabled": True}

    get_response = test_client.get("/api/settings/auto-organize")
    assert get_response.json() == {"enabled": True}


def test_create_watcher_folder_success(test_client, tmp_path: Path) -> None:
    response = test_client.post(
        "/api/settings/auto-organize/folders",
        json={
            "folder_path": str(tmp_path),
            "auto_organize_enabled": True,
            "frequency_value": 1,
            "frequency_unit": "hour",
            "recursive": False,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["folder_path"] == str(tmp_path.resolve())
    assert body["frequency_unit"] == "hour"
    assert body["frequency_seconds"] == 3600
    assert body["recursive"] is False
    assert body["next_scan_at"] is not None


def test_create_watcher_folder_rejects_non_absolute_path(test_client) -> None:
    response = test_client.post(
        "/api/settings/auto-organize/folders",
        json={"folder_path": "relative-dir"},
    )
    assert response.status_code == 400
    assert "absolute" in response.json()["detail"].lower()


def test_create_watcher_folder_rejects_missing_path(test_client) -> None:
    response = test_client.post(
        "/api/settings/auto-organize/folders",
        json={"folder_path": "/nonexistent/folder/deep"},
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_create_watcher_folder_409_on_duplicate(test_client, tmp_path: Path) -> None:
    payload = {"folder_path": str(tmp_path)}
    first = test_client.post("/api/settings/auto-organize/folders", json=payload)
    assert first.status_code == 201
    second = test_client.post("/api/settings/auto-organize/folders", json=payload)
    assert second.status_code == 409


def test_list_watcher_folders(test_client, tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    for folder in (a, b):
        assert (
            test_client.post(
                "/api/settings/auto-organize/folders",
                json={"folder_path": str(folder)},
            ).status_code
            == 201
        )
    list_response = test_client.get("/api/settings/auto-organize/folders")
    assert list_response.status_code == 200
    folders = list_response.json()["results"]
    assert len(folders) == 2


def test_update_watcher_folder_404_when_missing(test_client) -> None:
    response = test_client.patch(
        "/api/settings/auto-organize/folders/nonexistent-id",
        json={"recursive": False},
    )
    assert response.status_code == 404


def test_update_watcher_folder_requires_at_least_one_field(
    test_client,
    tmp_path: Path,
) -> None:
    create = test_client.post(
        "/api/settings/auto-organize/folders",
        json={"folder_path": str(tmp_path)},
    )
    watcher_id = create.json()["id"]

    response = test_client.patch(
        f"/api/settings/auto-organize/folders/{watcher_id}",
        json={},
    )
    assert response.status_code == 422


def test_delete_watcher_folder_round_trip(test_client, tmp_path: Path) -> None:
    create = test_client.post(
        "/api/settings/auto-organize/folders",
        json={"folder_path": str(tmp_path)},
    )
    watcher_id = create.json()["id"]

    delete_response = test_client.delete(
        f"/api/settings/auto-organize/folders/{watcher_id}",
    )
    assert delete_response.status_code == 204

    second_delete = test_client.delete(
        f"/api/settings/auto-organize/folders/{watcher_id}",
    )
    assert second_delete.status_code == 404
