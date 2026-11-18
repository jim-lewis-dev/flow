"""Discovery must release its catalog read before storing network results."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from flowledger import peer_nearby, peer_wire
from flowledger.peer_network import PeerNode


class PeerDatabaseTests(unittest.TestCase):
    def test_poll_releases_unconsumed_sightings_before_writing_candidates(self):
        # Three sightings reproduce this without timing, threads or a LAN:
        # the poll processes only two and must not leave its SELECT open.
        with tempfile.TemporaryDirectory() as root:
            node = PeerNode(Path(root) / 'device', discovery=False)

            @contextmanager
            def short_timeout_db():
                # Real rollback-journal SQLite, with a short timeout so the
                # regression fails promptly instead of hanging for ten seconds.
                with closing(sqlite3.connect(node.directory / 'devices.sqlite3', timeout=.1)) as db:
                    db.row_factory = sqlite3.Row
                    with db:
                        yield db

            node._db = short_timeout_db
            with node._db() as db:
                peer_nearby._set(db, 'open', int(time.time()) + 90)
                db.executemany('INSERT INTO sightings VALUES (?,?,?)', [
                    (str(i) * 64, '127.0.0.1:' + str(40000 + i), int(time.time()))
                    for i in range(1, 4)
                ])

            def hello(endpoint, expected, header):
                self.assertEqual(header, {'op': 'nearby_hello'})
                return expected, {'name': 'Peer ' + expected[0]}

            with patch.object(peer_wire, 'call', side_effect=hello) as calls:
                first = node.nearby_poll()
                self.assertEqual(len(first['devices']), 2)
                self.assertEqual(calls.call_count, 2)
                second = node.nearby_poll()
                self.assertEqual({d['name'] for d in second['devices']}, {'Peer 1', 'Peer 2', 'Peer 3'})
                self.assertEqual(calls.call_count, 3)

