# Hand-written source; tools/generate_sync.py derives ../_sync/conformance.py from this file.
"""The backend conformance suite (roadmap 1.2): one test suite every backend must pass.

A backend's test module subclasses it and says how to open the backend::

    class TestMyBackend(AsyncConformance):
        async def open_backend(self) -> AsyncBackend:
            return AsyncMyBackend(...)

and, for sync mode, the same with ``SyncConformance`` and ``MyBackend``.  The
sync suite is generated from this file, so both modes run the same tests.
The suite needs pytest-asyncio with ``asyncio_mode = "auto"``.

The suite is capability-aware.  A test reads the declaration first, and an
operation the collection declares ``UNSUPPORTED`` is asserted to raise
``UnsupportedError`` for that feature before any I/O, rather than being
skipped.  The skips are for a premise that cannot be set up (a write test on a
read-only collection, after the write was checked to raise; a task test on a
collection without tasks; a recurrence test where nothing recurs), and they
say so.

The suite makes its own collection where the backend can.  Where it cannot,
it uses the first one listed, compares only what the test itself stored, and
deletes what it stored afterwards, and puts back a name it changed.  Where it
can make collections but not delete them, it reuses the ones an earlier run
left, so they hold nothing of the suite's after a run.
"""

from __future__ import annotations

import pickle
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import icalendar
import pytest
from icalendar_searcher import Searcher

from .._corpus import CORPUS
from .._mapping import differences, property_lines
from ..capabilities import BACKEND_FEATURES, Feature, Support
from ..errors import (
    AlreadyExistsError,
    BackendError,
    CalendaringError,
    ConflictError,
    LossyWriteError,
    LossyWriteWarning,
    NotFoundError,
    PartialResultError,
    SyncTokenError,
    UnsupportedError,
)
from ..items import Event, Item, Journal, Task, TaskStatus
from ..loss import ALLOW, WARN, Loss
from ..timetracking import is_entry
from ..types import Component, Scope, StoredObject, SyncToken
from .backend import AsyncBackend
from .collection import AsyncCollection
from .items import AsyncEvent, AsyncItem, AsyncJournal, AsyncTask

__all__ = ["AsyncConformance"]


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


HOUR = timedelta(hours=1)


def key(item: Item) -> tuple[str, str | None]:
    rid = item.component.get("RECURRENCE-ID")
    return item.uid, None if rid is None else rid.to_ical().decode()


def component_key(component: icalendar.Component) -> tuple[str, str | None]:
    rid = component.get("RECURRENCE-ID")
    return str(component["UID"]), None if rid is None else rid.to_ical().decode()


def nid(item: Item) -> str:
    """A stored item's native id, which a stored item always has."""
    assert item.native_id is not None
    return item.native_id


def stored_by(found: Sequence[AsyncItem], seeded: Sequence[AsyncItem]) -> list[AsyncItem]:
    """Of what a read found, what the test itself stored: a borrowed collection holds other things too."""
    mine = {i.native_id for i in seeded}
    return [i for i in found if i.native_id in mine]


def writable(collection: AsyncCollection) -> bool:
    return collection.capabilities.level(Feature.WRITE) is not Support.UNSUPPORTED


def holds(collection: AsyncCollection, kind: Component) -> bool:
    return kind in collection.components


def level(collection: AsyncCollection, feature: Feature) -> Support:
    return collection.capabilities.level(feature)


async def readable(collection: AsyncCollection) -> tuple[list[AsyncItem], set[str]]:
    """What a borrowed collection holds that can be read, and the native ids of what cannot."""
    try:
        return await collection.search(), set()
    except PartialResultError as exc:
        return list(exc.results), {b.native_id for b in exc.broken}


async def snapshot(collection: AsyncCollection) -> list[tuple[str | None, str | None]]:
    """What is stored and readable, as (native_id, etag) pairs: unchanged means no write happened."""
    return sorted((i.native_id, i.etag) for i in (await readable(collection))[0])


# One item type per component: the data class, the add_ shorthand, the
# listing shorthand, the bound class and the feature that declares it.
KINDS: dict[Component, tuple[type[Item], str, str, type[AsyncItem], Feature]] = {
    Component.EVENT: (Event, "add_event", "events", AsyncEvent, Feature.COMPONENT_EVENT),
    Component.TASK: (Task, "add_task", "tasks", AsyncTask, Feature.COMPONENT_TASK),
    Component.JOURNAL: (Journal, "add_journal", "journals", AsyncJournal, Feature.COMPONENT_JOURNAL),
}

# What a component needs to be valid: RFC 5545 requires DTSTART on a VEVENT
# outside a scheduling message, and a server may refuse one without (Radicale).
REQUIRED: dict[Component, dict[str, Any]] = {Component.EVENT: {"start": at(1)}}


def minimal(kind: Component, summary: str, **properties: Any) -> Item:
    """The smallest valid item of a kind, with ``properties`` as ``new()`` takes them."""
    return KINDS[kind][0].new(summary=summary, **REQUIRED.get(kind, {}), **properties)


# A feature that is about one property, and a value of it to store.
PROPERTY_SAMPLES: dict[Feature, tuple[str, Any, dict[str, str]]] = {
    Feature.CATEGORIES: ("CATEGORIES", ["work"], {}),
    Feature.ATTENDEES: ("ATTENDEE", "mailto:alice@example.com", {"CN": "Alice"}),
    Feature.TASK_PERCENT_COMPLETE: ("PERCENT-COMPLETE", 40, {}),
    Feature.TASK_START: ("DTSTART", at(1), {}),
    Feature.TASK_DUE: ("DUE", at(3), {}),
    Feature.TASK_COMPLETED: ("COMPLETED", at(2), {}),
    Feature.TASK_PLANNED: ("X-PYCAL-PLANNED-START", icalendar.vDDDTypes(at(2)), {}),
    # A bare timedelta would be written "2:00:00", which is not iCalendar.
    Feature.TASK_ESTIMATE: ("ESTIMATED-DURATION", icalendar.vDuration(timedelta(hours=2)), {}),
    Feature.TASK_RELATIONS_PARENT: ("RELATED-TO", "conformance-parent", {"RELTYPE": "PARENT"}),
    Feature.TASK_RELATIONS_DEPENDS_ON: ("RELATED-TO", "conformance-dependency", {"RELTYPE": "DEPENDS-ON"}),
}

# Features whose values a backend may map; the candidates cover the whole range.
VALUE_SAMPLES: dict[Feature, tuple[str, list[Any]]] = {
    Feature.TASK_STATUS: ("STATUS", [s.value for s in TaskStatus]),
    Feature.TASK_PRIORITY: ("PRIORITY", list(range(10))),
}


def value_of(component: icalendar.Component, name: str) -> Any:
    if name not in component:
        return None
    value = component[name]
    return int(str(value)) if name == "PRIORITY" else str(value)


def searchers() -> dict[str, Callable[[], Searcher]]:
    def categories() -> Searcher:
        s = Searcher(todo=True)
        s.add_property_filter("CATEGORIES", "work")
        return s

    def text(value: str, operator: str = "contains", case_sensitive: bool = True) -> Callable[[], Searcher]:
        def make() -> Searcher:
            s = Searcher()
            s.add_property_filter("SUMMARY", value, operator=operator, case_sensitive=case_sensitive)
            return s

        return make

    def no_due() -> Searcher:
        s = Searcher(todo=True)
        s.add_property_filter("DUE", None, operator="undef")
        return s

    return {
        "everything": lambda: Searcher(),
        "summary-contains": text("rain"),
        "summary-any-case": text("REPORT", case_sensitive=False),
        "summary-not-containing": text("report", operator="!contains"),
        "tasks-without-due": no_due,
        "alarms-in-range": lambda: Searcher(alarm_start=at(3, 9), alarm_end=at(3, 10)),
        "tasks": lambda: Searcher(todo=True),
        "open-tasks": lambda: Searcher(todo=True, include_completed=False),
        "events-in-range": lambda: Searcher(event=True, start=at(2), end=at(4)),
        "tasks-in-range": lambda: Searcher(todo=True, start=at(2), end=at(4)),
        "events-expanded": lambda: Searcher(event=True, start=at(2), end=at(4), expand=True),
        "journals": lambda: Searcher(journal=True),
        "categories": categories,
    }


def dataset() -> list[Item]:
    """The items the search tests seed: something for each searcher to match and to miss."""
    a = Task.new(summary="A: report", end=at(3), categories=["work"])
    b = Task.new(summary="B: done", end=at(5), status=TaskStatus.COMPLETED)
    c = Task.new(summary="C: open")
    e1 = Event.new(summary="E1: meeting", start=at(3, 10), end=at(3, 11))
    alarm = icalendar.Alarm()
    alarm.add("ACTION", "DISPLAY")
    alarm.add("DESCRIPTION", "E1 starts soon")
    alarm.add("TRIGGER", timedelta(minutes=-30))
    e1.component.add_component(alarm)
    t = Event.new(summary="T: Training session", start=at(10, 18), end=at(10, 19))
    s = Event.new(summary="S: overnight", start=at(1, 20), end=at(2, 14))  # began before the range
    ts = Task.new(summary="TS: start only", start=at(3))
    tb = Task.new(summary="TB: spans the range", start=at(1), end=at(5))
    e2 = Event.new(summary="E2: later", start=at(20, 10), end=at(20, 11))
    r = Event.new(summary="R: daily", start=at(1, 9), end=at(1, 10))
    r.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 5})
    j = Journal.new(summary="J: notes", start=at(2))
    return [a, b, c, ts, tb, e1, e2, t, s, r, j]


class AsyncConformance:
    """Subclass as ``TestSomething`` and implement ``open_backend()``."""

    # Non-CalDAV backends are code, not servers, and must not declare UNKNOWN (§5.1).
    allow_unknown: bool = False

    # How many objects the paging test stores: more than a page of the APIs
    # backends list through (a JMAP /query limit, an issue tracker's list).
    page_size_hint: int = 120

    # --- hooks a backend's test module may override --------------------

    async def open_backend(self) -> AsyncBackend:
        raise NotImplementedError("a conformance test class must implement open_backend()")

    async def open_collection(self, backend: AsyncBackend, name: str = "conformance") -> AsyncCollection:
        """A collection to test against: made if the backend may be able to, else the first one listed.

        Where collections can be made but not deleted, the one a previous run
        left is used again, so a run leaves one collection per name behind.
        """
        if backend.capabilities.level(Feature.CREATE_COLLECTION) is not Support.UNSUPPORTED:
            deletable = backend.capabilities.level(Feature.DELETE_COLLECTION) is not Support.UNSUPPORTED
            left = None if deletable else await self.left_behind(backend, name)
            if left is not None:
                return await self.borrow(left)
            try:
                made = await backend.create_collection(
                    name, components=(Component.EVENT, Component.TASK, Component.JOURNAL)
                )
            except CalendaringError:
                if backend.capabilities.level(Feature.CREATE_COLLECTION) is not Support.UNKNOWN:
                    raise
            else:
                if not deletable:
                    return await self.borrow(made)
                self._made.append(made)
                return made
        existing = await backend.collections()
        if not existing:
            pytest.fail("the backend can neither create a collection nor list one")
        return await self.borrow(existing[0])

    async def left_behind(self, backend: AsyncBackend, name: str) -> AsyncCollection | None:
        named = [c for c in await backend.collections() if c.name == name]
        return named[0] if named else None

    async def borrow(self, collection: AsyncCollection) -> AsyncCollection:
        """Use a collection the suite may not delete, and note what it writes there, to delete only that."""
        items, broken = await readable(collection)
        before: set[str | None] = {i.native_id for i in items} | set(broken)
        written: set[str] = set()  # native ids and UIDs of what the suite created
        create = collection._create

        async def noting(calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
            stored = await create(calendar, native_id=native_id, wire=wire)
            written.add(stored.native_id)
            written.update(str(c.get("UID")) for c in stored.calendar.subcomponents if "UID" in c)
            return stored

        collection._create = noting  # type: ignore[method-assign]
        self._borrowed.append((collection, before, written))
        return collection

    async def seed(self, collection: AsyncCollection, items: Sequence[Item]) -> list[AsyncItem]:
        """Store items the way a test needs them.  A read-only backend overrides this to seed natively."""
        return [await collection.add(item, loss=ALLOW) for item in items]

    async def unseed(self, collection: AsyncCollection, item: AsyncItem) -> None:
        """Remove a seeded item.  A read-only backend overrides this to remove it natively."""
        await collection.delete(item)

    # --- fixtures -----------------------------------------------------

    @pytest.fixture
    async def backend(self) -> AsyncIterator[AsyncBackend]:
        self._made: list[AsyncCollection] = []
        self._borrowed: list[tuple[AsyncCollection, set[str | None], set[str]]] = []
        backend = await self.open_backend()
        async with backend:
            yield backend
            await self.tidy_up(backend)

    async def tidy_up(self, backend: AsyncBackend) -> None:
        """Delete the collections the test made, and what it stored in one it borrowed.

        In a borrowed collection, an item is the suite's if it created it, or
        if it carries or relates to a UID the suite created (a completed
        occurrence the backend stored as a new object): anything else arrived
        from another client while the test ran.
        """
        for made in self._made:
            await made.delete_collection()
        for borrowed, before, written in self._borrowed:
            if not writable(borrowed):
                continue
            items, _ = await readable(borrowed)
            for native_id in {i.native_id for i in items if self.wrote(i, before, written)}:
                assert native_id is not None
                await borrowed.delete(native_id)

    @staticmethod
    def wrote(item: Item, before: set[str | None], written: set[str]) -> bool:
        if item.native_id in before:
            return False
        related = item.component.get("RELATED-TO", [])
        related = related if isinstance(related, list) else [related]
        return item.native_id in written or item.uid in written or any(str(r) in written for r in related)

    @pytest.fixture
    async def collection(self, backend: AsyncBackend) -> AsyncCollection:
        return await self.open_collection(backend)

    # --- helpers ------------------------------------------------------

    async def assert_unsupported(self, collection: AsyncCollection, feature: Feature, call: Callable[[], Any]) -> None:
        """The call raises ``UnsupportedError(feature)``, and nothing was written."""
        before = await snapshot(collection)
        with pytest.raises(UnsupportedError) as info:
            await call()
        assert info.value.feature == feature, f"expected UnsupportedError({feature}), got {info.value!r}"
        assert await snapshot(collection) == before, "an unsupported operation wrote something"

    async def assert_read_only(self, collection: AsyncCollection) -> None:
        await self.assert_unsupported(collection, Feature.WRITE, lambda: collection.add(Task.new(summary="x")))

    async def add_task(self, collection: AsyncCollection, **properties: Any) -> AsyncTask:
        """A stored task, or the test ends here having checked that it cannot be one."""
        if not writable(collection):
            await self.assert_read_only(collection)
            pytest.skip("read-only collection: the write paths were checked to raise")
        if not holds(collection, Component.TASK):
            await self.assert_unsupported(
                collection, Feature.COMPONENT_TASK, lambda: collection.add(Task.new(summary="x"))
            )
            pytest.skip("the collection holds no tasks: adding one was checked to raise")
        return await collection.add_task(**properties)

    # === the declaration ==============================================

    async def test_declaration_covers_every_feature(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        declared = set(backend.capabilities) | set(collection.capabilities)
        missing = set(Feature) - declared
        assert not missing, f"not declared: {sorted(missing)}"
        assert set(BACKEND_FEATURES) <= set(backend.capabilities)

    async def test_no_unknown_levels(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        if self.allow_unknown:
            return
        for table in (backend.capabilities, collection.capabilities):
            unknown = [f for f in table if table.level(f) is Support.UNKNOWN]
            assert not unknown, f"a backend that is code must know: {unknown}"

    async def test_collection_never_widens_backend(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        for feature in set(backend.capabilities) & set(collection.capabilities):
            if backend.capabilities.level(feature) is Support.UNSUPPORTED:
                assert collection.capabilities.level(feature) is Support.UNSUPPORTED, feature

    async def test_components_match_declaration(self, collection: AsyncCollection) -> None:
        for kind, (_, _, _, _, feature) in KINDS.items():
            assert (kind in collection.components) == (level(collection, feature) is not Support.UNSUPPORTED), kind

    async def test_value_details_are_lists(self, collection: AsyncCollection) -> None:
        for feature in collection.capabilities:
            values = collection.capabilities.details(feature).get("values")
            if values is not None:
                assert isinstance(values, list | tuple), feature
                assert values, feature

    # === listing ======================================================

    async def test_backend_lists_the_collection(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        assert collection.backend_id == backend.id
        assert collection.id in [c.id for c in await backend.collections()]
        assert (await backend.collection(collection.id)).id == collection.id

    async def test_missing_collection_raises(self, backend: AsyncBackend) -> None:
        with pytest.raises(NotFoundError):
            await backend.collection("conformance-no-such-collection")

    # === adding =======================================================

    @pytest.mark.parametrize("kind", list(KINDS))
    async def test_add_returns_a_bound_item(self, collection: AsyncCollection, kind: Component) -> None:
        _, add_name, list_name, bound_class, feature = KINDS[kind]
        item = minimal(kind, "conformance")
        if not writable(collection):
            await self.assert_read_only(collection)
            return
        if not holds(collection, kind):
            await self.assert_unsupported(collection, feature, lambda: collection.add(item))
            await self.assert_unsupported(
                collection, feature, lambda: getattr(collection, add_name)(summary="x", **REQUIRED.get(kind, {}))
            )
            assert await getattr(collection, list_name)() == []
            return
        stored = await collection.add(item)
        assert isinstance(stored, bound_class)
        assert stored.collection is collection
        assert stored.collections == (collection,)
        assert stored.collection_id == collection.id
        assert stored.native_id is not None
        assert stored.etag is not None
        assert stored.wire is None, "a write sets wire to None"
        assert str(stored.component["SUMMARY"]) == "conformance"
        assert item.native_id is None, "add() changed its argument"
        assert item.etag is None, "add() changed its argument"
        shorthand = await getattr(collection, add_name)(summary="shorthand", **REQUIRED.get(kind, {}))
        assert isinstance(shorthand, bound_class)
        assert {i.native_id for i in await getattr(collection, list_name)()} >= {stored.native_id, shorthand.native_id}

    async def test_uid_round_trips_as_declared(self, collection: AsyncCollection) -> None:
        item = Task.new(summary="identity")
        placeholder = await self.add_task(collection, summary="placeholder")
        await placeholder.delete()
        stored = await collection.add(item)
        if level(collection, Feature.IDENTITY_CLIENT_UID) is Support.UNSUPPORTED:
            assert stored.uid != item.uid
            with pytest.raises(NotFoundError):
                await collection.get(item.uid)
        else:
            assert stored.uid == item.uid
        assert (await collection.get(stored.uid)).native_id == stored.native_id

    async def test_adding_a_uid_twice(self, collection: AsyncCollection) -> None:
        first = await self.add_task(collection, summary="first")
        again = Task.from_ical(first.to_ical())
        if level(collection, Feature.IDENTITY_CLIENT_UID) is Support.UNSUPPORTED:
            second = await collection.add(again)
            assert second.native_id != first.native_id, "the backend assigns identity, so a second add is new"
            return
        before = await snapshot(collection)
        with pytest.raises(AlreadyExistsError) as info:
            await collection.add(again)
        assert info.value.taken == "uid"
        assert await snapshot(collection) == before

    async def test_add_with_native_id(self, collection: AsyncCollection) -> None:
        await self.add_task(collection, summary="setup")
        name = f"conformance-{uuid.uuid4().hex[:12]}.ics"  # a crashed run may leave one behind
        if level(collection, Feature.IDENTITY_CLIENT_NATIVE_ID) is Support.UNSUPPORTED:
            await self.assert_unsupported(
                collection,
                Feature.IDENTITY_CLIENT_NATIVE_ID,
                lambda: collection.add(Task.new(summary="named"), native_id=name),
            )
            return
        stored = await collection.add(Task.new(summary="named"), native_id=name)
        assert stored.native_id == name
        assert (await collection.get_by_native_id(name)).uid == stored.uid
        with pytest.raises(AlreadyExistsError) as info:
            await collection.add(Task.new(summary="clash"), native_id=name)
        assert info.value.taken == "native_id"

    # === reading ======================================================

    async def test_get_missing_raises(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        with pytest.raises(NotFoundError) as info:
            await collection.get("conformance-no-such-uid")
        assert isinstance(info.value, LookupError)
        assert info.value.backend == backend.id
        assert info.value.collection_id == collection.id
        with pytest.raises(NotFoundError):
            await collection.get_by_native_id("conformance-no-such-native-id")

    async def test_reads_are_bound_and_consistent(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="read me")
        by_uid = await collection.get(stored.uid)
        by_id = await collection.get_by_native_id(nid(stored))
        reloaded = await collection.reload(stored)
        for read in (by_uid, by_id, reloaded):
            assert isinstance(read, AsyncTask)
            assert read.collection is collection
            assert read.native_id == stored.native_id
            assert read.etag == stored.etag
            assert str(read.component["SUMMARY"]) == "read me"
        assert reloaded is not stored

    async def test_get_finds_the_exact_uid(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        stored = []
        for kind, (_, _, _, bound_class, _) in KINDS.items():
            if holds(collection, kind) and writable(collection):
                item = minimal(kind, f"get {kind.value}")
                added = await collection.add(item)
                if level(collection, Feature.IDENTITY_CLIENT_UID) is not Support.UNSUPPORTED:
                    assert added.uid == item.uid, f"identity.client-uid: the {kind.value} UID was replaced"
                stored.append((added, bound_class, kind))
        if not stored:
            pytest.skip("nothing can be stored here")
        for added, bound_class, _ in stored:
            found = await collection.get(added.uid)
            assert isinstance(found, bound_class)
            assert found.native_id == added.native_id
        if level(collection, Feature.IDENTITY_CLIENT_UID) is not Support.UNSUPPORTED:
            first, _, first_kind = stored[0]
            suffix = f"{first.uid}@conformance.invalid"
            longer = await collection.add(minimal(first_kind, "longer", uid=f"11-{suffix}"))
            shorter = await collection.add(minimal(first_kind, "shorter", uid=f"1-{suffix}"))
            assert (await collection.get(shorter.uid)).native_id == shorter.native_id
            assert (await collection.get(longer.uid)).native_id == longer.native_id
            with pytest.raises(NotFoundError):
                await collection.get(f"-{suffix}")
        if backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            other = await self.open_collection(backend, "conformance-other")
            if writable(other) and holds(other, Component.TASK):
                elsewhere = await other.add_task(summary="in another collection")
                with pytest.raises(NotFoundError):
                    await collection.get(elsewhere.uid)

    async def test_add_with_native_id_keeps_the_uid(self, collection: AsyncCollection) -> None:
        if level(collection, Feature.IDENTITY_CLIENT_NATIVE_ID) is Support.UNSUPPORTED:
            return  # test_add_with_native_id checked that it raises
        if level(collection, Feature.IDENTITY_CLIENT_UID) is Support.UNSUPPORTED:
            pytest.skip("the backend assigns UIDs")
        if not writable(collection):
            return  # test_read_only_rejects_every_write checked the writes
        kind = next(k for k in KINDS if holds(collection, k))
        item = minimal(kind, "named and identified")
        name = f"conformance-{uuid.uuid4().hex[:12]}.ics"
        stored = await collection.add(item, native_id=name)
        assert stored.uid == item.uid
        assert (await collection.get(item.uid)).native_id == name

    async def test_a_listing_shows_a_save_made_elsewhere(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="original", description="kept")
        other = await collection.get(stored.uid)
        other.component["SUMMARY"] = "saved elsewhere"
        await other.save()
        listed = [i for i in await collection.search() if i.native_id == stored.native_id]
        streamed = [i async for i in collection.iter_search() if i.native_id == stored.native_id]
        for [read] in (listed, streamed):
            assert read.etag != stored.etag, "a listing gave the old etag after a save"
            assert str(read.component["SUMMARY"]) == "saved elsewhere", "a listing gave old content"
            assert str(read.component["DESCRIPTION"]) == "kept"
        stored.component["SUMMARY"] = "stale"
        with pytest.raises(ConflictError):
            await stored.save()

    async def test_read_paths_on_seeded_items(self, collection: AsyncCollection) -> None:
        """What a read-only backend must pass too: everything here is stored through ``seed()``."""
        kind = next(k for k in KINDS if holds(collection, k))
        [first] = await self.seed(collection, [minimal(kind, "seeded first")])
        assert (await collection.get(first.uid)).native_id == first.native_id
        assert (await collection.get_by_native_id(nid(first))).uid == first.uid
        assert (await collection.reload(first)).etag == first.etag
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            return
        start = await collection.changes()
        [second] = await self.seed(collection, [minimal(kind, "seeded second")])
        since = await collection.changes(start.token)
        assert nid(second) in {nid(i) for i in since.changed}
        assert nid(first) not in {nid(i) for i in since.changed}
        assert since.deleted == []
        await self.unseed(collection, first)
        gone = await collection.changes(since.token)
        assert gone.deleted == [nid(first)]
        with pytest.raises(NotFoundError):
            await collection.get(first.uid)

    async def test_wire(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="bytes")
        read = await collection.get_by_native_id(nid(stored))
        data = Task.new(summary="sent as bytes")
        payload = data.to_ical().replace(b"SUMMARY:", b"SUMMARY;X-CONFORMANCE=1:")
        if level(collection, Feature.WRITE_WIRE) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.WRITE_WIRE, lambda: collection.add(data, wire=payload))
            return
        assert read.wire is not None
        assert Task.from_ical(read.wire) == read
        with pytest.raises(ValueError, match="UID"):
            await collection.add(Task.new(summary="other"), wire=payload)
        written = await collection.add(data, wire=payload)
        assert written == Task.from_ical(payload)
        assert written.wire is None
        reread = await collection.get_by_native_id(nid(written))
        assert reread.component["SUMMARY"].params.get("X-CONFORMANCE") == "1"

    # === saving and deleting ==========================================

    async def test_bound_save_updates_in_place(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="before")
        first_etag = stored.etag
        stored.component["SUMMARY"] = "after"
        await stored.save()
        assert stored.etag != first_etag
        stored.component["SUMMARY"] = "after again"
        await stored.save()  # no stale etag
        assert str((await collection.get_by_native_id(nid(stored))).component["SUMMARY"]) == "after again"

    async def test_collection_save_returns_a_copy(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="before")
        etag = stored.etag
        stored.component["SUMMARY"] = "after"
        result = await collection.save(stored)
        assert result is not stored
        assert stored.etag == etag, "collection.save() changed its argument"
        assert result.etag != etag
        assert isinstance(result, AsyncTask)

    async def test_save_detects_conflicts(self, collection: AsyncCollection) -> None:
        mine = await self.add_task(collection, summary="original")
        theirs = await collection.get_by_native_id(nid(mine))
        theirs.component["SUMMARY"] = "theirs"
        await theirs.save()
        mine.component["SUMMARY"] = "mine"
        with pytest.raises(ConflictError) as info:
            await mine.save()
        assert not isinstance(info.value, AlreadyExistsError | SyncTokenError)
        assert str((await collection.reload(mine)).component["SUMMARY"]) == "theirs"
        await mine.save(overwrite=True)
        assert str((await collection.reload(mine)).component["SUMMARY"]) == "mine"

    async def test_delete(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="doomed")
        stale = await collection.get_by_native_id(nid(stored))
        stored.component["SUMMARY"] = "changed"
        await stored.save()
        with pytest.raises(ConflictError):
            await collection.delete(stale)
        await stored.delete()
        with pytest.raises(NotFoundError):
            await collection.get_by_native_id(nid(stored))
        other = await collection.add_task(summary="by id")
        await collection.delete(nid(other))
        with pytest.raises(NotFoundError):
            await collection.get(other.uid)
        with pytest.raises(NotFoundError):
            await collection.delete(nid(other))
        with pytest.raises(NotFoundError):
            await collection.delete(stale, overwrite=True)

    async def test_read_only_rejects_every_write(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        if writable(collection):
            return
        kinds = [k for k in KINDS if holds(collection, k)]
        if not kinds:
            pytest.fail("a read-only collection that holds nothing")
        [stored] = await self.seed(collection, [minimal(kinds[0], "seeded")])
        other = await self.open_collection(backend, "conformance-other")
        for call in (
            lambda: collection.add(Task.new(summary="x")),
            lambda: collection.save(stored),
            lambda: stored.save(),
            lambda: collection.delete(stored),
            lambda: collection.move(stored, other),
        ):
            await self.assert_unsupported(collection, Feature.WRITE, call)
        if isinstance(stored, AsyncTask):
            await self.assert_unsupported(collection, Feature.WRITE, lambda: stored.complete())

    # === properties and losses ========================================

    async def test_unknown_properties_survive(self, collection: AsyncCollection) -> None:
        await self.add_task(collection, summary="setup")
        item = Task.new(summary="passthrough")
        item.component.add("X-CONFORMANCE-UNKNOWN", "kept", parameters={"X-PARAM": "too"})
        stored = await collection.add(item, loss=ALLOW)
        read = await collection.get_by_native_id(nid(stored))
        if level(collection, Feature.PROPERTIES_PASSTHROUGH) is Support.UNSUPPORTED:
            assert "X-CONFORMANCE-UNKNOWN" not in read.component
        else:
            assert property_lines(read.component, "X-CONFORMANCE-UNKNOWN") == property_lines(
                item.component, "X-CONFORMANCE-UNKNOWN"
            )

    @pytest.mark.parametrize("feature", list(PROPERTY_SAMPLES))
    async def test_property_round_trips_as_declared(self, collection: AsyncCollection, feature: Feature) -> None:
        await self.add_task(collection, summary="setup")
        name, value, params = PROPERTY_SAMPLES[feature]
        item = Task.new(summary=f"sample {feature}")
        item.component.add(name, value, parameters=params)
        support = level(collection, feature)
        if support is Support.UNSUPPORTED:
            before = await snapshot(collection)
            with pytest.raises(LossyWriteError) as info:
                await collection.add(item)
            assert [loss.property for loss in info.value.losses] == [name]
            assert info.value.losses[0].stored is None
            assert await snapshot(collection) == before
            stored = await collection.add(item, loss=ALLOW)
            assert name not in (await collection.reload(stored)).component
            return
        try:
            stored = await collection.add(item)
        except LossyWriteError:
            assert support is Support.LOSSY, f"{feature} is declared {support.value} but the write is lossy"
            return
        read = await collection.reload(stored)
        if support is not Support.LOSSY:
            assert property_lines(read.component, name) == property_lines(item.component, name)

    @pytest.mark.parametrize("feature", list(VALUE_SAMPLES))
    async def test_values_map_as_declared(self, collection: AsyncCollection, feature: Feature) -> None:
        await self.add_task(collection, summary="setup")
        name, candidates = VALUE_SAMPLES[feature]
        support = level(collection, feature)
        declared = collection.capabilities.details(feature).get("values")
        if support is Support.UNSUPPORTED:
            with pytest.raises(LossyWriteError):
                await collection.add(Task.new(summary="valued", **{name.lower(): candidates[1]}))
            return
        if support is Support.LOSSY and declared is None:
            # The backend maps by rules of its own: each value is stored, or refused before I/O.
            for value in candidates:
                item = Task.new(summary=f"{name}={value}")
                item.component[name] = value
                refused: list[str] = []
                try:
                    await collection.add(item)
                except LossyWriteError as exc:
                    refused = [loss.property for loss in exc.losses]
                assert refused in ([], [name]), refused
            return
        storable = candidates if declared is None else [v for v in candidates if v in declared]
        for value in storable:
            item = Task.new(summary=f"{name}={value}")
            item.component[name] = value
            stored = await collection.add(item)
            assert value_of((await collection.reload(stored)).component, name) == value, value
        if support is not Support.LOSSY:
            return
        assert declared is not None  # LOSSY without values returned above
        outside = [v for v in candidates if v not in declared]
        assert outside, f"{feature} is LOSSY but every value is storable"
        item = Task.new(summary=f"{name} outside")
        item.component[name] = outside[0]
        before = await snapshot(collection)
        with pytest.raises(LossyWriteError) as info:
            await collection.add(item)
        assert [loss.property for loss in info.value.losses] == [name]
        assert await snapshot(collection) == before, "a refused lossy write sent something"
        with pytest.warns(LossyWriteWarning):
            warned = await collection.add(item, loss=WARN)
        stored_value = value_of((await collection.reload(warned)).component, name)
        assert stored_value is None or stored_value in declared
        seen: list[Sequence[Loss]] = []

        def policy(losses: Sequence[Loss], item: Item) -> None:
            seen.append(losses)

        await collection.add(item, loss=policy)
        assert [[loss.property for loss in losses] for losses in seen] == [[name]]
        assert seen[0][0].sent == outside[0]

    @pytest.mark.parametrize("name", list(CORPUS))
    async def test_real_world_objects_round_trip(self, collection: AsyncCollection, name: str) -> None:
        """Only what the declaration says may be lost: verify compares the stored object with what was sent."""
        run = f"-{uuid.uuid4().hex[:12]}@conformance.invalid".encode()  # a crashed run may leave one behind
        item = Item.from_ical(CORPUS[name].replace(b"@conformance.invalid", run))
        kind = Component(item.component.name)
        if not holds(collection, kind):
            pytest.skip(f"the collection holds no {kind.value}")
        if writable(collection):
            read = await collection.reload(await collection.add(item, loss=ALLOW, verify=True))
        else:
            [seeded] = await self.seed(collection, [item])
            read = await collection.reload(seeded)
            assert differences(item.icalendar, read.icalendar) == []
        zones = {tz["TZID"] for tz in read.icalendar.walk("VTIMEZONE")}
        for component in read.icalendar.walk():
            for prop in component.property_items(recursive=False):
                tzid = getattr(prop[1], "params", {}).get("TZID")
                assert tzid is None or tzid in zones, f"{prop[0]} uses TZID {tzid}, which has no VTIMEZONE"

    async def test_verify_passes_on_an_honest_write(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="verified", description="checked after writing")
        stored.component["SUMMARY"] = "verified again"
        await stored.save(verify=True)
        await collection.add(Task.new(summary="verified add"), verify=True)

    # === search =======================================================

    @pytest.mark.parametrize("name", list(searchers()))
    async def test_search_gives_the_client_side_answer(self, collection: AsyncCollection, name: str) -> None:
        wanted = [i for i in dataset() if any(i.component.name == k.value for k in collection.components)]
        if not wanted:
            pytest.fail("the collection holds nothing")
        seeded = await self.seed(collection, wanted)
        expected = {
            component_key(c)
            for matched in searchers()[name]().filter([i.icalendar for i in seeded])
            for c in matched.subcomponents
            if c.name != "VTIMEZONE"
        }
        found = stored_by(await collection.search(searchers()[name]()), seeded)
        assert {key(i) for i in found} == expected
        assert all(isinstance(i, AsyncItem) and i.collection is collection for i in found)

    async def test_search_keywords_build_a_searcher(self, collection: AsyncCollection) -> None:
        if not holds(collection, Component.TASK):
            assert await collection.search(todo=True) == []
            return
        await self.seed(collection, [i for i in dataset() if isinstance(i, Task)])
        by_keywords = await collection.search(todo=True, include_completed=False)
        by_searcher = await collection.search(Searcher(todo=True, include_completed=False))
        assert {key(i) for i in by_keywords} == {key(i) for i in by_searcher}
        assert by_keywords

    async def test_expanded_search_returns_occurrences(self, collection: AsyncCollection) -> None:
        if not holds(collection, Component.EVENT):
            pytest.skip("the collection holds no events")
        series = [i for i in dataset() if str(i.component["SUMMARY"]).startswith("R:")]
        if level(collection, Feature.RECURRENCE) is Support.UNSUPPORTED:
            if not writable(collection):
                pytest.skip("read-only, and nothing recurs")
            with pytest.raises(LossyWriteError):
                await collection.add(series[0])
            return
        seeded = await self.seed(collection, series)
        found = stored_by(await collection.search(event=True, start=at(2), end=at(4), expand=True), seeded)
        assert len(found) == 2
        assert all(i.is_occurrence and i.wire is None for i in found)
        assert len({i.uid for i in found}) == 1
        assert len({i.native_id for i in found}) == 1
        assert all("RECURRENCE-ID" in i.component for i in found)

    async def test_iter_search_yields_what_search_returns(self, collection: AsyncCollection) -> None:
        wanted = [i for i in dataset() if any(i.component.name == k.value for k in collection.components)]
        await self.seed(collection, wanted)
        listed = await collection.search()
        iterated = [i async for i in collection.iter_search()]

        def fields(items: Sequence[AsyncItem]) -> list[tuple[Any, ...]]:
            return sorted((key(i), i.native_id, i.etag, i.component.to_ical()) for i in items)

        assert fields(iterated) == fields(listed), "iter_search() must give what search() gives, field by field"

    async def test_listings_are_not_cut_at_a_page(self, collection: AsyncCollection) -> None:
        kind = next(k for k in KINDS if holds(collection, k))
        seeded = await self.seed(collection, [minimal(kind, f"paged {n}") for n in range(self.page_size_hint)])
        mine = {nid(i) for i in seeded}
        assert {nid(i) for i in stored_by(await collection.search(), seeded)} == mine
        assert {nid(i) async for i in collection.iter_search() if i.native_id in mine} == mine
        last = seeded[-1]
        assert (await collection.get(last.uid)).native_id == last.native_id
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            return
        full = await collection.changes()
        assert mine <= {nid(i) for i in full.changed}
        if level(collection, Feature.CHANGES_KNOWN) is not Support.UNSUPPORTED:
            known = await collection.changes(known={nid(i): str(i.etag) for i in seeded})
            assert known.deleted == []
            assert not mine & {nid(i) for i in known.changed}
        if writable(collection):
            last.component["SUMMARY"] = "paged, changed"
            await last.save()
            since = await collection.changes(full.token)
            assert nid(last) in {nid(i) for i in since.changed}
            assert since.deleted == []

    async def test_search_sorts(self, collection: AsyncCollection) -> None:
        searcher = Searcher(todo=True)
        searcher.add_sort_key("SUMMARY")
        if not holds(collection, Component.TASK):
            assert await collection.search(searcher) == []
            return
        seeded = await self.seed(collection, [Task.new(summary=s) for s in ("b", "c", "a")])
        found = stored_by(await collection.search(searcher), seeded)
        assert [str(i.component["SUMMARY"]) for i in found] == ["a", "b", "c"]

    async def test_listing_shorthands_return_one_type(self, collection: AsyncCollection) -> None:
        wanted = [i for i in dataset() if any(i.component.name == k.value for k in collection.components)]
        seeded = await self.seed(collection, wanted)
        for kind, (_, _, list_name, bound_class, _) in KINDS.items():
            listed = await getattr(collection, list_name)()
            assert all(isinstance(i, bound_class) for i in listed)
            assert bool(stored_by(listed, seeded)) == any(i.component.name == kind.value for i in wanted)

    # === completing ===================================================

    async def test_complete_and_uncomplete(self, collection: AsyncCollection) -> None:
        task = await self.add_task(collection, summary="to do")
        if level(collection, Feature.TASK_STATUS) is Support.UNSUPPORTED:
            before = await snapshot(collection)
            with pytest.raises(LossyWriteError):
                await task.complete()  # a backend without a status cannot store "completed"
            assert await snapshot(collection) == before
            return
        await task.complete()
        assert str(task.component["STATUS"]) == "COMPLETED"
        read = await collection.reload(task)
        assert str(read.component["STATUS"]) == "COMPLETED"
        if level(collection, Feature.TASK_COMPLETED) is not Support.UNSUPPORTED:
            assert "COMPLETED" in read.component
        await task.uncomplete()
        read = await collection.reload(task)
        assert str(read.component["STATUS"]) == "NEEDS-ACTION"
        assert "COMPLETED" not in read.component

    async def test_complete_a_recurring_task(self, collection: AsyncCollection) -> None:
        await self.add_task(collection, summary="setup")
        item = Task.new(summary="weekly", start=at(1), end=at(2))
        item.component.add("RRULE", {"FREQ": "WEEKLY", "COUNT": 4})
        if level(collection, Feature.RECURRENCE) is Support.UNSUPPORTED:
            assert level(collection, Feature.TASK_COMPLETE_RECURRING) is Support.UNSUPPORTED
            return
        task = collection.bind(item)
        task._take(await collection.add(item))
        if level(collection, Feature.TASK_COMPLETE_RECURRING) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.TASK_COMPLETE_RECURRING, lambda: task.complete())
            return
        etag = task.etag
        await task.complete()
        read = await collection.reload(task)
        assert str(read.component.get("STATUS", "")) != "COMPLETED", "completing one occurrence ended the series"
        # "safe" mode (§2.3): a completed copy of the occurrence, and the master's DTSTART moved on.
        assert read.etag != etag, "complete() on a recurring task wrote nothing"
        assert read.component.decoded("DTSTART") > at(1), "the master's DTSTART did not move"
        done = [t for t in await collection.tasks() if str(t.component.get("STATUS", "")) == "COMPLETED"]
        assert any(str(t.component["SUMMARY"]) == "weekly" for t in done), "no completed copy of the occurrence"

    # === recurrence ===================================================

    async def test_save_one_occurrence(self, collection: AsyncCollection) -> None:
        if not writable(collection):
            await self.assert_read_only(collection)
            return
        if not holds(collection, Component.EVENT):
            pytest.skip("the collection holds no events")
        if level(collection, Feature.RECURRENCE) is Support.UNSUPPORTED:
            pytest.skip("nothing recurs: test_expanded_search_returns_occurrences checks the refusal")
        seeded = await self.seed(collection, [i for i in dataset() if str(i.component["SUMMARY"]).startswith("R:")])
        first, _ = stored_by(await collection.search(event=True, start=at(2), end=at(4), expand=True), seeded)
        first.component["SUMMARY"] = "R: moved"
        if level(collection, Feature.RECURRENCE_EDIT_THIS_AND_FUTURE) is Support.UNSUPPORTED:
            await self.assert_unsupported(
                collection,
                Feature.RECURRENCE_EDIT_THIS_AND_FUTURE,
                lambda: collection.save(first, scope=Scope.THIS_AND_FUTURE),
            )
        if level(collection, Feature.RECURRENCE_EDIT_OCCURRENCE) is Support.UNSUPPORTED:
            await self.assert_unsupported(
                collection, Feature.RECURRENCE_EDIT_OCCURRENCE, lambda: collection.save(first)
            )
            return
        await collection.save(first)
        again = stored_by(await collection.search(event=True, start=at(2), end=at(4), expand=True), seeded)
        assert sorted(str(i.component["SUMMARY"]) for i in again) == ["R: daily", "R: moved"]

    async def recurring_series(self, collection: AsyncCollection) -> AsyncEvent:
        """A stored daily series in Europe/Oslo time, with day 3 moved (an override), or the test ends here."""
        if not writable(collection):
            await self.assert_read_only(collection)
            pytest.skip("read-only collection: the write paths were checked to raise")
        if not holds(collection, Component.EVENT):
            pytest.skip("the collection holds no events")
        if level(collection, Feature.RECURRENCE) is Support.UNSUPPORTED:
            pytest.skip("nothing recurs: test_expanded_search_returns_occurrences checks the refusal")
        oslo = ZoneInfo("Europe/Oslo")
        start = at(1, 9).astimezone(oslo)
        series = Event.new(summary="R: daily", start=start, end=start + timedelta(hours=1))
        series.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 5})
        moved = Event.new(summary="R: moved", start=start + timedelta(days=2, hours=5), uid=series.uid)
        moved.component["DTEND"] = icalendar.vDDDTypes(start + timedelta(days=2, hours=6))
        moved.component.add("RECURRENCE-ID", start + timedelta(days=2))
        series.icalendar.add_component(moved.component)
        series.icalendar.add_missing_timezones()
        [stored] = await self.seed(collection, [series])
        assert isinstance(stored, AsyncEvent)
        return stored

    async def occurrences(self, collection: AsyncCollection, series: AsyncItem) -> dict[int, AsyncItem]:
        """The series' occurrences, by day of the month of their RECURRENCE-ID."""
        found = await collection.search(event=True, start=at(1, 0), end=at(9, 0), expand=True)
        return {i.component.decoded("RECURRENCE-ID").day: i for i in found if i.uid == series.uid}

    async def test_delete_one_occurrence(self, collection: AsyncCollection) -> None:
        series = await self.recurring_series(collection)
        before = await self.occurrences(collection, series)
        assert sorted(before) == [1, 2, 3, 4, 5]
        await collection.delete(before[2])
        with pytest.raises(NotFoundError):
            await collection.delete(before[2])  # already gone
        await collection.delete(before[3])  # the moved one
        after = await self.occurrences(collection, series)
        assert sorted(after) == [1, 4, 5]
        stored = await collection.get(series.uid)
        assert not stored.is_occurrence, "deleting an occurrence deleted the series"
        dtstart = stored.component["DTSTART"]
        exdates = stored.component.get("EXDATE")
        exdates = exdates if isinstance(exdates, list) else [exdates]
        assert sorted(d.dt for e in exdates for d in e.dts) == [
            before[2].component.decoded("RECURRENCE-ID"),
            before[3].component.decoded("RECURRENCE-ID"),
        ]
        assert all(e.params.get("TZID") == dtstart.params.get("TZID") for e in exdates), "EXDATE not in DTSTART's form"
        assert not [c for c in stored.icalendar.walk("VEVENT") if "RECURRENCE-ID" in c], "the override was kept"

    async def test_delete_an_occurrence_read_before_a_sibling_was_deleted(self, collection: AsyncCollection) -> None:
        series = await self.recurring_series(collection)
        read = await self.occurrences(collection, series)
        await collection.delete(read[4])
        await collection.delete(read[5])  # its etag is stale, but the occurrence itself is unchanged (Q12)
        assert sorted(await self.occurrences(collection, series)) == [1, 2, 3]

    async def test_delete_an_occurrence_changed_since_it_was_read(self, collection: AsyncCollection) -> None:
        series = await self.recurring_series(collection)
        read = await self.occurrences(collection, series)
        series.component["SUMMARY"] = "R: renamed"
        await collection.save(series)
        with pytest.raises(ConflictError):
            await collection.delete(read[2])
        await collection.delete(read[2], overwrite=True)
        assert sorted(await self.occurrences(collection, series)) == [1, 3, 4, 5]

    # === relatives ====================================================

    async def test_relatives(self, collection: AsyncCollection) -> None:
        parent = await self.add_task(collection, summary="parent")
        data = Task.new(summary="child")
        data.component.add("RELATED-TO", parent.uid, parameters={"RELTYPE": "PARENT"})
        if level(collection, Feature.TASK_RELATIONS_PARENT) is Support.UNSUPPORTED:
            with pytest.raises(LossyWriteError):
                await collection.add(data)
            return
        child = await collection.add(data)
        assert [r.uid for r in await child.relatives()] == [parent.uid]
        assert [r.uid for r in await child.relatives("PARENT")] == [parent.uid]
        assert await child.relatives("CHILD") == []
        assert [r.uid for r in await parent.relatives()] == [child.uid]
        assert [r.uid for r in await parent.relatives("CHILD")] == [child.uid]
        assert await parent.relatives("PARENT") == []
        assert all(r.collection is collection for r in await parent.relatives())

    # === change detection =============================================

    async def test_changes_full_listing(self, collection: AsyncCollection) -> None:
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.CHANGES, lambda: collection.changes())
            return
        kind = next(k for k in KINDS if holds(collection, k))
        seeded = await self.seed(collection, [minimal(kind, s) for s in ("one", "two")])
        changes = await collection.changes()
        assert {i.native_id for i in changes.changed} >= {i.native_id for i in seeded}
        assert changes.deleted == []
        assert isinstance(changes.token, str)
        assert all(isinstance(i, AsyncItem) and i.collection is collection for i in changes.changed)

    async def test_changes_since_a_token(self, collection: AsyncCollection) -> None:
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            await self.assert_unsupported(
                collection, Feature.CHANGES, lambda: collection.changes(SyncToken("conformance-token"))
            )
            return
        keep, change, drop = [await self.add_task(collection, summary=s) for s in ("keep", "change", "drop")]
        start = await collection.changes()
        change.component["SUMMARY"] = "changed"
        await change.save()
        await drop.delete()
        added = await collection.add_task(summary="added")
        since = await collection.changes(start.token)
        assert {i.native_id for i in since.changed} == {change.native_id, added.native_id}
        assert since.deleted == [drop.native_id]
        assert keep.native_id not in since.deleted
        quiet = await collection.changes(since.token)
        assert quiet.changed == []
        assert quiet.deleted == []

    async def test_changes_against_known(self, collection: AsyncCollection) -> None:
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.CHANGES, lambda: collection.changes(known={}))
            return
        keep, change, drop = [await self.add_task(collection, summary=s) for s in ("keep", "change", "drop")]
        known = {nid(i): str(i.etag) for i in (keep, change, drop)}
        if level(collection, Feature.CHANGES_KNOWN) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.CHANGES_KNOWN, lambda: collection.changes(known=known))
            return
        change.component["SUMMARY"] = "changed"
        await change.save()
        await drop.delete()
        added = await collection.add_task(summary="added")
        result = await collection.changes(known=known)
        assert {i.native_id for i in result.changed} == {change.native_id, added.native_id}
        assert result.deleted == [drop.native_id]
        with pytest.raises(ValueError, match="known"):
            await collection.changes(result.token, known=known)
        later = await collection.add_task(summary="later")
        following = await collection.changes(result.token)  # a known= token is a token like any other
        assert {i.native_id for i in following.changed} == {later.native_id}
        assert following.deleted == []

    async def test_a_bad_token_raises(self, collection: AsyncCollection) -> None:
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            await self.assert_unsupported(
                collection, Feature.CHANGES, lambda: collection.changes(SyncToken("conformance-not-a-token"))
            )
            return
        with pytest.raises(SyncTokenError):
            await collection.changes(SyncToken("conformance-not-a-token"))

    async def test_a_token_from_another_collection_raises(
        self, backend: AsyncBackend, collection: AsyncCollection
    ) -> None:
        if level(collection, Feature.CHANGES) is Support.UNSUPPORTED:
            return  # test_a_bad_token_raises checked that changes() raises
        if not backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            pytest.skip("a second collection is needed, and the backend cannot make one")
        other = await self.open_collection(backend, "conformance-other")
        if level(other, Feature.CHANGES) is Support.UNSUPPORTED:
            pytest.skip("the second collection has no change feed")
        token = (await other.changes()).token
        with pytest.raises(SyncTokenError):
            await collection.changes(token)

    # === time tracking ================================================

    async def logged_against(self, collection: AsyncCollection) -> AsyncItem:
        """A stored task (an event where there are no tasks) to log time against, or the test ends here."""
        kind = Component.TASK if holds(collection, Component.TASK) else Component.EVENT
        if not holds(collection, kind):
            pytest.skip("the collection holds neither tasks nor events")
        [item] = await self.seed(collection, [KINDS[kind][0].new(summary="logged against", start=at(1, 8))])
        return item

    async def assert_time_log_unsupported(self, collection: AsyncCollection, item: AsyncItem) -> bool:
        """True if the time log is unsupported here, after checking that logging raises before any I/O."""
        if level(collection, Feature.TIME_LOG) is not Support.UNSUPPORTED:
            return False
        await self.assert_unsupported(collection, Feature.TIME_LOG, lambda: item.log_time(spent=HOUR))
        await self.assert_unsupported(collection, Feature.TIME_LOG, lambda: item.time_log())
        return True

    def lost(self, collection: AsyncCollection) -> set[str]:
        return set(collection.capabilities.details(Feature.TIME_LOG).get("lost", ()))

    def total_only(self, collection: AsyncCollection) -> bool:
        return bool(collection.capabilities.details(Feature.TIME_LOG).get("total-only"))

    async def test_time_log_round_trips_as_declared(self, collection: AsyncCollection) -> None:
        item = await self.logged_against(collection)
        if await self.assert_time_log_unsupported(collection, item):
            return
        assert await item.time_log() == []
        assert await item.time_spent() == timedelta(0)
        if not writable(collection):
            await self.assert_unsupported(collection, Feature.WRITE, lambda: item.log_time(spent=HOUR))
            return
        users = ("mailto:alice@example.com", "mailto:bob@example.com")
        logged = await item.log_time(start=at(3, 9), end=at(3, 11), users=users, comment="first draft\nwith figures")
        assert logged.duration == 2 * HOUR
        assert not any(is_entry(r.component) for r in await item.relatives()), "a time entry is no subtask"
        [read] = await item.time_log()
        if self.total_only(collection):
            assert await item.time_spent() == 4 * HOUR, "two people for two hours, kept as a total"
            assert read.start is None, "a total-only backend has no entries to give a start"
            return
        assert read.duration == 2 * HOUR
        if "users" not in self.lost(collection):
            assert await item.time_spent() == 4 * HOUR, "person-time: two people for two hours"
        lost = self.lost(collection)
        expected: dict[str, tuple[object, object]] = {
            "start-time": (read.start, at(3, 9)),
            "end": (read.end, at(3, 11)),
        }
        expected |= {"users": (read.users, users), "comment": (read.comment, "first draft\nwith figures")}
        for name, (got, sent) in expected.items():
            if name in lost:
                assert got in (None, ()), f"{name} is declared lost, yet read back as {got!r}: never invent it"
            else:
                assert got == sent, f"{name}: sent {sent!r}, read back {got!r}"
        assert read.id is not None

    async def test_log_time_takes_two_of_three(self, collection: AsyncCollection) -> None:
        item = await self.logged_against(collection)
        if await self.assert_time_log_unsupported(collection, item) or not writable(collection):
            return
        before = await snapshot(collection)
        with pytest.raises(ValueError, match="disagree"):
            await item.log_time(spent=3 * HOUR, start=at(3, 9), end=at(3, 11))
        with pytest.raises(ValueError, match="two of"):
            await item.log_time(start=at(3, 9))
        assert await snapshot(collection) == before, "a refused entry wrote something"
        by_start = await item.log_time(spent=HOUR, start=at(3, 9))
        by_end = await item.log_time(spent=HOUR, end=at(4, 11))
        recent = await item.log_time(spent=HOUR)
        assert [e.duration for e in (by_start, by_end, recent)] == [HOUR] * 3
        assert await item.time_spent() == 3 * HOUR
        if not self.total_only(collection) and "start-time" not in self.lost(collection):
            assert (by_start.start, by_start.end) == (at(3, 9), at(3, 10))
            assert (by_end.start, by_end.end) == (at(4, 10), at(4, 11))

    async def test_a_day_of_durations_lines_up(self, collection: AsyncCollection) -> None:
        item = await self.logged_against(collection)
        if await self.assert_time_log_unsupported(collection, item) or not writable(collection):
            return
        tuesday = at(3).date()
        first = await item.log_time(spent=3 * HOUR, day=tuesday)
        second = await item.log_time(spent=2 * HOUR, day=tuesday)
        assert await item.time_spent() == 5 * HOUR
        if self.total_only(collection) or "start-time" in self.lost(collection):
            return
        assert first.start is not None
        assert first.start.date() == tuesday
        assert second.start == first.end, "the day's entries line up back to back"

    async def test_the_timer(self, collection: AsyncCollection) -> None:
        if not holds(collection, Component.TASK):
            pytest.skip("the timer is for tasks, and the collection holds none")
        [task] = await self.seed(collection, [Task.new(summary="timed")])
        assert isinstance(task, AsyncTask)
        if level(collection, Feature.TIME_LOG) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.TIME_LOG, lambda: task.start_timer())
            return
        if not writable(collection):
            await self.assert_unsupported(collection, Feature.WRITE, lambda: task.start_timer())
            return
        await task.start_timer(at=at(3, 9))
        with pytest.raises(ValueError, match="running"):
            await task.start_timer()
        reread = await collection.reload(task)
        assert isinstance(reread, AsyncTask)
        [running] = await reread.time_log()
        assert running.running
        assert running.start == at(3, 9)
        entry = await task.stop_timer(at=at(3, 10, 30), comment="done for now")
        assert entry.duration == timedelta(hours=1, minutes=30)
        with pytest.raises(ValueError, match="running"):
            await task.stop_timer()
        [stored] = await (await collection.reload(task)).time_log()
        assert not stored.running
        assert stored.duration == timedelta(hours=1, minutes=30)
        assert "X-PYCAL-TIMER-START" not in (await collection.reload(task)).component, "the timer is temporary"

    async def test_amend_and_delete_time(self, collection: AsyncCollection) -> None:
        item = await self.logged_against(collection)
        if await self.assert_time_log_unsupported(collection, item) or not writable(collection):
            return
        entry = await item.log_time(start=at(3, 9), end=at(3, 10), comment="first")
        if self.total_only(collection):
            [read] = await item.time_log()
            await self.assert_unsupported(collection, Feature.TIME_LOG, lambda: item.amend_time(read, comment="x"))
            await self.assert_unsupported(collection, Feature.TIME_LOG, lambda: item.delete_time(read))
            return
        amended = await item.amend_time(entry, spent=2 * HOUR)
        assert amended.duration == 2 * HOUR
        assert amended.id == entry.id
        [read] = await item.time_log()
        assert read.duration == 2 * HOUR
        if "comment" not in self.lost(collection):
            assert read.comment == "first", "amending one field kept the others"
        await item.delete_time(read)
        assert await item.time_log() == []

    async def test_time_spent_in_a_meeting(self, collection: AsyncCollection) -> None:
        if not holds(collection, Component.EVENT):
            pytest.skip("the collection holds no events")
        [meeting] = await self.seed(collection, [Event.new(summary="planning", start=at(5, 9), end=at(5, 10))])
        if await self.assert_time_log_unsupported(collection, meeting) or not writable(collection):
            return
        before = await collection.reload(meeting)  # as stored: a server may add to what was sent
        await meeting.log_time(start=at(5, 9), end=at(5, 10), users=("mailto:alice@example.com",))
        [entry] = await meeting.time_log()
        assert entry.duration == HOUR
        assert await collection.reload(meeting) == before, "logging time does not touch the meeting itself"

    async def test_time_entries_in_a_range(self, collection: AsyncCollection) -> None:
        item = await self.logged_against(collection)
        if await self.assert_time_log_unsupported(collection, item) or not writable(collection):
            return
        if self.total_only(collection) or "start-time" in self.lost(collection):
            pytest.skip("entries without a start cannot be searched by time")
        inside = await item.log_time(start=at(10, 9), end=at(10, 10))
        await item.log_time(start=at(12, 9), end=at(12, 10))
        found = await collection.time_entries(at(10, 0), at(11, 0))
        assert [(uid, e.id) for uid, e in found] == [(item.uid, inside.id)]

    # === moving =======================================================

    async def test_move(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        task = await self.add_task(collection, summary="moving")
        if level(collection, Feature.MOVE) is Support.UNSUPPORTED:
            await self.assert_unsupported(collection, Feature.MOVE, lambda: collection.move(task, collection))
            return
        if not backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            pytest.skip("a second collection is needed, and the backend cannot make one")
        target = await self.open_collection(backend, "conformance-target")
        moved = await collection.move(task, target)
        assert moved.collection is target
        assert moved.collection_id == target.id
        with pytest.raises(NotFoundError):
            await collection.get_by_native_id(nid(task))
        assert (await target.get_by_native_id(nid(moved))).uid == moved.uid

    async def test_changes_follow_a_move(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        if Support.UNSUPPORTED in (level(collection, Feature.CHANGES), level(collection, Feature.MOVE)):
            return  # the tests of each feature checked that it raises
        task = await self.add_task(collection, summary="moving")
        if not backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            pytest.skip("a second collection is needed, and the backend cannot make one")
        target = await self.open_collection(backend, "conformance-target")
        if level(target, Feature.CHANGES) is Support.UNSUPPORTED:
            pytest.skip("the second collection has no change feed")
        here, there = await collection.changes(), await target.changes()
        elsewhere = await target.add_task(summary="added there")
        moved = await collection.move(task, target)
        since_here = await collection.changes(here.token)
        since_there = await target.changes(there.token)
        assert since_here.deleted == [nid(task)], "a moved item is deleted where it was"
        assert not {nid(elsewhere), nid(moved)} & {nid(i) for i in since_here.changed}
        assert {nid(moved), nid(elsewhere)} <= {nid(i) for i in since_there.changed}
        assert since_there.deleted == []

    # === collections ==================================================

    async def test_set_name(self, backend: AsyncBackend, collection: AsyncCollection) -> None:
        if level(collection, Feature.COLLECTION_SET_NAME) is Support.UNSUPPORTED:
            with pytest.raises(UnsupportedError) as info:
                await collection.set_name("renamed")
            assert info.value.feature == Feature.COLLECTION_SET_NAME
            return
        name = collection.name
        await collection.set_name("conformance renamed")
        try:
            assert collection.name == "conformance renamed"
            assert (await backend.collection(collection.id)).name == "conformance renamed"
        finally:
            if name is not None:  # a borrowed or reused collection is put back as it was
                await collection.set_name(name)

    async def test_create_collection_holds_one_of_what_was_asked(self, backend: AsyncBackend) -> None:
        """A collection holding none of the requested components is refused, not made."""
        if not backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            pytest.skip("the backend makes no collections")
        deletable = backend.capabilities.level(Feature.DELETE_COLLECTION) is not Support.UNSUPPORTED
        if not deletable and await self.left_behind(backend, "conformance-events") is not None:
            return  # the run that left it checked it
        before = [c.id for c in await backend.collections()]
        refused: UnsupportedError | None = None
        try:
            made = await backend.create_collection("conformance-events", components=(Component.EVENT,))
        except UnsupportedError as exc:
            refused = exc
        if refused is not None:
            assert refused.feature == Feature.COMPONENT_EVENT
            if deletable:
                assert [c.id for c in await backend.collections()] == before, "a refused collection was left behind"
            return
        if deletable:
            self._made.append(made)
        assert Component.EVENT in made.components

    async def test_create_and_delete_collection(self, backend: AsyncBackend) -> None:
        before = [c.id for c in await backend.collections()]
        if backend.capabilities.level(Feature.CREATE_COLLECTION) is Support.UNKNOWN:
            deletable = backend.capabilities.level(Feature.DELETE_COLLECTION) is not Support.UNSUPPORTED
            attempted = None if deletable else await self.left_behind(backend, "conformance-new")
            if attempted is None:
                try:
                    attempted = await backend.create_collection("conformance-new", components=(Component.TASK,))
                except (UnsupportedError, BackendError):
                    return  # unprobed, and it does not work here: attempted, as UNKNOWN promises
                if deletable:
                    self._made.append(attempted)
            assert attempted.components == frozenset({Component.TASK})
            assert attempted.id in [c.id for c in await backend.collections()]
            for kind, (_, _, _, _, feature) in KINDS.items():
                assert (kind == Component.TASK) == (level(attempted, feature) is not Support.UNSUPPORTED), feature
            return
        if not backend.capabilities.supports(Feature.CREATE_COLLECTION, allow_lossy=True):
            with pytest.raises(UnsupportedError) as info:
                await backend.create_collection("conformance-new")
            assert info.value.feature == Feature.CREATE_COLLECTION
            assert [c.id for c in await backend.collections()] == before
            return
        deletable = backend.capabilities.supports(Feature.DELETE_COLLECTION)
        # Without delete-collection, only the first run on a backend makes it: later ones find it left there.
        made = None if deletable else await self.left_behind(backend, "conformance-new")
        lossy = backend.capabilities.level(Feature.CREATE_COLLECTION) is Support.LOSSY
        if made is None:
            refused: UnsupportedError | None = None
            try:
                made = await backend.create_collection("conformance-new", components=(Component.TASK,))
            except UnsupportedError as exc:
                if not lossy:
                    raise
                refused = exc
            if refused is not None:
                # The requested set is not kept, and what was made held no tasks (Bedework: events only).
                assert refused.feature == Feature.COMPONENT_TASK
                if deletable:
                    assert [c.id for c in await backend.collections()] == before, "a refused collection was left behind"
                return
        assert made is not None
        if not lossy:  # where it is, create_collection() saw to it that tasks are among what it holds
            assert made.components == frozenset({Component.TASK})
        assert made.id in [c.id for c in await backend.collections()]
        for kind, (_, _, _, _, feature) in KINDS.items():
            assert (kind in made.components) == (level(made, feature) is not Support.UNSUPPORTED), feature
        if not deletable:
            with pytest.raises(UnsupportedError) as info:
                await made.delete_collection()
            assert info.value.feature == Feature.DELETE_COLLECTION
            return
        self._made.append(made)
        await made.delete_collection()
        self._made.remove(made)
        assert made.id not in [c.id for c in await backend.collections()]

    # === binding ======================================================

    async def test_bind_and_as_data(self, collection: AsyncCollection) -> None:
        stored = await self.add_task(collection, summary="bound")
        data = stored.as_data()
        assert type(data) is Task
        assert not hasattr(data, "collections")
        assert data == stored, "equality compares the data, never the collections"
        assert pickle.loads(pickle.dumps(data)) == data
        with pytest.raises(TypeError):
            pickle.dumps(stored)
        copied = stored.copy()
        assert copied.collection is collection
        assert copied is not stored
        before = await snapshot(collection)
        bound = collection.bind(Task.new(summary="not stored"))
        assert isinstance(bound, AsyncTask)
        assert bound.collection is collection
        assert await snapshot(collection) == before, "bind() did I/O"
