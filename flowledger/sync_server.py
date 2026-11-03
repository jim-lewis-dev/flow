"""Optional single-coordinator service; run ``python -m flowledger.sync_server --help``.

The authoritative SQLite file is local to this process. A router-drive mirror
is a separate consistent snapshot, published on a daemon worker so an unavailable
share never blocks operation delivery. Only the Python standard library and
OpenSSL (certificate creation, once) are needed on the coordinator.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import uuid

from .core import Repository
from .sync import SyncState
from .sync_transport import MAX_MESSAGE, parse_connection


def _write_json(path, value):
    target = Path(path)
    temporary = target.with_name(target.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, target)


def load_settings(directory):
    directory = Path(directory).expanduser().resolve()
    settings = json.loads((directory / 'coordinator.json').read_text())
    parse_connection(settings)
    return directory, settings


def initialize(source, directory, endpoint, mirror=None):
    """Seed a NEW coordinator directory without modifying the source file."""
    source = Path(source).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    if not source.is_file():
        raise ValueError('The source database does not exist.')
    settings = dict(endpoint=endpoint.rstrip('/'), dataset=str(uuid.uuid4()),
                    token=secrets.token_urlsafe(32), certificate='0' * 64)
    parse_connection(settings)
    if directory.exists():
        raise ValueError('Choose a new coordinator directory; the existing directory was not changed.')
    mirror_path = Path(mirror).expanduser().resolve() if mirror else None
    if mirror_path and (mirror_path == source or mirror_path == directory or directory in mirror_path.parents):
        raise ValueError('Choose a snapshot destination separate from the original file and coordinator directory.')
    if not shutil.which('openssl'):
        raise ValueError('OpenSSL is needed once to create the coordinator certificate.')
    # Validate before creating files. Existing pending edits must be synchronized
    # with their original coordinator rather than starting a second authority.
    with_source = Repository(source)
    try:
        if getattr(with_source, 'sync', None) and with_source.sync.status().get('enabled'):
            raise ValueError('This file already belongs to a coordinator. Use its existing service directory.')
        directory.mkdir(parents=True, mode=0o700)
        try:
            database = directory / source.name
            with_source.backup(database)
            database.chmod(0o600)
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256',
                '-nodes', '-days', '3650', '-subj', '/CN=Flow coordinator',
                '-keyout', str(directory / 'server.key'),
                '-out', str(directory / 'server.crt'),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            (directory / 'server.key').chmod(0o600)
            pem = (directory / 'server.crt').read_text()
            settings['certificate'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            settings['database'] = database.name
            settings['mirror'] = str(mirror_path) if mirror_path else None
            repo = Repository(database)
            try:
                sync = SyncState(repo)
                sync.configure(dataset=settings['dataset'], client='coordinator', server=True)
            finally:
                repo.close()
            _write_json(directory / 'coordinator.json', settings)
        except BaseException:
            shutil.rmtree(directory)
            raise
    finally:
        with_source.close()
    return settings


class SnapshotMirror:
    """One replaceable snapshot, never the live database and never in the API path."""
    def __init__(self, source, destination):
        self.source = Path(source)
        self.destination = Path(destination) if destination else None
        self.changed = threading.Event()
        self.stopping = threading.Event()
        self.error = None
        self.thread = None

    def start(self):
        if self.destination:
            self.thread = threading.Thread(target=self._run, name='flow-snapshot', daemon=True)
            self.thread.start()
            self.changed.set()

    def _publish(self):
        # First obtain a consistent LOCAL snapshot. SQLite connections belong to
        # this worker. Slow network writes happen after both DBs are closed.
        with tempfile.TemporaryDirectory(prefix='flow-mirror-') as temporary:
            local = Path(temporary) / 'snapshot.sqlite3'
            with sqlite3.connect(self.source) as source, sqlite3.connect(local) as target:
                source.backup(target)
            destination = self.destination
            if not destination.parent.is_dir():
                raise OSError('The snapshot destination directory is unavailable.')
            staged = destination.with_name('.' + destination.name + '.sync-tmp')
            try:
                with local.open('rb') as incoming, staged.open('wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.replace(staged, destination)
            finally:
                try:
                    staged.unlink(missing_ok=True)
                except OSError:
                    pass

    def _run(self):
        while not self.stopping.is_set():
            if not self.changed.wait(10):
                continue
            self.changed.clear()
            try:
                self._publish()
                self.error = None
            except (OSError, sqlite3.Error) as exc:
                message = 'Router snapshot unavailable; device synchronization continues.'
                if self.error != message:
                    print(message, file=sys.stderr, flush=True)
                self.error = message
                # Retry without a busy loop, independently of new edits.
                if not self.stopping.wait(10):
                    self.changed.set()

    def stop(self):
        self.stopping.set()
        self.changed.set()


class Coordinator(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, directory, settings, *, tls=True):
        self.directory = Path(directory)
        self.settings = settings
        self.database = self.directory / settings['database']
        self.operation_lock = threading.Lock()
        destination = settings.get('mirror')
        if destination:
            target = Path(destination).expanduser().resolve()
            if target == self.directory.resolve() or self.directory.resolve() in target.parents:
                raise ValueError('The snapshot destination must be outside the coordinator directory.')
        self.mirror = SnapshotMirror(self.database, settings.get('mirror'))
        self.tls_context = None
        super().__init__(address, RequestHandler)
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.directory / 'server.crt', self.directory / 'server.key')
            self.tls_context = context

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(5)
        if self.tls_context:
            try:
                # The worker performs its TLS handshake on the first read.
                # A slow handshake must not block other devices from connecting.
                connection = self.tls_context.wrap_socket(connection, server_side=True,
                                                          do_handshake_on_connect=False)
            except BaseException:
                connection.close()
                raise
        return connection, address

    def server_close(self):
        self.mirror.stop()
        super().server_close()

    def process(self, path, request):
        if request.get('dataset') != self.settings['dataset']:
            raise ValueError('This connection belongs to a different Flow file.')
        with self.operation_lock:
            repo = Repository(self.database)
            try:
                state = repo.sync
                previous_revision = state.status()['cursor']
                if path == '/v1/bootstrap':
                    result = state.export_seed()
                else:
                    result = state.server_exchange(request)
                changed = state.status()['cursor'] != previous_revision
            finally:
                repo.close()
        if changed:
            self.mirror.changed.set()
        return result


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'FlowSync/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, TimeoutError):
            # Pin validation deliberately disconnects before HTTP. A phone can
            # also leave Wi-Fi midway through a request; retries are idempotent.
            self.close_connection = True

    def log_message(self, format, *args):
        # Neither tokens nor record contents belong in the service log.
        pass

    def respond(self, status, result):
        body = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        if len(body) > MAX_MESSAGE:
            status = 413
            body = b'{"error":"The sync response is too large."}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        try:
            expected = 'Bearer ' + self.server.settings['token']
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
                self.respond(401, {'error': 'The connection key is incorrect.'})
                return
            if self.path not in ('/v1/bootstrap', '/v1/exchange'):
                self.respond(404, {'error': 'Unknown sync endpoint.'})
                return
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Use a request with a known size.')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_MESSAGE:
                self.respond(413, {'error': 'The sync request is too large or empty.'})
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('The sync request was interrupted.')
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError('The sync request must be an object.')
            result = self.server.process(self.path, request)
            self.respond(200, result)
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
            self.respond(400, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.respond(500, {'error': 'The coordinator could not apply this request. Local edits remain saved.'})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Coordinate optional Flow synchronization on one computer.')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Seed a new coordinator from an existing local Flow file')
    init.add_argument('source', type=Path)
    init.add_argument('--directory', type=Path, required=True, help='New local service directory')
    init.add_argument('--url', required=True, help='HTTPS address reachable by your devices, including port')
    init.add_argument('--mirror', type=Path, help='Optional router-drive snapshot destination file')
    serve = commands.add_parser('serve', help='Run the existing coordinator')
    serve.add_argument('--directory', type=Path, required=True)
    serve.add_argument('--bind', default='0.0.0.0', help='Local listen address (default: all IPv4 interfaces)')
    connection = commands.add_parser('connection', help='Print private connection text to paste into Flow')
    connection.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            settings = initialize(args.source, args.directory, args.url, args.mirror)
            print(f'Coordinator created: {args.directory.expanduser().resolve()}')
            print('The original file was not changed. Start the service, then pair your devices.')
            return 0
        directory, settings = load_settings(args.directory)
        if args.command == 'connection':
            print(json.dumps(parse_connection(settings), separators=(',', ':')))
            return 0
        # One service owns this directory. Never elect a second coordinator when
        # the first is unreachable: offline clients simply keep their outboxes.
        import fcntl
        with (directory / 'service.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('This coordinator is already running.') from exc
            port = urlsplit(settings['endpoint']).port or 443
            with Coordinator((args.bind, port), directory, settings) as service:
                service.mirror.start()
                print(f'Flow coordinator listening on {args.bind}:{port}', flush=True)
                try:
                    service.serve_forever(poll_interval=0.25)
                except KeyboardInterrupt:
                    pass
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        print(f'Flow sync: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
