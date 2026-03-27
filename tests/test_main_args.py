from __future__ import annotations

import sys

from main import parse_args


def test_parse_args_defaults(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["klin-worker"])
    args = parse_args()

    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.log_level == "info"
    assert args.reload is False
    assert args.data_dir is None


def test_parse_args_custom_values(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "klin-worker",
            "--host",
            "0.0.0.0",
            "--port",
            "9001",
            "--log-level",
            "debug",
            "--reload",
            "--data-dir",
            "./tmp-data",
        ],
    )
    args = parse_args()

    assert args.host == "0.0.0.0"
    assert args.port == 9001
    assert args.log_level == "debug"
    assert args.reload is True
    assert args.data_dir == "./tmp-data"
