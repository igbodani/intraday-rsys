"""Python network tripwire for the single-threaded provider-validation simulation.

This blocks Python socket connections and DNS in the scope; it is not an OS
sandbox for arbitrary native extensions or subprocesses. The supported runners
use pinned local files only. Do not use this process-wide scope concurrently.
"""

import socket
from contextlib import contextmanager


@contextmanager
def deny_python_network():
    def denied(*args, **kwargs):
        raise RuntimeError("network access is disabled during offline backtest")

    targets = [
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ]
    originals = [(owner, name, getattr(owner, name)) for owner, name in targets]
    try:
        for owner, name in targets:
            setattr(owner, name, denied)
        yield
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)
