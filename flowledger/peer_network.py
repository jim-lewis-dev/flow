"""Opt-in device-to-device synchronization. Each installation owns a private identity
and a catalog of selected local files. The network never accepts filesystem paths.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import fcntl
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time

from . import peer_wire as wire
from .peer_nearby import NearbyPairing, _get as _near_get, _set as _near_set
from .core import APPLICATION_ID, SCHEMA_VERSION, Repository


def default_config_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'flow' / 'peers'


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(ch) < 32 for ch in value):
        raise ValueError('Choose a short, nonempty display name.')
    return value.strip()


def _desktop_cert(cert, key):
    try:
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
                        'ec_paramgen_curve:prime256v1', '-nodes', '-sha256', '-days', '3650',
                        '-subj', '/CN=Flow peer', '-keyout', str(key), '-out', str(cert)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Install OpenSSL to create this device identity, then start sharing again.') from exc


def _state(db, key, value=None):
    if value is not None:
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else ''


def _inventory_digest(ids):
    digest = hashlib.sha256()
    for item in sorted(ids):
        digest.update(item.encode('ascii') + b'\n')
    return digest.hexdigest()


def _now():
    return int(time.time())


class PeerNode(NearbyPairing):
    def __init__(self, config_dir=None, *, name=None, host='0.0.0.0', port=wire.PORT,
                 discovery=True, interval=10, cert_factory=None):
        self.directory = Path(config_dir or default_config_dir()).expanduser().resolve()
        self.host, self.port = host, port
        self.discovery, self.interval = discovery, max(float(interval), 0.2)
        self.cert_factory = cert_factory or _desktop_cert
        self.default_name = _name(name or socket.gethostname() or 'Flow device')
        self._life = threading.RLock()
        self._sync_lock = threading.Lock()
        self._started = False
        self._listener = self._owner = self._monitor = self._discovery = None
        self._addresses = None
        self._identity = None
        self._stopped = threading.Event()
        self._initialize()

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.directory / 'devices.sqlite3', timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def _initialize(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    address TEXT NOT NULL, token TEXT NOT NULL, seen INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS files(dataset TEXT PRIMARY KEY, name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE, baseline TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sightings(id TEXT PRIMARY KEY,address TEXT NOT NULL,seen INTEGER NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('name', self.default_name))
        os.chmod(self.directory / 'devices.sqlite3', 0o600)

    def _cert(self):
        cert, key = (self.directory / 'device-cert.pem', self.directory / 'device-key.pem')
        # A follower may need the identity even while another process owns the listener.
        with (self.directory / 'identity.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not cert.exists() and not key.exists():
                with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
                    c, k = Path(tmp) / cert.name, Path(tmp) / key.name
                    self.cert_factory(c, k)
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(c, k)
                    k.chmod(0o600)
                    os.replace(k, key)
                    os.replace(c, cert)
            if not cert.is_file() or not key.is_file():
                raise ValueError('The saved device identity is incomplete. Restore the original key and certificate.')
            identity = wire.pin(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii')))
            with self._db() as db:
                previous = _state(db, 'identity')
                if previous and not hmac.compare_digest(identity, previous):
                    raise ValueError('The device certificate changed. Restore the original device identity.')
                if not previous:
                    _state(db, 'identity', identity)
        self._identity = identity
        return cert, key

    def _acquire(self):
        owner = (self.directory / 'listener.lock').open('a+b')
        try:
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                owner.close()
                return
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.directory / 'device-cert.pem', self.directory / 'device-key.pem')
            listener = wire.Listener(self.host, self.port, context, self._handle)
            try:
                with self._db() as db:
                    self._addresses = [wire.address(ip, listener.port) for ip in wire.lan_hosts(self.host)]
                    _state(db, 'address', next(iter(self._addresses), ''))
                    _state(db, 'addresses', json.dumps(self._addresses))
                    _state(db, 'port', listener.port)
                    _state(db, 'nearby_listener', 1)
                    _state(db, 'discovery_error', '')
                listener.thread.start()
            except BaseException:
                listener.socket.close()
                raise
            self._listener, self._owner = listener, owner
            if self.discovery:
                self._discovery = threading.Thread(target=self._beacons, args=(self._stopped, listener.port),
                                                   name='flow-discovery', daemon=True)
                self._discovery.start()
        except BaseException:
            owner.close()
            raise

    def start(self):
        with self._life:
            if self._started:
                return self
            self._cert()
            self._stopped = threading.Event()
            self._acquire()
            self._started = True
            self._monitor = threading.Thread(target=self._run, args=(self._stopped,),
                                             name='flow-peer-service', daemon=True)
            self._monitor.start()
            return self

    def stop(self):
        with self._life:
            if not self._started:
                return
            self._started = False
            self._stopped.set()
            if self._listener:
                self._listener.close()
                self._listener = None
            if self._owner:
                self._owner.close()
                self._owner = None
            monitor, discovery = self._monitor, self._discovery
            self._monitor = self._discovery = None
        for thread in (monitor, discovery):
            if thread:
                thread.join(timeout=1)

    def _run(self, stop):
        due = time.monotonic() + self.interval
        while not stop.wait(min(0.5, self.interval)):
            try:
                with self._life:
                    if stop.is_set():
                        return
                    if self._owner is None:
                        self._acquire()
                    owner = self._owner is not None
                    if owner and time.monotonic() >= due:
                        endpoints = [wire.address(ip, self._listener.port) for ip in wire.lan_hosts(self.host)]
                        if endpoints != self._addresses:
                            with self._db() as db:
                                _state(db, 'address', next(iter(endpoints), ''))
                                _state(db, 'addresses', json.dumps(endpoints))
                            self._addresses = endpoints
                if owner and time.monotonic() >= due:
                    self._sync_active(stop)
                    due = time.monotonic() + self.interval
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                if stop.is_set():
                    return
                self._error(str(exc))
                due = time.monotonic() + self.interval

    def _error(self, message):
        with self._db() as db:
            _state(db, 'error', message[:500])

    def _beacons(self, stop, port):
        while not stop.is_set():
            try:
                wire.discover(stop, self._identity, port, self._sighting,
                              interface=self.host, on_status=self._discovery_status)
            except Exception as exc:
                # A stopped worker must not reopen device settings during teardown.
                if stop.is_set():
                    return
                # Binding or receiving failed. Preserve the TCP listener and retry UDP.
                logging.getLogger(__name__).warning('UDP discovery: %s', exc)
                self._discovery_status(f'UDP discovery on port {wire.DISCOVERY_PORT}: {exc}')
                stop.wait(10)

    def _discovery_status(self, message):
        try:
            with self._db() as db:
                _state(db, 'discovery_error', message[:500])
            return True
        except sqlite3.Error as exc:
            logging.getLogger(__name__).warning('Could not save discovery status: %s', exc)
            return False

    def _sighting(self, identity, endpoint):
        with self._db() as db:
            db.execute('DELETE FROM sightings WHERE seen<?', (_now() - 30,))
            db.execute('DELETE FROM sightings WHERE id IN (SELECT id FROM sightings ORDER BY seen DESC LIMIT -1 OFFSET 128)')
            db.execute('INSERT OR REPLACE INTO sightings VALUES (?,?,?)', (identity, endpoint, _now()))

    def peers(self):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                'SELECT id AS peer_id,name,address,seen AS last_seen FROM peers ORDER BY name,id')]

    def local_files(self):
        with self._db() as db:
            return [dict(r) for r in db.execute('SELECT dataset,name,path FROM files ORDER BY name,dataset')]

    def status(self):
        with self._db() as db:
            return dict(running=self._started, owner=self._owner is not None,
                        addresses=json.loads(_state(db, 'addresses') or '[]'),
                        address=_state(db, 'address'), port=int(_state(db, 'port') or 0),
                        name=_state(db, 'name'), peer_id=self._identity or _state(db, 'identity'),
                        peers=self.peers(), files=self.local_files(),
                        last_sync=int(_state(db, 'last_sync') or 0),
                        error=_state(db, 'error'), discovery_error=_state(db, 'discovery_error'))

    def forget(self, peer_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM peers WHERE id=?', (peer_id,))
            db.execute('DELETE FROM sightings WHERE id=?', (peer_id,))
            for key in ('pending', 'receipt'):
                saved = _near_get(db, key)
                if saved and saved.get('peer_id') == peer_id:
                    _near_set(db, key, None)
            _near_set(db, 'completed', [p for p in _near_get(db, 'completed', []) if p != peer_id])
            for key in ('candidates', 'probed'):
                saved = _near_get(db, key, {})
                saved.pop(peer_id, None)
                _near_set(db, key, saved)
            errors = json.loads(_state(db, 'peer_errors') or '{}')
            errors.pop(peer_id, None)
            _state(db, 'peer_errors', json.dumps(errors))
            _state(db, 'error', '; '.join(errors.values())[:500])

    def _peer_snapshot(self):
        # Tokens stay private; they let an older network attempt distinguish a
        # forgotten/re-paired device from the same identity's new authorization.
        with self._db() as db:
            return [dict(r) for r in db.execute('SELECT id AS peer_id,name,token FROM peers ORDER BY name,id')]

    def _save_peer_errors(self, failures, checked=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = dict(db.execute('SELECT id,token FROM peers'))
            errors = {}
            for peer, message in failures:
                if current.get(peer['peer_id']) == peer['token']:
                    previous = errors.get(peer['peer_id'])
                    errors[peer['peer_id']] = previous + '; ' + message if previous else message
            _state(db, 'peer_errors', json.dumps(errors))
            _state(db, 'error', '; '.join(errors.values())[:500])
            if checked:
                _state(db, 'last_sync', _now())
            return list(errors.values())

    def unshare(self, dataset):
        with self._db() as db:
            db.execute('DELETE FROM files WHERE dataset=?', (dataset,))

    def _device(self):
        if not self._started:
            self.start()
        # A companion process can hold the lock just before publishing its first address.
        for _ in range(30):
            with self._db() as db:
                port = int(_state(db, 'port') or 0)
                if port:
                    return dict(id=self._identity, name=_state(db, 'name'), port=port)
            if self._stopped.wait(0.1):
                break
        raise RuntimeError('The local peer listener has not started.')

    def _handle(self, hdr, stream, host):
        op = hdr.get('op')
        if isinstance(op,str) and op.startswith('nearby_'):
            return self._nearby_handle(hdr,stream,host)
        with self._db() as db:
            row = db.execute('SELECT token FROM peers WHERE id=?', (hdr.get('id'),)).fetchone()
            if not row or not hmac.compare_digest(row[0], str(hdr.get('token',''))):
                raise PermissionError()
            db.execute('UPDATE peers SET seen=? WHERE id=?', (_now(), hdr['id']))
            port = hdr.get('port')
            if type(port) is int and 0 < port < 65536:
                db.execute('INSERT OR REPLACE INTO sightings VALUES (?,?,?)',
                           (hdr['id'], wire.address(host, port), _now()))
        if op not in ('catalog', 'seed', 'pull', 'push'):
            raise ValueError('Unsupported request.')
        from . import peer_store as store
        # Pairing and discovery retain their existing format. File exchange has
        # its own version gate, before opening a file or accepting an event body.
        if type(hdr.get('protocol')) is not int or hdr['protocol'] != store.PROTOCOL:
            raise wire.Refused('Update Flow on both devices to synchronize this file.')
        def reply(result):
            return dict(protocol=store.PROTOCOL, result=result)
        if op == 'catalog':
            return reply([{k:f[k] for k in ('dataset','name')} for f in self.local_files()])
        dataset = hdr['dataset']
        with self._open(dataset) as (repo, name):
            if op != 'seed' and hdr.get('baseline') != self._baseline(repo):
                raise wire.Refused('The shared document has a different original snapshot. Check the local copies.')
            if op == 'seed':
                return reply(dict(name=name, seed=store.seed(repo)))
            if op == 'pull':
                known = list(store.inventory(repo))
                if hdr.get('inventory') == _inventory_digest(known):
                    return reply({'unchanged': True})
            wire.write(stream, {'ready': True, 'protocol': store.PROTOCOL}, wire.HEADER)
            body = wire.read(stream)
            if not isinstance(body, list):
                raise ValueError('Invalid batch.')
            if op == 'pull':
                return reply(dict(known=known, events=store.missing(repo, body)))
            if body:
                store.receive(repo, body)
            return reply(True)

    @contextmanager
    def _open(self, dataset):
        from . import peer_store as store
        with self._db() as db:
            file = db.execute('SELECT path,name,baseline FROM files WHERE dataset=?', (dataset,)).fetchone()
        if not file or not Path(file['path']).is_file():
            raise wire.Refused('Shared file is not available at its saved location.')
        def check(repo):
            # Use the same existing SQLite handle for preflight AND use: a path
            # replacement cannot trick Repository into using a different document.
            db = repo.conn
            if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or
                    db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION or
                    store.dataset(repo) != dataset or self._baseline(repo) != file['baseline']):
                raise wire.Refused('The file at this location is a different document.')
        with closing(Repository(file['path'], preflight=check)) as repo:
            store.require_current(repo)
            yield repo, file['name']

    def _register(self, db, dataset, name, path, baseline):
        old = db.execute('SELECT path,baseline FROM files WHERE dataset=?', (dataset,)).fetchone()
        if old and old[0] != str(path):
            raise ValueError('This file is already joined at another local path.')
        if old and old['baseline'] != baseline:
            raise ValueError('The registered file has a different original snapshot.')
        db.execute('INSERT INTO files(dataset,name,path,baseline) VALUES (?,?,?,?) '
                   'ON CONFLICT(dataset) DO UPDATE SET name=excluded.name', (dataset, name, str(path), baseline))

    def share(self, path, name=None):
        from . import peer_store as store
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Choose an existing Flow file to share.')
        if path == self.directory or self.directory in path.parents:
            raise ValueError('Choose a Flow file outside the private device settings directory.')
        name = _name(name or path.stem)
        with self._db() as db, closing(Repository(path)) as repo:
            db.execute('BEGIN IMMEDIATE')
            registered = db.execute('SELECT dataset FROM files WHERE path=?', (str(path),)).fetchone()
            previous = store.dataset(repo)
            if registered and registered[0] != previous:
                raise ValueError('Another shared file is registered at this location. Stop sharing it first.')
            dataset = store.share(repo, name)
            self._register(db, dataset, name, path, self._baseline(repo))
            return dataset

    def _remote(self, peer_id, op, *, dataset=None, baseline=None, body=None):
        from . import peer_store as store
        with self._db() as db:
            row = db.execute('SELECT token,address FROM peers WHERE id=?', (peer_id,)).fetchone()
            if not row:
                raise ValueError('Pair this device first.')
            port = int(_state(db, 'port') or 0)
            observed = db.execute('SELECT address FROM sightings WHERE id=? AND seen>?',
                                  (peer_id, _now()-30)).fetchone()
        header = dict(op=op, token=row['token'], id=self._identity, port=port,
                      protocol=store.PROTOCOL)
        if dataset is not None:
            header['dataset'] = dataset
        if baseline is not None:
            header['baseline'] = baseline
        if op == 'pull':
            header['inventory'] = _inventory_digest(body)
        targets = list(dict.fromkeys([row['address']] + ([observed[0]] if observed else [])))
        for index, endpoint in enumerate(targets):
            with self._db() as db:
                if not db.execute('SELECT 1 FROM peers WHERE id=? AND token=?',
                                  (peer_id, row['token'])).fetchone():
                    raise ValueError('Device was forgotten while this request was in progress.')
            try:
                _, result = wire.call(endpoint, peer_id, header, body)
            except (OSError, ValueError):
                if index == len(targets)-1:
                    raise
                continue
            with self._db() as db:
                saved = db.execute('UPDATE peers SET address=?,seen=? WHERE id=? AND token=?',
                                   (endpoint, _now(), peer_id, row['token']))
                if not saved.rowcount:
                    raise ValueError('Device was forgotten while this request was in progress.')
            return result

    def _catalog(self, peer_id):
        items = self._remote(peer_id, 'catalog')
        if not isinstance(items, list):
            raise ValueError('The peer sent an invalid file list.')
        result = []
        seen = set()
        for item in items:
            dataset = item.get('dataset') if isinstance(item, dict) else None
            if (not isinstance(dataset, str) or not 1 <= len(dataset) <= 100 or
                    not dataset.isprintable() or not dataset.strip() or dataset in seen):
                raise ValueError('The peer sent an invalid or duplicate file identity.')
            seen.add(dataset)
            result.append(dict(dataset=dataset, name=_name(item.get('name'))))
        return result

    def available(self):
        self._device()
        result, errors = [], []
        for peer in self._peer_snapshot():
            try:
                items = self._catalog(peer['peer_id'])
                for item in items:
                    result.append(dict(dataset=item['dataset'], name=item['name'],
                                       peer_id=peer['peer_id'], peer_name=peer['name']))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append((peer, f"{peer['name']}: {exc}"))
        self._save_peer_errors(errors)
        return result

    @staticmethod
    def _baseline(repo):
        from . import peer_store as store
        return store.genesis(repo)

    def _exchange(self, repo, dataset, peer_id, receiving_only=False, stop=None):
        from . import peer_store as store
        baseline = self._baseline(repo)
        received = sent = 0
        previous_remote = None
        def still_shared():
            if receiving_only:
                return True  # A new file is registered only after its first exchange.
            with self._db() as db:
                return db.execute('SELECT 1 FROM files WHERE dataset=? AND path=? AND baseline=?',
                                  (dataset, repo.path, baseline)).fetchone() is not None
        # Each page is committed locally before requesting the next page. Interrupted
        # sessions can resume from an arbitrary peer using the committed inventory.
        while True:
            if (stop is not None and stop.is_set()) or not still_shared():
                return received, sent
            known = list(store.inventory(repo))
            reply = self._remote(peer_id, 'pull', dataset=dataset, baseline=baseline, body=known)
            if reply.get('unchanged') is True:
                return received, sent
            events = reply['events']
            if not isinstance(events, list) or not isinstance(reply['known'], list):
                raise ValueError('Invalid peer inventory or events.')
            if events:
                if not store.receive(repo, events):
                    # A concurrent connection can legitimately have received the same page.
                    if set(store.inventory(repo)) == set(known):
                        raise ValueError('A peer returned an event page with no new events.')
                received += len(events)
            outgoing = [] if receiving_only else store.missing(repo, reply['known'])
            if outgoing:
                if not still_shared():
                    return received, sent
                remote = set(reply['known'])
                if previous_remote == remote:
                    raise ValueError('The peer acknowledged edits without recording them; synchronization stopped.')
                previous_remote = remote
                self._remote(peer_id, 'push', dataset=dataset, baseline=baseline, body=outgoing)
                sent += len(outgoing)
            if not events and not outgoing:
                return received, sent

    def join(self, dataset, peer_id, path):
        from . import peer_store as store
        self._device()
        path = Path(path).expanduser().absolute()
        if path.exists() or path.is_symlink():
            raise ValueError('Choose a new file name. Existing files are never overwritten.')
        if self.directory in path.resolve().parents:
            raise ValueError('Choose a file outside the private device settings directory.')
        with self._db() as db:
            if db.execute('SELECT 1 FROM files WHERE dataset=?', (dataset,)).fetchone():
                raise ValueError('This device already has a local copy of this file.')
        payload = self._remote(peer_id, 'seed', dataset=dataset)
        name = _name(payload['name'])
        if payload['seed']['dataset'] != dataset:
            raise ValueError('The other device sent a different file.')
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent, prefix='.flow-joining-') as tmp:
            staged = Path(tmp) / 'new.sqlite3'
            with closing(Repository(staged)) as repo:
                if store.join(repo, payload['seed']) != dataset:
                    raise ValueError('The shared file does not match the selected document.')
                self._exchange(repo, dataset, peer_id, receiving_only=True)
                baseline = self._baseline(repo)
            staged.chmod(0o600)
            installed = None
            try:
                with self._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._register(db, dataset, name, path.resolve(), baseline)
                    with path.open('xb') as reserved:
                        installed = os.fstat(reserved.fileno())
                    os.replace(staged, path)
                    installed = path.stat()
            except BaseException:
                if installed and path.exists() and os.path.samestat(installed, path.stat()):
                    path.unlink()
                raise
        return str(path)

    def sync_once(self):
        self._device()
        return self._sync_active(self._stopped)

    def _sync_active(self, stop):
        with self._sync_lock:
            files = {f['dataset']: f for f in self.local_files()}
            errors, received, sent, checked = [], 0, 0, 0
            for peer in self._peer_snapshot():
                if stop.is_set():
                    break
                try:
                    peer_id = peer['peer_id']
                    shared = self._catalog(peer_id)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    errors.append((peer, f"{peer['name']}: {exc}"))
                    continue
                for item in shared:
                    dataset = item['dataset']
                    if dataset not in files:
                        continue
                    try:
                        with self._open(dataset) as (repo, unused_name):
                            incoming, outgoing = self._exchange(repo, dataset, peer_id, stop=stop)
                        received += incoming
                        sent += outgoing
                        checked += 1
                    except (ValueError, RuntimeError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                        errors.append((peer, f"{peer['name']} / {files[dataset]['name']}: {exc}"))
            errors = self._save_peer_errors(errors, checked=True)
            return dict(received=received, sent=sent, checked=checked, errors=errors)
