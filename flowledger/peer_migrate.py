"""Explicit, offline cutover from a stopped coordinator to a new peer file."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from .core import Repository
from .history import _COLUMNS
from .sync_server import load_settings


def _readonly(path):
    return sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True)


def _proof(conn, server):
    conn.execute('BEGIN')
    try:
        Repository._validate_database(conn)
        meta = {k: json.loads(v) for k, v in conn.execute('SELECT key,value FROM sync_meta')}
        if not meta.get('dataset') or bool(meta.get('server')) != server:
            raise ValueError('Expected a legacy coordinator.' if server else 'Choose a joined legacy client file.')
        if conn.execute('SELECT 1 FROM sync_outbox LIMIT 1').fetchone():
            raise ValueError('Pending or disputed edits: finish synchronizing this device first.')
        digest = hashlib.sha256()
        for table, cols in sorted(_COLUMNS.items()):
            names = ','.join(cols)
            for row in conn.execute(f'SELECT {names} FROM {table} ORDER BY {names}'):
                digest.update(json.dumps([table, row], separators=(',', ':')).encode('ascii') + b'\n')
        return f"{meta['dataset']}:{meta.get('cursor', 0)}:{digest.hexdigest()}"
    finally:
        conn.rollback()


def proof(path):
    with closing(_readonly(path)) as conn:
        return _proof(conn, False)


def migrate(directory, destination, name, receipts, *, all_closed=False):
    from . import peer_store
    if not all_closed or not receipts:
        raise ValueError('Close all joined clients and supply the receipt from every client.')
    directory, config = load_settings(directory)
    dest = Path(destination).expanduser().resolve()
    if dest.exists() or dest.is_relative_to(directory) or not dest.parent.is_dir():
        raise ValueError('Choose a new file in an existing directory outside the coordinator.')
    if config.get('mirror'):
        raise ValueError('This coordinator has a router mirror. Keep using the coordinator while you rely on that mirror. No files were changed.')
    src = directory / config['database']
    retired = directory / 'coordinator.retired.json'
    with (directory / 'service.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Stop the coordinator service before migration.') from exc
        if retired.exists() or not (directory / 'coordinator.json').is_file():
            raise ValueError('Coordinator already retired or directory changed.')
        with tempfile.TemporaryDirectory(prefix='.flow-convert-', dir=dest.parent) as tmp:
            staged = Path(tmp) / 'peer.sqlite3'
            with closing(_readonly(src)) as source:
                expected = _proof(source, True)
                if any(value != expected for value in receipts) or not expected.startswith(config['dataset'] + ':'):
                    raise ValueError('A client differs from the coordinator. Reconnect all old clients before migrating.')
                with closing(sqlite3.connect(staged)) as target:
                    source.backup(target)
            staged.chmod(0o600)
            with closing(sqlite3.connect(staged)) as target:
                # Only clear service metadata on the private, unpublished copy.
                target.execute('PRAGMA secure_delete=ON')
                for table in ('sync_meta', 'sync_base', 'sync_versions', 'sync_base_versions',
                              'sync_remote_versions', 'sync_outbox', 'sync_log', 'sync_receipts'):
                    target.execute('DROP TABLE ' + table)
                for table in _COLUMNS:
                    for op in ('insert', 'update', 'delete'):
                        target.execute(f'DROP TRIGGER IF EXISTS sync_guard_{table}_{op}')
                target.commit()
                target.execute('VACUUM')
            repo = Repository(staged)
            try:
                peer_store.share(repo, name, dataset_id=config['dataset'])
                if peer_store.dataset(repo) != config['dataset']:
                    raise ValueError('Converted file has the wrong dataset identity.')
                if repo.conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Converted database did not pass SQLite integrity checking.')
            finally:
                repo.close()
            # Retire the old endpoint first. Even older server versions require
            # coordinator.json, so they cannot silently serve a divergent dataset.
            # Publish without overwrite; restore the old config on a normal failure.
            os.rename(directory / 'coordinator.json', retired)
            try:
                os.link(staged, dest)
            except BaseException:
                os.rename(retired, directory / 'coordinator.json')
                raise
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    check = sub.add_parser('check', help='Print a receipt after draining one old client')
    check.add_argument('file', type=Path)
    go = sub.add_parser('convert', help='Retire the stopped coordinator and create a peer file')
    go.add_argument('--directory', type=Path, required=True)
    go.add_argument('--output', type=Path, required=True)
    go.add_argument('--name', required=True)
    go.add_argument('--receipt', action='append', required=True)
    go.add_argument('--all-clients-closed', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'check':
            print(proof(args.file))
        else:
            dest = migrate(args.directory, args.output, args.name, args.receipt,
                           all_closed=args.all_clients_closed)
            print(f'Coordinator retired. Open {dest} in Flow and select Share this file.')
    except (OSError, sqlite3.Error, ValueError) as exc:
        print('Flow migration: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
