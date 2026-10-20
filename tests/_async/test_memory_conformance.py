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

import pytest
from icalendar_searcher import Searcher

from calendaring import AsyncBackend, AsyncCollection, AsyncItem, Feature, Item, Support, Task
from calendaring.memory import (
    FEED,
    FULL,
    MINIMAL,
    TRACKER,
    AsyncMemoryBackend,
    AsyncMemoryCollection,
    MemoryObject,
    MemoryStore,
    Profile,
)
from calendaring.testing import AsyncConformance
from calendaring.types import StoredObject


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
        backend = AsyncMemoryBackend(profile=FEED)
        # A feed is read-only as a whole, which a backend may say as well (§5.2).
        backend.capabilities = backend.capabilities.updated({Feature.WRITE: Support.UNSUPPORTED})
        return backend

    async def seed(self, collection: AsyncCollection, items: Sequence[Item]) -> list[AsyncItem]:
        assert isinstance(collection, AsyncMemoryBackend.collection_class)
        native_ids = [collection.store.put(item.to_ical()) for item in items]
        return [await collection.get_by_native_id(n) for n in native_ids]


def sharing(profile: Profile, store: MemoryStore | None = None) -> AsyncMemoryBackend:
    """A backend on ``store``, like a second connection to a server that keeps its data."""
    backend = AsyncMemoryBackend(profile=profile)
    if store is not None:
        backend.store = backend.native = store
    return backend


class TestTidyingUp:
    """What the suite leaves behind on a backend that keeps data between tests."""

    def suite(self) -> AsyncConformance:
        suite = AsyncConformance()
        suite._made, suite._borrowed = [], []
        return suite

    async def test_a_concurrent_write_to_a_borrowed_collection_survives(self) -> None:
        backend = sharing(MINIMAL)
        suite = self.suite()
        collection = await suite.open_collection(backend)
        await collection.add_task(summary="the suite's")
        [shared] = await sharing(MINIMAL, backend.store).collections()
        await shared.add_task(summary="the user's, from their phone")
        await suite.tidy_up(backend)
        assert [str(i.component["SUMMARY"]) for i in await shared.search()] == ["the user's, from their phone"]

    async def test_a_borrowed_collection_keeps_its_name(self) -> None:
        backend = sharing(MINIMAL)
        suite = self.suite()
        await suite.test_set_name(backend, await suite.open_collection(backend))
        await suite.tidy_up(backend)
        assert (await backend.collection("default")).name == "Default"

    async def test_without_delete_collection_one_collection_is_reused(self) -> None:
        store = sharing(TRACKER).store
        for _ in range(3):
            backend = sharing(TRACKER, store)
            suite = self.suite()
            collection = await suite.open_collection(backend)
            await collection.add_task(summary="the suite's")
            await suite.test_create_and_delete_collection(backend)
            await suite.tidy_up(backend)
        assert sorted(store.calendars) == ["conformance", "conformance-new", "default"]
        assert [len(c.objects) for c in store.calendars.values()] == [0, 0, 0]


class FirstPageOnly(AsyncMemoryCollection):
    """Lists through a paged API and never asks for the second page."""

    def _page(self) -> list[MemoryObject]:
        return list(self.store.objects.values())[:50]

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        return [StoredObject(o.native_id, o.etag, o.calendar, o.data, o) for o in self._page()]

    async def _list_etags(self) -> dict[str, str]:
        return {o.native_id: o.etag for o in self._page()}


async def test_the_suite_catches_a_backend_that_reads_one_page() -> None:
    class Backend(AsyncMemoryBackend):
        collection_class = FirstPageOnly

    suite = TestFullProfile()
    suite._made, suite._borrowed = [], []
    with pytest.raises(AssertionError):
        await suite.test_listings_are_not_cut_at_a_page(await Backend(profile=FULL).collection("default"))


async def test_the_suite_catches_a_collection_wider_than_its_backend() -> None:
    backend = AsyncMemoryBackend(profile=FULL)
    backend.capabilities = backend.capabilities.updated({Feature.WRITE: Support.UNSUPPORTED})
    with pytest.raises(AssertionError):
        await TestFullProfile().test_collection_never_widens_backend(backend, await backend.collection("default"))
