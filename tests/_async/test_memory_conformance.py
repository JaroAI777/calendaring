# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The in-memory reference backend passes the conformance suite, in four profiles.

A backend that can do everything, one shaped like an issue tracker (tasks
only, mapped statuses and priorities, assigned identity), a writable one
without most optional features, and a read-only feed.  Between them they run
both branches of the capability-aware tests, except the supported branch of
``recurrence.edit-occurrence`` and ``task.complete-recurring``, which wait for
the merge in recurring-ical-events (API_DESIGN.md Q3).
"""

from __future__ import annotations

from collections.abc import Sequence

from calendaring import AsyncBackend, AsyncCollection, AsyncItem, Item, Task
from calendaring.memory import FEED, FULL, MINIMAL, TRACKER, AsyncMemoryBackend
from calendaring.testing import AsyncConformance


class TestFullProfile(AsyncConformance):
    async def open_backend(self) -> AsyncBackend:
        return AsyncMemoryBackend(profile=FULL)


class TestTrackerProfile(AsyncConformance):
    async def open_backend(self) -> AsyncBackend:
        return AsyncMemoryBackend(profile=TRACKER)


class TestMinimalProfile(AsyncConformance):
    """It cannot make collections, so the suite borrows one that already holds something."""

    async def open_backend(self) -> AsyncBackend:
        backend = AsyncMemoryBackend(profile=MINIMAL)
        backend.store.calendars["default"].put(Task.new(summary="not the suite's").to_ical())
        return backend

    async def test_the_borrowed_collection_is_left_as_it_was(self) -> None:
        backend = await self.open_backend()
        self._made, self._borrowed = [], []
        collection = await self.open_collection(backend)
        before = {i.native_id for i in await collection.search()}
        await collection.add_task(summary="the suite's")
        await self.tidy_up(backend)
        assert {i.native_id for i in await collection.search()} == before


class TestFeedProfile(AsyncConformance):
    async def open_backend(self) -> AsyncBackend:
        return AsyncMemoryBackend(profile=FEED)

    async def seed(self, collection: AsyncCollection, items: Sequence[Item]) -> list[AsyncItem]:
        assert isinstance(collection, AsyncMemoryBackend.collection_class)
        native_ids = [collection.store.put(item.to_ical()) for item in items]
        return [await collection.get_by_native_id(n) for n in native_ids]
