"""The async files backend does its file I/O in worker threads, so an event loop is never held up (roadmap 2.2)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from calendaring import AsyncBackend, Backend, Component
from calendaring._files_store import Vdir


def record_threads(monkeypatch: pytest.MonkeyPatch) -> list[threading.Thread]:
    seen: list[threading.Thread] = []
    for name in ("entries", "create", "etags"):
        original = getattr(Vdir, name)

        def spy(self: Vdir, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
            seen.append(threading.current_thread())
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Vdir, name, spy)
    return seen


async def test_the_async_mode_reads_and_writes_in_worker_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = record_threads(monkeypatch)
    backend = await AsyncBackend.open(str(tmp_path))
    collection = await backend.create_collection("calendar", components=[Component.TASK])
    await collection.add_task(summary="x")
    await collection.tasks()
    await collection.changes()
    assert seen
    assert threading.main_thread() not in seen


def test_the_sync_mode_does_its_io_where_it_is_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = record_threads(monkeypatch)
    collection = Backend.open(str(tmp_path)).create_collection("calendar", components=[Component.TASK])
    collection.add_task(summary="x")
    collection.tasks()
    assert set(seen) == {threading.main_thread()}
