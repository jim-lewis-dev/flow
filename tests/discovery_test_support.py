"""Keep real discovery tests separate from running Flow installations."""
import socket
import uuid
from unittest.mock import patch

from flowledger import peer_wire


PRODUCTION_MAGIC = peer_wire.MAGIC


def isolate_discovery(test):
    # Reserve an OS-selected port until every test listener has stopped. Test
    # nodes use loopback; a distinct prefix also rejects unrelated datagrams.
    while True:
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reservation.bind(('', 0))
            port = reservation.getsockname()[1]
            if port != 47622:
                break
        except BaseException:
            reservation.close()
            raise
        reservation.close()
    test.addCleanup(reservation.close)
    for name, value in (('DISCOVERY_PORT', port),
                        ('MAGIC', b'FLOW-TEST/' + uuid.uuid4().hex.encode('ascii') + b' ')):
        change = patch.object(peer_wire, name, value)
        change.start()
        test.addCleanup(change.stop)
