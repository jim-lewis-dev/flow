"""Run a trusted Flow peer without opening a desktop window."""
import argparse
import signal
import threading

from .peer_network import PeerNode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Device configuration directory (default: same as Flow)')
    args = parser.parse_args(argv)
    stopped = threading.Event()
    node = PeerNode(args.config)
    previous = {sig: signal.signal(sig, lambda *_: stopped.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        node.start()
        print('Flow peer is running.', flush=True)
        stopped.wait()
    finally:
        node.stop()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    main()
