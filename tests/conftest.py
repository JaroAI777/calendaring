"""Embedded CalDAV servers for the caldav backend's conformance tests (roadmap 2.1).

Each is started once per session, and skipped when it is not installed
(``pip install -e .[caldav,test-servers]``).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


def serve(module: str, factory: str, config: dict[str, Any]) -> Iterator[Any]:
    pytest.importorskip(module)
    testing = pytest.importorskip("caldav.testing")
    server = getattr(testing, factory)(config)
    server.start()
    yield server
    # stop() leaves these open (RadicaleServer one end of its socketpair,
    # XandikosServer its thread's event loop): https://github.com/python-caldav/caldav/issues/797
    leftovers = [getattr(server, name, None) for name in ("shutdown_socket_out", "xapp_loop")]
    server.stop()
    for leftover in leftovers:
        if leftover is not None:
            leftover.close()


@pytest.fixture(scope="session")
def xandikos_server() -> Iterator[Any]:
    yield from serve("xandikos", "XandikosServer", {"port": free_port()})


@pytest.fixture(scope="session")
def radicale_server() -> Iterator[Any]:
    # RadicaleServer does not pass its port on to Radicale, which then
    # listens on its default, 5232: https://github.com/python-caldav/caldav/issues/797
    yield from serve("radicale", "RadicaleServer", {})
