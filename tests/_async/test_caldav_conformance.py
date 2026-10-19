# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The caldav backend passes the conformance suite against embedded Xandikos and Radicale."""

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
        return await AsyncBackend.connect(
            self.server.url,
            kind="caldav",
            username=self.server.username,
            password=self.server.password,
            features=self.server.config["features"],
        )


class TestXandikos(CalDAVConformance):
    @pytest.fixture(autouse=True)
    def _server(self, xandikos_server: Any) -> None:
        self.server = xandikos_server


class TestRadicale(CalDAVConformance):
    @pytest.fixture(autouse=True)
    def _server(self, radicale_server: Any) -> None:
        self.server = radicale_server
