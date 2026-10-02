"""ZYRO local product command line."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zyro", description="ZYRO local Personal Executive")
    subcommands = parser.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="start the local API and frontend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    serve.add_argument("--data-dir", default=os.environ.get("ZYRO_DATA_DIR", ".zyro"))
    subcommands.add_parser("check", help="check local database integrity")
    backup = subcommands.add_parser("backup", help="create safe SQLite backups")
    backup.add_argument("destination", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    command = args.command
    if command is None:
        from zyro.runtime.bootstrap import initialize_runtime

        runtime = initialize_runtime()
        print(
            "ZYRO foundation runtime initialized "
            f"(environment={runtime.config.environment}, logging={runtime.config.log_level}); "
            "run `zyro serve` to open the local product"
        )
        return
    if command == "serve":
        os.environ["ZYRO_DATA_DIR"] = str(args.data_dir)
        import uvicorn

        print(f"ZYRO is starting at http://{args.host}:{args.port}")
        uvicorn.run("zyro.api.app:app", host=args.host, port=args.port, reload=False)
        return
    data_dir = Path(os.environ.get("ZYRO_DATA_DIR", ".zyro"))
    databases = tuple(data_dir.glob("*.sqlite"))
    if command == "check":
        import sqlite3

        failed: list[str] = []
        for path in databases:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            print(f"{path.name}: {result}")
            if result != "ok":
                failed.append(path.name)
        if failed:
            raise SystemExit(1)
        return
    if command == "backup":
        import sqlite3

        args.destination.mkdir(parents=True, exist_ok=True)
        for path in databases:
            target = args.destination / path.name
            source = sqlite3.connect(path)
            output = sqlite3.connect(target)
            try:
                source.backup(output)
            finally:
                output.close()
                source.close()
            print(f"backed up {path.name} -> {target}")


if __name__ == "__main__":
    main()
