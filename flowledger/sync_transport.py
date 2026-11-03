"""Small HTTPS transport for Flow's optional local-network synchronization.

The pairing text pins the coordinator's certificate and carries its access
token. SQLite is never used here, so callers can run these functions on a
network worker without moving a repository connection between threads.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import ssl
from urllib.parse import urlsplit

MAX_MESSAGE = 16 * 1024 * 1024


def parse_connection(text: str | dict) -> dict:
    try:
        data = json.loads(text) if isinstance(text, str) else dict(text)
        if not isinstance(data, dict):
            raise ValueError()
        endpoint = str(data['endpoint']).rstrip('/')
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username or
                url.password or url.path or url.query or url.fragment):
            raise ValueError()
        port = url.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        dataset = data['dataset']
        token = data['token']
        certificate = str(data['certificate']).lower()
        if (not isinstance(dataset, str) or not 1 <= len(dataset) <= 100 or
                not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token) or
                not re.fullmatch(r'[0-9a-f]{64}', certificate)):
            raise ValueError()
        return dict(endpoint=endpoint, dataset=dataset, token=token, certificate=certificate)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Paste the complete connection text from the Flow coordinator.') from exc


def _request(config: dict, path: str, payload: dict, *, timeout: float = 3.0) -> dict:
    config = parse_connection(config)
    url = urlsplit(config['endpoint'])
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(body) > MAX_MESSAGE:
        raise ValueError('This sync request is too large. Contact the coordinator administrator.')
    # A private coordinator normally has a self-signed certificate. Verify its
    # exact SHA-256 fingerprint BEFORE transmitting either credentials or data.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443,
                                             timeout=timeout, context=context)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, config['certificate']):
            raise ValueError('The coordinator certificate changed. Check the service before pairing again.')
        connection.request('POST', path, body, {
            'Authorization': 'Bearer ' + config['token'],
            'Content-Type': 'application/json',
            'Content-Length': str(len(body)),
            'Connection': 'close',
        })
        response = connection.getresponse()
        raw = response.read(MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE:
            raise ValueError('The coordinator response is too large.')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('The coordinator returned an invalid response.') from exc
        if not isinstance(result, dict):
            raise ValueError('The coordinator returned an invalid response.')
        if response.status != 200:
            raise ValueError(str(result.get('error', 'The coordinator rejected this request.')))
        return result
    except (OSError, http.client.HTTPException) as exc:
        raise ConnectionError('Coordinator unavailable. Changes remain saved on this device.') from exc
    finally:
        connection.close()


def fetch_bootstrap(config: dict) -> dict:
    """Return the coordinator's consistent seed for one explicitly joined file."""
    return _request(config, '/v1/bootstrap', {'dataset': config['dataset']})


def exchange(config: dict, request: dict) -> dict:
    """Send queued operations and receive ordered changes; no database access."""
    return _request(config, '/v1/exchange', request)
