from __future__ import annotations

from app.main import _request_trace_input


def test_request_trace_input_captures_search_body_query() -> None:
    trace_input = _request_trace_input(
        "POST",
        "/api/search/files",
        {},
        b'{"query":"deepseek ocr","ignored":"value"}',
    )

    assert trace_input == {
        "method": "POST",
        "path": "/api/search/files",
        "query_params": {},
        "body": {"query": "deepseek ocr"},
    }


def test_request_trace_input_ignores_invalid_search_body() -> None:
    trace_input = _request_trace_input("POST", "/api/search/files", {}, b"{not-json")

    assert trace_input == {
        "method": "POST",
        "path": "/api/search/files",
        "query_params": {},
        "body_parse_error": True,
    }


def test_request_trace_input_does_not_capture_non_search_body() -> None:
    trace_input = _request_trace_input(
        "POST",
        "/api/summary",
        {},
        b'{"query":"should not appear"}',
    )

    assert trace_input == {
        "method": "POST",
        "path": "/api/summary",
        "query_params": {},
    }
