"""Local, opt-in pairing. Discovery is only a hint; both people approve a
number bound to both TLS certificates and fresh session randomness. Only the
initiating device needs to make TCP connections. All state used by a GUI and
its background service is shared through the private device catalog.
"""
import hashlib
import hmac
import json
import re
import secrets
import ssl
import struct
import time

from . import peer_wire as wire


_HEARTBEAT = 90
_LIFE = 180
_DISCOVER = 8
_SCOPE = b'Flow nearby pairing 1\x00'
_RANDOM = re.compile('[0-9a-f]{64}')


def _now():
    return int(time.time())


def _hex(x):
    if not isinstance(x, str) or not _RANDOM.fullmatch(x):
        raise ValueError('Invalid pairing session.')
    return x


def _label(x):
    if not isinstance(x, str) or not 1 <= len(x) <= 200 or not x.strip() or not x.isprintable():
        raise ValueError('Invalid device name.')
    return x.strip()


def _hash(label, *parts):
    return hashlib.sha256(_SCOPE + label + b''.join(bytes.fromhex(_hex(p)) for p in parts)).hexdigest()


def _code(p):
    digest = _hash(b'compare', p['source'], p['target'], p['id'], p['a'], p['b'])
    return '%06d' % (int(digest[:16], 16) % 1000000)


def _get(db, key, default=None):
    r = db.execute('SELECT value FROM meta WHERE key=?', ('nearby_' + key,)).fetchone()
    return json.loads(r[0]) if r else default


def _set(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('nearby_' + key, json.dumps(value)))


def _active(db):
    return _get(db, 'open', 0) > _now()


def _pending(db):
    p = _get(db, 'pending')
    if p and p['expires'] > _now() and _active(db):
        return p
    return None


def _request(db, session, cap=None):
    p = _pending(db)
    if not p or p['id'] != session or (cap is not None and not hmac.compare_digest(p['cap'], _hex(cap))):
        raise wire.Refused('This pairing request is no longer active.')
    return p


def _context(node):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(node.directory / 'device-cert.pem', node.directory / 'device-key.pem')
    return context


def _frame(stream):
    n, = struct.unpack('!I', wire._exact(stream, 4))
    if not 0 < n <= 65536:
        raise ValueError('Invalid pairing proof.')
    return wire._exact(stream, n)


def _flush(stream, outgoing):
    data = outgoing.read()
    if data:
        stream.sendall(struct.pack('!I', len(data)) + data)


def exchange_proof(stream, context, expected, binding):
    """Prove the outbound client's key *on the same connection* by reversing TLS
    roles inside its already pinned TLS stream. TLS 1.2 here gives a bounded
    handshake with no post-handshake tickets. The inner application byte string
    explicitly binds this proof to both endpoints and this invitation.
    """
    server = context is not None
    if not server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    outgoing, incoming = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_side=server)
    for step in range(12):
        try:
            tls.do_handshake()
            break
        except ssl.SSLWantReadError:
            _flush(stream, outgoing)
            incoming.write(_frame(stream))
    else:
        raise ValueError('Incomplete device identity proof.')
    _flush(stream, outgoing)
    if server:
        tls.write(binding)
        _flush(stream, outgoing)
        return
    actual = wire.pin(tls.getpeercert(binary_form=True))
    if not hmac.compare_digest(actual, expected):
        raise wire.Refused('The device could not prove its claimed identity.')
    for step in range(3):
        try:
            if not hmac.compare_digest(tls.read(256), binding):
                raise wire.Refused('Pairing proof belongs to a different request.')
            return
        except ssl.SSLWantReadError:
            incoming.write(_frame(stream))
    raise ValueError('Missing device identity proof.')


def _bind(source, target, session, commitment):
    return bytes.fromhex(_hash(b'proof', source, target, session, commitment))


class NearbyPairing:
    def _near_state(self, message=''):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            opened = _active(db)
            p = _pending(db)
            candidates = _get(db, 'candidates', {})
            known = {r[0] for r in db.execute('SELECT id FROM peers')}
            devices = [dict(peer_id=k, name=d['name'], address=d['address'])
                       for k, d in candidates.items() if d['seen'] > _now()-30 and k not in known] if opened else []
            devices.sort(key=lambda d: (d['name'].casefold(), d['peer_id']))
            request = None
            if p and p.get('number'):
                request = {k:p[k] for k in ('id','name','peer_id','number','approved','direction')}
            complete = _get(db, 'completed', [])
            if complete:
                _set(db, 'completed', [])
            return dict(open=opened, devices=devices, request=request, paired=complete, message=message)

    def nearby_open(self):
        self._device()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM meta WHERE key='nearby_listener' AND value='1'").fetchone():
                raise ValueError('Restart other Flow windows or flow-peer.service on this computer, then try again.')
            if not _active(db):
                _set(db, 'pending', None)
                _set(db, 'candidates', {})
            _set(db, 'open', _now()+_HEARTBEAT)
        return self._near_state()

    def nearby_close(self):
        return self._near_cancel(None, True)

    def nearby_cancel(self, request_id):
        return self._near_cancel(_hex(request_id), False)

    def _near_cancel(self, session, closing):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            p = _pending(db)
            if not closing and (not p or p['id'] != session):
                raise ValueError('This pairing request is no longer active.')
            _set(db, 'pending', None)
            if closing:
                _set(db, 'open', 0)
                _set(db, 'candidates', {})
        if p and p.get('cap') and p['direction'] == 'outgoing':
            try:
                wire.call(p['address'], p['peer_id'], dict(op='nearby_cancel',sid=p['id'],cap=p['cap']))
            except (ValueError,OSError):
                pass
        return self._near_state()

    def _near_hello(self, endpoint, expected=None):
        # Manual lookup is deliberately allowed only this public, fixed query
        # before the peer certificate is pinned. No file or secret is sent.
        remote, result = wire.call(endpoint, expected, dict(op='nearby_hello'))
        if remote != self._identity:
            name = _label(result['name'])
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not _active(db):
                    return
                options = _get(db, 'candidates', {})
                options = {k:v for k,v in options.items() if v['seen'] > _now()-30}
                if len(options) < 32 or remote in options:
                    options[remote] = dict(name=name, address=endpoint, seen=_now())
                    _set(db, 'candidates', options)
            return remote
        raise ValueError('Choose a different device.')

    def nearby_lookup(self, address):
        wire.parse_address(address)
        self._device()
        with self._db() as db:
            if not _active(db):
                raise ValueError('Open nearby pairing first.')
        self._near_hello(address.strip())
        return self._near_state()

    def nearby_poll(self):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            active = _active(db)
            p = _pending(db)
            targets=[]
            if active:
                _set(db, 'open', _now()+_HEARTBEAT)
                if not p:
                    sightings=db.execute('SELECT id,address FROM sightings WHERE seen>? '
                                         'AND id NOT IN (SELECT id FROM peers) ORDER BY seen DESC LIMIT 32',(_now()-30,))
                    last=_get(db,'probed',{})
                    last={k:v for k,v in last.items() if v>_now()-30}
                    for identity, endpoint in sightings:
                        if identity not in last or last[identity]<=_now()-_DISCOVER:
                            targets.append((identity,endpoint))
                            last[identity]=_now()
                            if len(targets)==2:
                                break
                    _set(db,'probed',last)
        message=''
        if active and p and p['direction']=='outgoing' and p.get('number'):
            try:
                self._near_progress(p)
            except wire.Refused as exc:
                message=str(exc)
            except OSError:
                message='Waiting for the other device to reconnect.'
        for identity,endpoint in targets:
            try:
                self._near_hello(endpoint, identity)
            except wire.Refused:
                with self._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    options = _get(db, 'candidates', {})
                    if options.pop(identity, None):
                        _set(db, 'candidates', options)
            except (ValueError, OSError, KeyError, TypeError):
                pass
        return self._near_state(message)

    def nearby_request(self, peer_id):
        dev = self._device()
        sid, a = secrets.token_hex(32), secrets.token_hex(32)
        commitment = _hash(b'commit', dev['id'], peer_id, sid, a)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _active(db) or _pending(db):
                raise ValueError('Finish or cancel the current pairing request.')
            candidate = _get(db,'candidates',{}).get(peer_id)
            if not candidate or candidate['seen'] < _now()-30:
                raise ValueError('Refresh nearby devices and select this device again.')
            p=dict(id=sid, direction='outgoing', peer_id=peer_id, source=dev['id'], target=peer_id,
                   name=candidate['name'], address=candidate['address'], a=a, approved=False, expires=_now()+_LIFE)
            _set(db,'pending',p)
        try:
            _, r = wire.call(p['address'],peer_id,dict(op='nearby_begin',device=dev,sid=sid,commit=commitment),
                             key_proof=(_context(self), _bind(dev['id'],peer_id,sid,commitment)))
            p.update(b=_hex(r['nonce']), cap=_hex(r['cap']))
            p['number']=_code(p)
            wire.call(p['address'],peer_id,dict(op='nearby_reveal',sid=sid,cap=p['cap'],nonce=a))
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                _request(db,sid)
                _set(db,'pending',p)
        except Exception:
            if p.get('cap'):
                try:
                    wire.call(p['address'], peer_id, dict(op='nearby_cancel', sid=sid, cap=p['cap']))
                except (OSError, ValueError):
                    pass
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if (_get(db,'pending') or {}).get('id')==sid:
                    _set(db,'pending',None)
            raise
        return self._near_state()

    def nearby_approve(self, request_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            p = _request(db,_hex(request_id))
            if not p.get('number'):
                raise ValueError('Wait for the verification number.')
            p['approved']=True
            _set(db,'pending',p)
        if p['direction']=='outgoing':
            self._near_progress(p)
        return self._near_state()

    def _near_progress(self,p):
        _, r = wire.call(p['address'],p['peer_id'],dict(op='nearby_step',sid=p['id'],cap=p['cap'],approved=p['approved']))
        if r.get('state')=='waiting':
            return
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            fresh=_request(db,p['id'])
            if r.get('state')=='gone':
                _set(db,'pending',None)
                raise_after=True
            elif r.get('state')=='paired' and fresh['approved']:
                token=_hex(r['token'])
                prior = db.execute('SELECT token FROM peers WHERE id=?', (p['peer_id'],)).fetchone()
                if prior and not hmac.compare_digest(prior[0], token):
                    raise ValueError('This device was paired separately. Cancel the duplicate request.')
                if not prior:
                    db.execute('INSERT INTO peers VALUES (?,?,?,?,?)',
                               (p['peer_id'], p['name'], p['address'], token, _now()))
                _set(db,'pending',None)
                _set(db,'completed',[p['peer_id']])
                raise_after=False
            else:
                raise ValueError('Invalid pairing response.')
        if raise_after:
            raise wire.Refused('The other device closed, canceled, or let this request expire.')

    def _nearby_handle(self,hdr,stream,host):
        op=hdr['op']
        if op=='nearby_hello':
            with self._db() as db:
                if not _active(db):
                    raise wire.Refused('Open Nearby devices in Flow on the other device first.')
                return {'name': db.execute("SELECT value FROM meta WHERE key='name'").fetchone()[0]}
        sid=_hex(hdr['sid'])
        if op=='nearby_begin':
            dev=hdr['device']
            source, target, port=dev['id'], self._identity, dev['port']
            name=_label(dev['name'])
            if not wire.is_id(source) or source==target or type(port) is not int or not 0<port<=65535:
                raise ValueError('Invalid pairing source.')
            commitment=_hex(hdr['commit'])
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not _active(db) or _pending(db):
                    raise wire.Refused('Other device is not ready. Open Nearby devices and finish or cancel any pending request.')
                if db.execute('SELECT 1 FROM peers WHERE id=?',(source,)).fetchone():
                    raise wire.Refused('These devices are already paired.')
                attempts=[x for x in _get(db,'attempts',[]) if x>_now()-120]
                if len(attempts)>=8:
                    raise wire.Refused('Too many pairing requests. Try again in two minutes.')
                _set(db,'attempts',attempts+[_now()])
            stream.settimeout(5)
            wire.write(stream, {'ready':True}, wire.HEADER)
            exchange_proof(stream,None,source,_bind(source,target,sid,commitment))
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not _active(db) or _pending(db):
                    raise wire.Refused('Pairing window is busy or closed.')
                p=dict(id=sid,source=source,target=target,peer_id=source,name=name,address=wire.address(host,port),
                       commit=commitment,b=secrets.token_hex(32),cap=secrets.token_hex(32),approved=False,
                       direction='incoming',expires=_now()+15)
                _set(db,'pending',p)
            return dict(nonce=p['b'],cap=p['cap'])
        cap=_hex(hdr['cap'])
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if op=='nearby_step':
                done=_get(db,'receipt')
                if done and done['id']==sid and done['until']>_now() and hmac.compare_digest(done['cap'],cap):
                    row=db.execute('SELECT token FROM peers WHERE id=?',(done['peer_id'],)).fetchone()
                    if row and hmac.compare_digest(done['digest'],hashlib.sha256(row[0].encode()).hexdigest()):
                        return dict(state='paired',token=row[0])
            p=_pending(db)
            if not p or p['id']!=sid or p['direction']!='incoming' or not hmac.compare_digest(p['cap'],cap):
                if op=='nearby_step':return dict(state='gone')
                raise PermissionError()
            if op=='nearby_reveal':
                a=_hex(hdr['nonce'])
                if not hmac.compare_digest(_hash(b'commit',p['source'],p['target'],sid,a),p['commit']):
                    raise PermissionError()
                if 'number' not in p:
                    p['expires'] = _now() + _LIFE
                p['a']=a
                p['number']=_code(p)
                _set(db,'pending',p)
                return True
            if op=='nearby_cancel':
                _set(db,'pending',None)
                return True
            if op!='nearby_step' or type(hdr.get('approved')) is not bool:
                raise ValueError('Unknown pairing action.')
            if not (p.get('number') and p['approved'] and hdr['approved']):
                return dict(state='waiting')
            token=secrets.token_hex(32)
            db.execute('INSERT INTO peers VALUES (?,?,?,?,?)',(p['peer_id'],p['name'],p['address'],token,_now()))
            _set(db,'pending',None)
            _set(db,'completed',[p['peer_id']])
            _set(db,'receipt',dict(id=sid,cap=cap,peer_id=p['peer_id'],digest=hashlib.sha256(token.encode()).hexdigest(),until=_now()+_LIFE))
            return dict(state='paired',token=token)
