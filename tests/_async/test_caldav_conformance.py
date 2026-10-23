# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The caldav backend passes the conformance suite on every CalDAV server ``caldav_server`` gives (see conftest)."""

from __future__ import annotations

from typing import Any

import pytest

from calendaring import AsyncBackend
from calendaring.testing import AsyncConformance


class CalDAVConformance(AsyncConformance):
    # A CalDAV server is probed, not known in advance (§5.1).
    allow_unknown = True
    server: Any

    async def open_backend(self) -> AsyncBackend:
        return await AsyncBackend.open(self.server.url, kind="caldav", **self.server.options)


class TestCalDAVServers(CalDAVConformance):
    @pytest.fixture(autouse=True)
    def _server(self, caldav_server: Any) -> None:
        self.server = caldav_server
