"""Integration tests for /api/settings/locks."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_get_locks_defaults_to_empty(test_client) -> None:
    response = test_client.get("/api/settings/locks")
    assert response.status_code == 200
    payload = response.json()
    assert payload["lock_file"] == []
    assert payload["lock_folder"] == []
    assert payload["lock_file_status"]["key"] == "lock_file"
    assert payload["lock_folder_status"]["key"] == "lock_folder"


def test_put_locks_round_trip(test_client) -> None:
    body = {
        "lock_file": ["/tmp/secret.pdf"],
        "lock_folder": ["/tmp/private"],
    }
    put_response = test_client.put("/api/settings/locks", json=body)
    assert put_response.status_code == 200

    get_response = test_client.get("/api/settings/locks")
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["lock_file"] == ["/tmp/secret.pdf"]
    assert payload["lock_folder"] == ["/tmp/private"]


def test_put_locks_rejects_relative_paths(test_client) -> None:
    response = test_client.put(
        "/api/settings/locks",
        json={"lock_file": ["relative.pdf"], "lock_folder": []},
    )
    assert response.status_code == 400
    assert "absolute" in response.json()["detail"].lower()


def test_put_locks_accepts_empty_lists(test_client) -> None:
    response = test_client.put(
        "/api/settings/locks",
        json={"lock_file": [], "lock_folder": []},
    )
    assert response.status_code == 200
