"""Foreground peer networking. No UI SQLite connections cross worker threads."""
from contextlib import closing
import sqlite3
import uuid
import json
import re
from pathlib import Path
import threading


def shared_path(root, dataset, filename):
    from .bridge import dataset_path
    token = uuid.UUID(dataset).hex
    folder = root / '.shared-files' / token
    try:
        return dataset_path(folder, filename)
    except ValueError:
        # A display name chosen on another platform need not be a legal local
        # filename. Identity is carried by the directory, never by that name.
        return folder / 'ledger.sqlite3'


def file_label(root, path):
    # Most labels are already the complete portable filename. If it could
    # not be represented as a filename, get it from the ledger itself.
    if path.name == 'ledger.sqlite3' and path.parent.parent == root / '.shared-files':
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                row = db.execute("SELECT json_extract(payload,'$.name') FROM peer_seed WHERE singleton=1").fetchone()
                if row and isinstance(row[0], str) and row[0].strip():
                    return row[0] if row[0].endswith('.sqlite3') else row[0] + '.sqlite3'
        except sqlite3.Error:
            pass
    return path.name


def file_key(root, path):
    path = Path(path)
    if path.parent == root:
        return path.name
    if path.parent.parent == root / '.shared-files' and re.fullmatch('[0-9a-f]{32}', path.parent.name):
        return 'shared:' + path.parent.name + '/' + path.name
    raise ValueError('File is outside the phone storage locations.')


def key_path(root, key):
    from .bridge import dataset_path
    match = re.fullmatch(r'shared:([0-9a-f]{32})/([^/]+)', key or '')
    if match:
        return dataset_path(root / '.shared-files' / match.group(1), match.group(2))
    return dataset_path(root, key)



def _platform():
    try:
        from java import jclass
    except ImportError:  # Desktop preview/test harness
        return None
    return jclass('dev.jimlewis.flow.PeerHost')


class PhonePeers:
    def __init__(self, root):
        self.directory = Path(root) / '.peer-device'
        self._node = None
        self._lock = threading.RLock()
        self._foreground = True
        self._nearby_open = False
        self.error = ''

    def _load(self):
        with self._lock:
            if self._node is None:
                from flowledger.peer_network import PeerNode
                host = _platform()
                options = {}
                if host is not None:
                    options = dict(name=str(host.deviceName()), cert_factory=lambda c, k:
                                   host.createIdentity(str(c), str(k)))
                self._node = PeerNode(self.directory, **options)
            return self._node

    def node(self):
        with self._lock:
            if not self._foreground:
                raise ValueError('Return to Flow to connect devices.')
            node = self._load()
            platform = _platform()
            if platform is not None:
                platform.discovery(True)
            try:
                node.start()
                self.error = ''
            except Exception as exc:
                if platform is not None:
                    platform.discovery(False)
                self.error = str(exc)
                raise
            return node

    def foreground(self, active):
        with self._lock:
            self._foreground = active
            if active:
                if (self.directory / 'devices.sqlite3').is_file():
                    self.node()
            else:
                try:
                    if self._node is not None:
                        try:
                            if self._nearby_open:
                                self._node.nearby_close()
                        finally:
                            self._nearby_open = False
                            self._node.stop()
                finally:
                    host = _platform()
                    if host is not None:
                        host.discovery(False)

    def status(self):
        # Reading state on a standalone phone must not create a listener,
        # device identity, catalog or network permission request.
        if not (self.directory / 'devices.sqlite3').is_file() and not self._node:
            return {'enabled': False, 'peers': [], 'files': [], 'running': False}
        s = self._load().status()
        return {**s, 'enabled': True, 'error': self.error or s.get('error', ''),
                'files': [{'dataset': f['dataset'], 'name': f['name'], 'key': file_key(self.directory.parent, f['path'])}
                          for f in s.get('files', [])]}

    def _nearby(self, mode, args):
        # Use the existing network executor, never the UI/file executor.
        # Close also works after onStop and must not restart a stopped node.
        with self._lock:
            if mode == 'close':
                if self._nearby_open and self._node:
                    self._node.nearby_close()
                self._nearby_open = False
                return {'nearby': {'open': False, 'devices': [], 'request': None, 'paired': []}}
            if mode == 'open':
                node = self.node()
                view = node.nearby_open()
                self._nearby_open = view['open']
                return {'nearby': view, 'device': self.status()}
            if not self._foreground or not self._nearby_open:
                raise ValueError('Open nearby pairing on both devices first.')
            node = self._node
        # A slow lookup must not block lifecycle or local ledger status.
        if mode == 'poll':
            view = node.nearby_poll()
        elif mode == 'request':
            view = node.nearby_request(args['peer_id'])
        elif mode == 'approve':
            view = node.nearby_approve(args['request_id'])
        elif mode == 'cancel':
            view = node.nearby_cancel(args['request_id'])
        elif mode == 'lookup':
            view = node.nearby_lookup(args['address'])
        else:
            raise ValueError('Unknown nearby device action.')
        return {'nearby': view, 'device': self.status()}

    def call(self, action, args):
        if action == 'peer_pair':
            return self._nearby(args.get('mode'), args)
        if action == 'peer_forget':
            # Forget is a local operation, even if networking cannot start.
            with self._lock:
                self._load().forget(args['peer_id'])
                self.error = ''
                return {'device': self.status()}
        node = self.node()
        if action == 'peer_catalog':
            return {'catalog': node.available(), 'device': self.status()}
        if action == 'peer_join':
            root = self.directory.parent
            target = shared_path(root, args['dataset'], args.get('name', ''))
            # The same basename can belong to any number of datasets. A
            # specific dataset, including its stopped-sharing recovery copy,
            # can only occupy its own namespace once.
            if list(target.parent.glob('*.sqlite3')):
                raise ValueError('A local copy of this shared dataset already exists. Open it under Your files.')
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            node.join(args['dataset'], args['peer_id'], str(target))
            return {'name': file_key(root, target)}
        raise ValueError('Unknown device action.')


_services = {}
_services_lock = threading.Lock()


def service(root):
    key = str(Path(root).resolve())
    with _services_lock:
        if key not in _services:
            _services[key] = PhonePeers(key)
        return _services[key]


def lifecycle(root, active):
    try:
        service(root).foreground(bool(active))
    except Exception as exc:
        service(root).error = str(exc)


def network(root, action, payload):
    try:
        return json.dumps({'ok': True, 'result': service(root).call(action, json.loads(payload))})
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})
