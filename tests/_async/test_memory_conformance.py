# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The in-memory reference backend passes the conformance suite, in seven profiles.

The library's four: a backend that can do everything, one shaped like an
issue tracker (tasks only, mapped statuses and priorities, assigned
identity), a writable one without most optional features, and a read-only
feed.  Three more here reach the branches those leave out; CI checks the
branch coverage of the suite.  The supported branches of
``recurrence.edit-occurrence``, ``recurrence.edit-this-and-future`` and
``task.complete-recurring`` wait for the merge in recurring-ical-events
(API_DESIGN.md Q3).

The second half is backends that each break one rule, and the suite test
that must catch it: a test that cannot fail tests nothing.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, date, datetime, time
from typing import Any, ClassVar

import icalendar
import pytest
from icalendar_searcher import Searcher

from calendaring import (
    AsyncBackend,
    AsyncCollection,
    AsyncItem,
    AsyncTask,
    BackendError,
    BrokenObject,
    Component,
    Feature,
    Item,
    NotFoundError,
    Support,
    SyncToken,
    Task,
    VerificationError,
)
from calendaring._async.conformance import snapshot
from calendaring.loss import LossPolicy
from calendaring.memory import (
    FEED,
    FULL,
    MINIMAL,
    TRACKER,
    AsyncMemoryBackend,
    AsyncMemoryCollection,
    MemoryCalendar,
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

    async def unseed(self, collection: AsyncCollection, item: AsyncItem) -> None:
        assert isinstance(collection, AsyncMemoryBackend.collection_class)
        assert item.native_id is not None
        del collection.store.objects[item.native_id]


# Three more profiles, for the branches the four above leave out.
EVENTS_ONLY = Profile(
    "events-only",
    {
        **FULL.capabilities,
        Feature.COMPONENT_TASK: Support.UNSUPPORTED,
        Feature.COMPONENT_JOURNAL: Support.UNSUPPORTED,
        Feature.CREATE_COLLECTION: Support.UNSUPPORTED,
        Feature.DELETE_COLLECTION: Support.UNSUPPORTED,
        Feature.CHANGES_KNOWN: Support.UNSUPPORTED,
    },
    frozenset({Component.EVENT}),
)

PLAIN_TASKS = Profile(
    "plain-tasks",
    {
        **TRACKER.capabilities,
        Feature.TASK_STATUS: Support.UNSUPPORTED,
        Feature.TASK_PRIORITY: {"level": Support.LOSSY},  # mapped by rules of its own
        Feature.CATEGORIES: {"level": Support.LOSSY},
        Feature.IDENTITY_CLIENT_NATIVE_ID: Support.FULL,
        Feature.CREATE_COLLECTION: Support.UNKNOWN,
    },
    frozenset({Component.TASK}),
)

TASK_FEED = Profile(
    "task-feed",
    {
        **FEED.capabilities,
        Feature.COMPONENT_EVENT: Support.UNSUPPORTED,
        Feature.COMPONENT_JOURNAL: Support.UNSUPPORTED,
        Feature.RECURRENCE: Support.UNSUPPORTED,
    },
    frozenset({Component.TASK}),
)


class TestEventsOnlyProfile(AsyncConformance):
    """Writable without tasks; a change feed without known=; move, but no second collection."""

    async def open_backend(self) -> AsyncBackend:
        return AsyncMemoryBackend(profile=EVENTS_ONLY)


class CannotCreate(AsyncMemoryBackend):
    async def _create_collection(self, name: str, components: frozenset[Component]) -> AsyncCollection:
        raise BackendError("this server turned out not to make collections")


class TestPlainTasksProfile(AsyncConformance):
    """No status, priority and categories mapped by its own rules, and an unprobed create-collection."""

    allow_unknown = True

    async def open_backend(self) -> AsyncBackend:
        return CannotCreate(profile=PLAIN_TASKS)


class TestTaskFeedProfile(TestFeedProfile):
    """A read-only collection of tasks, none recurring."""

    async def open_backend(self) -> AsyncBackend:
        return AsyncMemoryBackend(profile=TASK_FEED)


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

    async def test_a_broken_object_in_a_borrowed_collection_is_left_alone(self) -> None:
        backend = sharing(MINIMAL)
        bad = backend.store.calendars["default"].put(
            b"BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nDUE:2026-10-20\r\nEND:VTODO\r\n"
        )
        suite = self.suite()
        collection = await suite.open_collection(backend)
        assert await snapshot(collection) == []
        await collection.add_task(summary="the suite's")
        await suite.tidy_up(backend)
        assert list(backend.store.calendars["default"].objects) == [bad]

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


# Backends that each break one rule, and the suite test that must catch it.


def _listed(o: MemoryObject, calendar: icalendar.Calendar | None = None) -> StoredObject:
    return StoredObject(o.native_id, o.etag, calendar if calendar is not None else o.calendar, o.data, o)


class FirstPageOnly(AsyncMemoryCollection):
    """Lists through a paged API and never asks for the second page."""

    def _page(self) -> list[MemoryObject]:
        return list(self.store.objects.values())[:50]

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        return [_listed(o) for o in self._page()]

    async def _list_etags(self) -> dict[str, str]:
        return {o.native_id: o.etag for o in self._page()}


class MatchesUidSubstrings(AsyncMemoryCollection):
    """Looks a UID up with a server text-match, newest first."""

    async def _find_uid(self, uid: str) -> StoredObject:
        for o in reversed(list(self.store.objects.values())):
            if uid in o.uid:
                return _listed(o)
        raise NotFoundError(f"no item with UID {uid!r}")


class SearchesTheAccount(AsyncMemoryCollection):
    """Looks a UID up in every collection of the account."""

    async def _find_uid(self, uid: str) -> StoredObject:
        for calendar in self._backend.store.calendars.values():
            for o in calendar.objects.values():
                if o.uid == uid:
                    return _listed(o)
        raise NotFoundError(f"no item with UID {uid!r}")


class ReplacesEventUids(AsyncMemoryCollection):
    """Gives every event a UID of its own, while declaring identity.client-uid."""

    async def _create(self, calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
        for component in calendar.subcomponents:
            if component.name == "VEVENT":
                component["UID"] = f"server-{self._backend.store.next()}"
        return await super()._create(calendar, native_id=native_id, wire=None)


class StaleListing(AsyncMemoryCollection):
    """Lists the content it first saw under each object's current etag."""

    seen: ClassVar[dict[str, icalendar.Calendar]] = {}

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        cached = [self.seen.setdefault(o.native_id, o.calendar) for o in self.store.objects.values()]
        return [_listed(o, copy.deepcopy(c)) for o, c in zip(self.store.objects.values(), cached, strict=True)]


class ListViewStream(AsyncMemoryCollection):
    """Streams the fields of a list view: no DESCRIPTION, no CATEGORIES."""

    async def _iter_query(self, searcher: Searcher | None) -> AsyncIterator[StoredObject]:
        for o in self.store.objects.values():
            calendar = o.calendar
            for component in calendar.subcomponents:
                for name in ("DESCRIPTION", "CATEGORIES"):
                    component.pop(name, None)
            yield _listed(o, calendar)


class WordIndex(AsyncMemoryCollection):
    """Narrows a text search with a word index, so "rain" misses "Training"."""

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        wanted = {str(v) for v in getattr(searcher, "_property_filters", {}).values()}
        out = []
        for o in self.store.objects.values():
            words = {w for c in o.calendar.subcomponents for w in str(c.get("SUMMARY", "")).split()}
            if not wanted or wanted & words:
                out.append(_listed(o))
        return out


class NoDeletionsFeed(AsyncMemoryCollection):
    """A feed whose change listing never drops an id it has seen."""

    seen: ClassVar[dict[str, str]] = {}

    async def _list_etags(self) -> dict[str, str]:
        self.seen.update(await super()._list_etags())
        return dict(self.seen)


class FrozenFeed(AsyncMemoryCollection):
    """Serves its first download forever."""

    first: list[StoredObject] | None = None

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        if FrozenFeed.first is None and self.store.objects:  # the first download that has something in it
            FrozenFeed.first = [_listed(o) for o in self.store.objects.values()]
        return FrozenFeed.first or []

    async def _list_etags(self) -> dict[str, str]:
        return {s.native_id: s.etag for s in await self._query(None)}


class LeakyChangeFeed(AsyncMemoryCollection):
    """Reports changes in every collection of the account."""

    async def _changes(
        self, token: SyncToken | None, known: Mapping[str, str] | None
    ) -> tuple[Sequence[StoredObject | BrokenObject], list[str], SyncToken]:
        changed, deleted, new_token = await super()._changes(token, known)
        others = [
            _listed(o)
            for c in self._backend.store.calendars.values()
            if c is not self.store
            for o in c.objects.values()
        ]
        return [*changed, *others], deleted, new_token


class Mangling(AsyncMemoryCollection):
    """Stores a mangled copy of whatever it is given, declaring that it loses nothing."""

    def mangle(self, component: icalendar.Component) -> None:
        raise NotImplementedError

    def _mangled(self, calendar: icalendar.Calendar, wire: bytes | None) -> icalendar.Calendar:
        out = icalendar.Calendar.from_ical(wire) if wire is not None else copy.deepcopy(calendar)
        for component in out.subcomponents:
            if component.name in ("VEVENT", "VTODO", "VJOURNAL"):
                self.mangle(component)
        return out

    async def _create(self, calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
        return await super()._create(self._mangled(calendar, wire), native_id=native_id, wire=None)


class DropsAlarms(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        component.subcomponents[:] = [c for c in component.subcomponents if c.name != "VALARM"]


class AsciiOnly(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        for name in ("SUMMARY", "DESCRIPTION", "LOCATION"):
            if name in component:
                text = "".join(ch if ord(ch) < 128 else "?" for ch in str(component.pop(name)))
                component.add(name, text)


class FlattensNewlines(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        if "DESCRIPTION" in component:
            component["DESCRIPTION"] = icalendar.vText(str(component["DESCRIPTION"]).replace("\n", " "))


class StoresUtc(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        for name in ("DTSTART", "DTEND"):
            value = component.decoded(name, None)
            if isinstance(value, datetime):
                component.pop(name)
                component.add(name, value.astimezone(UTC))


class DatesAsDateTimes(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        for name in ("DTSTART", "DTEND", "DUE"):
            value = component.decoded(name, None)
            if isinstance(value, date) and not isinstance(value, datetime):
                component.pop(name)
                component.add(name, datetime.combine(value, time(), UTC))


class DropsEventDetails(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        for name in ("LOCATION", "URL", "EXDATE", "X-CONFORMANCE-COLOUR"):
            component.pop(name, None)


class DropsOverrides(AsyncMemoryCollection):
    async def _create(self, calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
        calendar = copy.deepcopy(calendar)
        calendar.subcomponents[:] = [c for c in calendar.subcomponents if "RECURRENCE-ID" not in c]
        return await super()._create(calendar, native_id=native_id, wire=None)


class FirstValueOnly(Mangling):
    def mangle(self, component: icalendar.Component) -> None:
        for name in ("CATEGORIES", "ATTENDEE"):
            values = component.get(name)
            if isinstance(values, list):
                component[name] = values[0]


class StartsInRange(AsyncMemoryCollection):
    """Narrows a time range by DTSTART alone, and leaves tasks out of it."""

    async def _query(self, searcher: Searcher | None) -> list[StoredObject]:
        start, end = getattr(searcher, "start", None), getattr(searcher, "end", None)
        out = []
        for o in self.store.objects.values():
            components = [c for c in o.calendar.subcomponents if c.name in ("VEVENT", "VTODO")]
            if start is None or any(
                c.name == "VEVENT" and start <= c.decoded("DTSTART") < end for c in components if "DTSTART" in c
            ):
                out.append(_listed(o))
        return out


class CompletesNothing(AsyncMemoryCollection):
    """Declares task.complete-recurring and writes nothing."""

    def __init__(self, backend: AsyncMemoryBackend, store: MemoryCalendar) -> None:
        super().__init__(backend, store)
        self.capabilities = self.capabilities.updated({Feature.TASK_COMPLETE_RECURRING: Support.FULL})

    async def _complete_recurring(
        self, task: Task, at: datetime | None, mode: str, *, loss: LossPolicy | None, verify: bool | None
    ) -> AsyncTask:
        return self.bind(task)


# What the suite raises when it catches a defect: its own assertion, or a
# pytest.raises that did not raise.  The round trip catches through verify=True.
ASSERTS = (AssertionError, pytest.fail.Exception)

CAUGHT: list[tuple[type[AsyncMemoryCollection], str, dict[str, Any], Any]] = [
    (FirstPageOnly, "test_listings_are_not_cut_at_a_page", {}, ASSERTS),
    (MatchesUidSubstrings, "test_get_finds_the_exact_uid", {}, ASSERTS),
    (SearchesTheAccount, "test_get_finds_the_exact_uid", {}, ASSERTS),
    (ReplacesEventUids, "test_get_finds_the_exact_uid", {}, ASSERTS),
    (StaleListing, "test_a_listing_shows_a_save_made_elsewhere", {}, ASSERTS),
    (ListViewStream, "test_iter_search_yields_what_search_returns", {}, ASSERTS),
    (WordIndex, "test_search_gives_the_client_side_answer", {"name": "summary-contains"}, ASSERTS),
    (FrozenFeed, "test_read_paths_on_seeded_items", {}, ASSERTS),
    (NoDeletionsFeed, "test_read_paths_on_seeded_items", {}, ASSERTS),
    (LeakyChangeFeed, "test_changes_follow_a_move", {}, ASSERTS),
    (DropsAlarms, "test_real_world_objects_round_trip", {"name": "event-series-in-a-time-zone"}, VerificationError),
    (DropsAlarms, "test_real_world_objects_round_trip", {"name": "task-due-on-a-date"}, VerificationError),
    (AsciiOnly, "test_real_world_objects_round_trip", {"name": "journal-entry"}, VerificationError),
    (FlattensNewlines, "test_real_world_objects_round_trip", {"name": "task-due-on-a-date"}, VerificationError),
    (StoresUtc, "test_real_world_objects_round_trip", {"name": "event-series-in-a-time-zone"}, VerificationError),
    (StoresUtc, "test_real_world_objects_round_trip", {"name": "floating-event"}, VerificationError),
    (DatesAsDateTimes, "test_real_world_objects_round_trip", {"name": "all-day-event-with-rdate"}, VerificationError),
    (DatesAsDateTimes, "test_real_world_objects_round_trip", {"name": "task-due-on-a-date"}, VerificationError),
    (
        DropsEventDetails,
        "test_real_world_objects_round_trip",
        {"name": "event-series-in-a-time-zone"},
        VerificationError,
    ),
    (DropsOverrides, "test_real_world_objects_round_trip", {"name": "event-series-in-a-time-zone"}, VerificationError),
    (FirstValueOnly, "test_real_world_objects_round_trip", {"name": "task-due-on-a-date"}, VerificationError),
    (StartsInRange, "test_search_gives_the_client_side_answer", {"name": "events-in-range"}, ASSERTS),
    (StartsInRange, "test_search_gives_the_client_side_answer", {"name": "tasks-in-range"}, ASSERTS),
    (CompletesNothing, "test_complete_a_recurring_task", {}, ASSERTS),
]


@pytest.mark.parametrize(
    ("broken", "test", "params", "expected"),
    CAUGHT,
    ids=[f"{c[0].__name__}-{c[2].get('name', '')}".rstrip("-") for c in CAUGHT],
)
async def test_the_suite_catches(
    broken: type[AsyncMemoryCollection], test: str, params: dict[str, Any], expected: Any
) -> None:
    class Backend(AsyncMemoryBackend):
        collection_class = broken

    StaleListing.seen.clear()
    NoDeletionsFeed.seen.clear()
    FrozenFeed.first = None
    profile, suite = (FEED, TestFeedProfile()) if broken in (FrozenFeed, NoDeletionsFeed) else (FULL, TestFullProfile())
    suite._made, suite._borrowed = [], []
    backend = Backend(profile=profile)
    collection = await suite.open_collection(backend)
    method = getattr(suite, test)
    wanted = inspect.signature(method).parameters
    fixtures = {"backend": backend, "collection": collection}
    with pytest.raises(expected):
        await method(**{k: v for k, v in fixtures.items() if k in wanted}, **params)


async def test_the_suite_catches_a_collection_wider_than_its_backend() -> None:
    backend = AsyncMemoryBackend(profile=FULL)
    backend.capabilities = backend.capabilities.updated({Feature.WRITE: Support.UNSUPPORTED})
    with pytest.raises(AssertionError):
        await TestFullProfile().test_collection_never_widens_backend(backend, await backend.collection("default"))
