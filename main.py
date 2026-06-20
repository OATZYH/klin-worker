"""Executable entrypoint for klin-worker sidecar packaging."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="klin-worker")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload (dev only)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override KLIN_APP_DATA_DIR for DB/storage paths",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["KLIN_APP_DATA_DIR"] = str(data_dir)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
