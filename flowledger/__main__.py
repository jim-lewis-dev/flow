"""Launch the desktop application with a personal or isolated demo database."""

import argparse
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Flow - local resource tracking")
    parser.add_argument("--db", type=Path, help="SQLite database path")
    parser.add_argument("--demo", action="store_true", help="Open the separate illustrative demo")
    args = parser.parse_args()
    data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "flowledger"
    db_path = args.db.expanduser() if args.db else data_root / ("demo.sqlite3" if args.demo else "flow.sqlite3")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "flowledger"
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
    try:
        from .core import Repository
        from .gui import FlowApp
    except ImportError as exc:
        print(f"Missing application dependency: {exc}\nOn Ubuntu: sudo apt install python3-tk python3-matplotlib", file=sys.stderr)
        return 1
    repo = Repository(db_path)
    try:
        if args.demo:
            from .demo import seed_demo
            seed_demo(repo)
        app = FlowApp(repo, demo=args.demo)
        app.mainloop()
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
