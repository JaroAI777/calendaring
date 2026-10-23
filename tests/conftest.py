"""CalDAV servers for the caldav backend's tests (roadmap 2.1, 3.2).

Embedded Xandikos and Radicale are started once per session, and skipped
when they are not installed (``pip install -e .[caldav,test-servers]``).

``--caldav-servers=Baikal,Nextcloud`` (or ``all``) adds the docker and
external servers of caldav's own test framework.  That framework is not
in caldav's wheel, so it is loaded from a caldav source checkout:
``$CALDAV_SOURCE``, or the one an editable caldav install points at.
Servers are never started here: start them in the checkout
(``tests/docker-test-servers/<name>/start.sh``) before the run.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import socket
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest

EMBEDDED = ["Xandikos", "Radicale"]


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


# --- caldav's test servers ----------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--caldav-servers",
        default="",
        metavar="NAMES",
        help="also test against caldav's test servers: comma-separated names (Baikal,Nextcloud,...) "
        "or 'all'; needs a caldav source checkout ($CALDAV_SOURCE)",
    )


def caldav_checkout() -> Path:
    if "CALDAV_SOURCE" in os.environ:
        return Path(os.environ["CALDAV_SOURCE"]).expanduser()
    import caldav

    return Path(caldav.__file__).parent.parent


def caldav_test_framework() -> ModuleType:
    """caldav's ``tests/test_servers`` package, imported under a name that cannot clash with ours.

    A path import, until caldav ships it: https://github.com/python-caldav/caldav/issues/817
    """
    name = "caldav_test_servers"
    if name in sys.modules:
        return sys.modules[name]
    checkout = caldav_checkout()
    package = checkout / "tests" / "test_servers"
    if not (package / "__init__.py").exists():
        raise pytest.UsageError(
            f"--caldav-servers needs a caldav source checkout; none at {checkout} (set CALDAV_SOURCE)"
        )
    spec = importlib.util.spec_from_file_location(
        name, package / "__init__.py", submodule_search_locations=[str(package)]
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def caldav_test_servers(config: pytest.Config) -> dict[str, Any]:
    """The servers ``--caldav-servers`` asked for, by name; the embedded ones are left to the fixtures above."""
    wanted = config.getoption("--caldav-servers")
    if not wanted:
        return {}
    framework = caldav_test_framework()
    # caldav reads tests/caldav_test_servers.yaml relative to the working directory.
    with contextlib.chdir(caldav_checkout()):
        registry = framework.get_registry()
    # caldav takes the first config source it finds; add the test_allowed sections of the
    # caldav configuration even when a test-server file exists (caldav issue 817).
    from caldav.config import get_all_test_servers

    allowed = get_all_test_servers()
    known = {s.name.lower() for s in registry.all_servers()}
    registry.load_from_config(
        {n: {"type": "external", "enabled": True, **c} for n, c in allowed.items() if n.lower() not in known}
    )
    registered = registry.enabled_servers()
    available = {s.name: s for s in registered if not isinstance(s, framework.EmbeddedTestServer)}
    if wanted == "all":
        return available
    names = [n.strip() for n in wanted.split(",") if n.strip()]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise pytest.UsageError(
            f"--caldav-servers: {unknown} unknown, disabled, or not running with no docker to start it; "
            f"available: {sorted(available)}"
        )
    return {n: available[n] for n in names}


SERVERS = pytest.StashKey[dict[str, Any]]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[SERVERS] = caldav_test_servers(config)


def caldav_server_names(config: pytest.Config) -> list[str]:
    return [*EMBEDDED, *config.stash[SERVERS]]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Every test taking ``caldav_server`` runs once per server, with the server's name as its id."""
    if "caldav_server" in metafunc.fixturenames:
        metafunc.parametrize("caldav_server", caldav_server_names(metafunc.config), indirect=True, scope="session")


@dataclass
class CalDAVServer:
    """A running server, and what ``Backend.open(url, kind="caldav", **options)`` needs to reach it."""

    name: str
    url: str
    options: dict[str, Any]


def reachable(url: str) -> bool:
    """Whether anything answers at the URL's host and port; caldav takes an external server's word for it."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.create_connection((parts.hostname, port), timeout=5).close()
    except OSError:
        return False
    return True


def running_caldav_server(request: pytest.FixtureRequest, name: str) -> CalDAVServer:
    """The server by name; a docker or external one is never started, only checked to be up."""
    if name in EMBEDDED:
        server = request.getfixturevalue(f"{name.lower()}_server")
    else:
        server = request.config.stash[SERVERS][name]
        server.claim()  # waits while a caldav test run is using the same server
        # An external server without a URL gets one from its auto-connect hints, in caldav.
        if not (server.is_accessible() and (not server.url or reachable(server.url))):
            message = f"{name} is not running at {server.url}; start it from the caldav checkout first"
            if request.config.getoption("--caldav-servers") == "all":
                pytest.skip(message)
            pytest.fail(message)
    options = {"username": server.username, "password": server.password, "features": server.config.get("features")}
    if "ssl_verify_cert" in server.config:
        options["ssl_verify_cert"] = server.config["ssl_verify_cert"]
    return CalDAVServer(name, server.url, options)


@pytest.fixture(scope="session")
def caldav_server(request: pytest.FixtureRequest) -> CalDAVServer:
    return running_caldav_server(request, request.param)
