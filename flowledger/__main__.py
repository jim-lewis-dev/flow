"""Launch the desktop application with a personal or isolated demo database."""

import argparse
import os
from pathlib import Path
import signal
import sqlite3
import sys


def database_path(name: str | None = None, *, db: Path | None = None,
                  demo: bool = False) -> Path:
    """Resolve a dataset name from the caller's directory, then user data."""
    data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "flowledger"
    if db is not None:
        return db.expanduser()
    if name is None:
        return data_root / ("demo.sqlite3" if demo else "flow.sqlite3")
    if not name.strip() or name in (".", ".."):
        raise ValueError("Enter a dataset name or file path.")
    path = Path(name).expanduser()
    if not path.suffix:
        path = path.with_name(path.name + ".sqlite3")
    if path.is_absolute() or "/" in name:
        return path
    local = Path.cwd() / path
    try:
        local.lstat()
    except FileNotFoundError:
        return data_root / path
    return local


def main() -> int:
    repo = app = None
    heartbeat = None
    interrupted = closing = False
    tkinter = sys.modules.get("tkinter")
    previous_root = getattr(tkinter, "_default_root", None)

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True
        if not closing:
            # Tk propagates SystemExit without printing a callback traceback,
            # so even a long callback stops immediately and unwinds its cleanup.
            raise SystemExit(0)

    previous_handler = signal.signal(signal.SIGINT, interrupt)
    try:
        parser = argparse.ArgumentParser(prog="flow", description="Flow - local resource tracking")
        parser.add_argument("name", nargs="?", help="Dataset name or path (.sqlite3 is added when omitted)")
        parser.add_argument("--db", type=Path, help="SQLite database path")
        parser.add_argument("--demo", action="store_true", help="Open the separate illustrative demo")
        args = parser.parse_args()
        if sum((args.name is not None, args.db is not None, args.demo)) > 1:
            parser.error("Choose a dataset name, --db, or --demo.")
        try:
            db_path = database_path(args.name, db=args.db, demo=args.demo)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            print(f"Cannot open dataset: {exc}", file=sys.stderr)
            return 1
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "flowledger"
        cache_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
        try:
            from .core import Repository
            from .gui import FlowApp
        except ImportError as exc:
            print(f"Missing application dependency: {exc}\nOn Ubuntu: sudo apt install python3-tk python3-matplotlib", file=sys.stderr)
            return 1
        try:
            repo = Repository(db_path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"Cannot open dataset {db_path}: {exc}", file=sys.stderr)
            return 1
        if args.demo:
            from .demo import seed_demo
            seed_demo(repo)
        app = FlowApp(repo, demo=args.demo)

        def poll_signals():
            nonlocal heartbeat
            heartbeat = None
            if interrupted:
                app.quit()
            else:
                heartbeat = app.after(50, poll_signals)

        # Idle Tk otherwise remains inside a C event wait, delaying Python's
        # signal handler until an unrelated GUI event wakes it up.
        heartbeat = app.after(50, poll_signals)
        app.mainloop()
    except KeyboardInterrupt:
        return 0
    except SystemExit:
        if interrupted:
            return 0
        raise
    finally:
        closing = True
        try:
            tkinter = sys.modules.get("tkinter")
            if app is None:
                root = getattr(tkinter, "_default_root", None)
                if root is not previous_root:
                    app = root
            if app is not None:
                try:
                    if heartbeat is not None:
                        app.after_cancel(heartbeat)
                    if app.winfo_exists():
                        experimental = getattr(app, "experimental", None)
                        if experimental is not None:
                            experimental.close()
                except tkinter.TclError:
                    pass  # Normal window close may have destroyed Tk already.
                finally:
                    try:
                        history = getattr(app, "history", None)
                        if history is not None:
                            history.close()
                    finally:
                        try:
                            app.destroy()
                        except tkinter.TclError:
                            pass
        finally:
            try:
                if repo is not None:
                    repo.close()
            finally:
                signal.signal(signal.SIGINT, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
