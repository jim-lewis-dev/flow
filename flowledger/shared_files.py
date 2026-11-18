"""Readable desktop copy names and an explicit, offline path cleanup."""
from contextlib import closing
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .core import APPLICATION_ID, SCHEMA_VERSION


def _data_root():
    return (Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
            / 'flowledger').expanduser().resolve()


def shared_path(name):
    """Return a safe readable path; joining still refuses an existing file."""
    if (not isinstance(name, str) or not name or name != name.strip()
            or not name.isascii() or len(name) > 200 or name.startswith('.')
            or name.endswith('.') or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(c in '<>:"/\\|?*' for c in name)):
        raise ValueError('Choose a shared name using plain letters, numbers, spaces or punctuation without path separators.')
    stem = name[:-8] if name.lower().endswith('.sqlite3') else name
    if not stem or stem.endswith((' ', '.')):
        raise ValueError('Choose a nonempty shared file name.')
    target = _data_root() / 'shared' / (stem + '.sqlite3')
    if target.parent.is_dir():
        for entry in target.parent.iterdir():
            if entry.name.casefold() == target.name.casefold() and entry.name != target.name:
                raise ValueError('A shared file with that name already exists: ' + entry.name)
    return target


def _identity(path, dataset, baseline):
    for suffix in ('-journal', '-wal', '-shm'):
        if Path(str(path) + suffix).exists():
            raise ValueError('Close Flow cleanly before renaming shared files: ' + str(path))
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
        if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
            raise ValueError('The registered path is not a current Flow file: ' + str(path))
        values = dict(db.execute("SELECT key,value FROM sync_meta WHERE key IN ('peer_dataset','peer_genesis')"))
        if (json.loads(values.get('peer_dataset', 'null')) != dataset
                or json.loads(values.get('peer_genesis', 'null')) != baseline):
            raise ValueError('The registered file has a different sharing identity: ' + str(path))


def _same_file(a, b):
    return a.exists() and b.exists() and os.path.samestat(a.stat(), b.stat())


def _aliases(root, old, new):
    result = []
    for path in root.iterdir():
        if path.is_symlink() and path.resolve() in (old, new):
            result.append(path)
    return result


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_link(path, target):
    temporary = path.with_name('.flow-link-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def rename_shared_files():
    """Rename only generated desktop copies. All Flow windows must be closed.

    The new name first links the same inode. Replacing the old name with a
    symlink keeps the old catalog and shortcuts usable through interruption.
    Catalog paths commit before shortcuts change; obsolete links go last.
    Rerunning finishes any interrupted step without recreating ledger data.
    """
    root = _data_root()
    settings = (Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
                / 'flow' / 'peers').expanduser().resolve()
    catalog = settings / 'devices.sqlite3'
    if not catalog.is_file():
        return []
    with (settings / 'listener.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Close all Flow windows on this computer before renaming shared files.') from None
        with closing(sqlite3.connect(catalog.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            db.execute('BEGIN IMMEDIATE')
            moves = []
            destinations = set()
            try:
                rows = db.execute('SELECT dataset,name,path,baseline FROM files ORDER BY dataset').fetchall()
                for dataset, name, saved, baseline in rows:
                    old = root / 'shared' / (sha256(dataset.encode()).hexdigest() + '.sqlite3')
                    current = Path(saved)
                    # Original files stay exactly where their owners put them.
                    if current != old and current.parent != old.parent:
                        continue
                    new = shared_path(name)
                    if current not in (old, new):
                        continue
                    aliases = _aliases(root, old, new)
                    pending_alias = any(p.resolve() == old for p in aliases)
                    if current == new and not old.exists() and not old.is_symlink() and not pending_alias:
                        continue
                    if new == old:
                        raise ValueError('Choose a readable name different from the generated file name.')
                    folded = str(new).casefold()
                    if folded in destinations:
                        raise ValueError('Two shared files have the same local name: ' + new.name)
                    destinations.add(folded)
                    if new.is_symlink():
                        raise ValueError('The destination is already a shortcut: ' + str(new))
                    if old.is_symlink():
                        if old.resolve() != new or not new.is_file():
                            raise ValueError('The generated path points to a different file: ' + str(old))
                    elif old.exists():
                        if not old.is_file() or (new.exists() and not _same_file(old, new)):
                            raise ValueError('The readable filename is already in use: ' + str(new))
                    elif not new.is_file():
                        raise ValueError('The registered shared file is missing: ' + str(old))
                    # Never adopt an unrelated existing destination as recovery.
                    if current == old and not old.exists() and not old.is_symlink():
                        raise ValueError('The registered shared file is missing: ' + str(old))
                    source = new if new.exists() else old
                    _identity(source, dataset, baseline)
                    if old.exists():
                        _identity(old, dataset, baseline)
                    moves.append((old, new, dataset, current, aliases))
                # Nothing has moved until every name and identity passes.
                for old, new, dataset, current, aliases in moves:
                    if not new.exists():
                        os.link(old, new)
                    _sync_directory(new.parent)
                    if not old.is_symlink():
                        if old.exists() and not _same_file(old, new):
                            raise ValueError('The source changed while renaming: ' + str(old))
                        _replace_link(old, new)
                    db.execute('UPDATE files SET path=? WHERE dataset=? AND path=?',
                               (str(new), dataset, str(current)))
                db.commit()
            except BaseException:
                db.rollback()
                # Interrupted links remain usable; rerun this command to finish.
                raise
            for old, new, dataset, current, aliases in moves:
                for alias in aliases:
                    if not alias.is_symlink() or alias.resolve() not in (old, new):
                        raise ValueError('A shortcut changed while renaming: ' + str(alias))
                    _replace_link(alias, new)
                if old.is_symlink() and old.resolve() == new:
                    old.unlink()
                    _sync_directory(old.parent)
            return [(old, new) for old, new, *_ in moves]
