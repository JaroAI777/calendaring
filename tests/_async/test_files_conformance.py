# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The files backend passes the conformance suite in each layout (roadmap 2.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from calendaring import AsyncBackend
from calendaring.testing import AsyncConformance


class FilesConformance(AsyncConformance):
    path: Path

    async def open_backend(self) -> AsyncBackend:
        return await AsyncBackend.open(str(self.path))


class TestVdirRoot(FilesConformance):
    """A directory of collections, one subdirectory each, as vdirsyncer writes them."""

    @pytest.fixture(autouse=True)
    def _path(self, tmp_path: Path) -> None:
        self.path = tmp_path / "calendars"
        self.path.mkdir()


class TestVdirCollection(FilesConformance):
    """One collection's directory, as khal's ``path =`` names it."""

    @pytest.fixture(autouse=True)
    def _path(self, tmp_path: Path) -> None:
        self.path = tmp_path / "work"
        self.path.mkdir()
        (self.path / "displayname").write_text("Work\n")


class TestSingleFile(FilesConformance):
    """One ``.ics`` file holding every object, created on the first write."""

    @pytest.fixture(autouse=True)
    def _path(self, tmp_path: Path) -> None:
        self.path = tmp_path / "calendar.ics"
