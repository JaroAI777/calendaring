# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The generic layer, beyond what the conformance suite checks of every backend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from icalendar_searcher import Searcher

from calendaring import (
    AsyncBackend,
    AsyncCollection,
    AsyncTask,
    AsyncWorkspace,
    BackendError,
    BrokenObject,
    Component,
    ConfigurationError,
    ConflictError,
    Event,
    Feature,
    InvalidDataError,
    LossyWriteWarning,
    MultiResult,
    NotFoundError,
    PartialResultError,
    Support,
    SyncTokenError,
    Task,
    UnsupportedError,
    VerificationError,
)
from calendaring.loss import WARN
from calendaring.memory import TRACKER, AsyncMemoryBackend, AsyncMemoryCollection, MemoryObject
from calendaring.types import StoredObject


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


async def test_connect_picks_the_kind_from_the_url() -> None:
    backend = await AsyncBackend.connect("memory:", id="from-url")
    assert isinstance(backend, AsyncMemoryBackend)
    assert backend.id == "from-url"
    with pytest.raises(ConfigurationError):
        await AsyncBackend.connect("gopher://calendar.example.com/")
    with pytest.raises(ConfigurationError):
        await AsyncBackend.connect("memory:", kind="no-such-kind")


async def test_verify_catches_a_silent_drop() -> None:
    backend = AsyncMemoryBackend(silently_drop=["CATEGORIES"])
    collection = await backend.collection("default")
    task = Task.new(summary="x", categories=["work"])
    await collection.add(task)  # not verified: nobody notices
    with pytest.raises(VerificationError) as info:
        await collection.add(Task.new(summary="y", categories=["work"]), verify=True)
    assert [d.property for d in info.value.differences] == ["CATEGORIES"]
    assert info.value.backend == "memory"


async def test_backend_and_workspace_defaults() -> None:
    lying = AsyncMemoryBackend(id="lying", silently_drop=["CATEGORIES"])
    tracker = AsyncMemoryBackend(TRACKER, id="tracker", loss=None)
    AsyncWorkspace([lying, tracker], loss=WARN, verify=True)
    with pytest.raises(VerificationError):
        await (await lying.collection("default")).add_task(summary="x", categories=["a"])
    collection = await tracker.collection("default")
    with pytest.warns(LossyWriteWarning):
        await collection.add_task(summary="x", priority=3)


async def test_bind_replaces_the_collection() -> None:
    backend = AsyncMemoryBackend()
    first = await backend.collection("default")
    second = await backend.create_collection("second")
    stored = await first.add_task(summary="x")
    rebound = second.bind(stored)
    assert rebound.collections == (second,)
    assert rebound.native is None
    assert stored.collection is first


async def test_wrap_takes_the_native_object() -> None:
    backend = AsyncMemoryBackend()
    collection = await backend.collection("default")
    stored = await collection.add_task(summary="x")
    assert stored.native_id is not None
    native = (await collection.get_by_native_id(stored.native_id)).native
    assert isinstance(native, MemoryObject)
    wrapped = collection.wrap(native)
    assert isinstance(wrapped, AsyncTask)
    assert wrapped.uid == stored.uid
    with pytest.raises(UnsupportedError):
        collection.wrap(object())


async def test_bound_to_several_collections_is_unsupported() -> None:
    backend = AsyncMemoryBackend()
    collection = await backend.collection("default")
    task = Task.new(summary="x")
    with pytest.raises(UnsupportedError):
        AsyncTask(task.icalendar, collections=(collection, collection))


class Unreachable(AsyncMemoryBackend):
    async def collections(self) -> list[AsyncCollection]:
        raise BackendError("connection refused")


async def test_fan_out_returns_partial_results() -> None:
    good = AsyncMemoryBackend(id="good")
    await (await good.collection("default")).add_task(summary="found")
    async with AsyncWorkspace([good, Unreachable(id="down")]) as workspace:
        listed = await workspace.collections()
        assert [c.id for c in listed.items] == ["default"]
        assert set(listed.errors) == {"down"}
        found = await workspace.search(todo=True)
        assert [str(i.component["SUMMARY"]) for i in found.items] == ["found"]
        assert set(found.errors) == {"down"}
        with pytest.raises(ExceptionGroup):
            found.raise_for_errors()
        assert (await workspace.collection("default")).backend_id == "good"
        with pytest.raises(NotFoundError):
            await workspace.collection("nope")
    MultiResult().raise_for_errors()


async def test_errors_from_primitives_name_their_source() -> None:
    backend = AsyncMemoryBackend(id="named")
    collection = await backend.collection("default")
    assert isinstance(collection, AsyncMemoryCollection)
    with pytest.raises(NotFoundError) as info:
        await collection.get_by_native_id("missing")
    assert (info.value.backend, info.value.collection_id) == ("named", "default")


async def test_reload_finds_a_moved_override() -> None:
    backend = AsyncMemoryBackend()
    collection = await backend.collection("default")
    series = Event.new(summary="daily", start=at(1, 9), end=at(1, 10))
    series.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 5})
    moved = Event.new(summary="moved", start=at(3, 15), end=at(3, 16), uid=series.uid)
    moved.component.add("RECURRENCE-ID", at(3, 9))
    series.icalendar.add_component(moved.component)
    await collection.add(series)
    [occurrence] = await collection.search(event=True, start=at(3, 14), end=at(3, 17), expand=True)
    assert str(occurrence.component["SUMMARY"]) == "moved"
    reloaded = await collection.reload(occurrence)
    assert str(reloaded.component["SUMMARY"]) == "moved"
    assert reloaded.is_occurrence
    generated = (await collection.search(event=True, start=at(2, 0), end=at(2, 23), expand=True))[0]
    assert isinstance(generated, Event)
    assert (await collection.reload(generated)).component.start == at(2, 9)


async def test_a_collection_narrows_a_backend_level_entry() -> None:
    backend = AsyncMemoryBackend()
    backend.capabilities = backend.capabilities.updated({Feature.WRITE: Support.FULL})
    collection = await backend.collection("default")
    collection.capabilities = collection.capabilities.updated({Feature.WRITE: Support.UNSUPPORTED})
    with pytest.raises(UnsupportedError):
        await collection.add_task(summary="x")


async def test_completed_is_utc() -> None:
    collection = await AsyncMemoryBackend().collection("default")
    task = await collection.add_task(summary="x")
    oslo = timezone(timedelta(hours=2))
    await task.complete(at=datetime(2026, 11, 1, 14, tzinfo=oslo))
    assert b"COMPLETED:20261101T120000Z" in task.to_ical()


async def test_deleting_an_occurrence_is_a_library_limitation() -> None:
    collection = await AsyncMemoryBackend().collection("default")
    series = Event.new(summary="daily", start=at(1, 9), end=at(1, 10))
    series.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 5})
    await collection.add(series)
    first = (await collection.search(event=True, start=at(2, 0), end=at(2, 23), expand=True))[0]
    with pytest.raises(UnsupportedError) as info:
        await collection.delete(first)
    assert info.value.feature is None
    with pytest.raises(ValueError, match="wire"):
        await collection.save(first, wire=b"")


async def test_save_without_an_etag_needs_overwrite() -> None:
    collection = await AsyncMemoryBackend().collection("default")
    stored = await collection.add_task(summary="x")
    stored.etag = None
    with pytest.raises(ValueError, match="etag"):
        await collection.save(stored)
    await collection.save(stored, overwrite=True)


async def test_workspace_defaults_stay_the_workspace_s() -> None:
    backend = AsyncMemoryBackend(silently_drop=["CATEGORIES"])
    AsyncWorkspace([backend], verify=True)
    assert backend.verify is None, "the backend's own setting is untouched"
    with pytest.raises(VerificationError):
        await (await backend.collection("default")).add_task(summary="x", categories=["a"])


async def test_move_between_backends_checks_the_etag_before_copying() -> None:
    source = await AsyncMemoryBackend(id="source").collection("default")
    target = await AsyncMemoryBackend(TRACKER, id="tracker").collection("default")
    task = await source.add_task(summary="file taxes")
    other = await source.get(task.uid)
    other.component["SUMMARY"] = "file taxes (by 31 Oct)"
    await other.save()
    for _ in range(2):
        with pytest.raises(ConflictError):
            await source.move(task, target)
    assert await target.search() == [], "a move refused for a stale etag left a copy behind"


class LosesTheRace(AsyncMemoryCollection):
    """Another client edits the item between the copy and the delete."""

    async def _delete(self, native_id: str, *, etag: str | None) -> None:
        raise ConflictError(f"{native_id!r} changed since it was read")


async def test_a_move_that_loses_the_race_removes_its_copy() -> None:
    class Backend(AsyncMemoryBackend):
        collection_class = LosesTheRace

    source = await Backend(id="source").collection("default")
    target = await AsyncMemoryBackend(id="target").collection("default")
    task = await source.add_task(summary="x")
    with pytest.raises(ConflictError):
        await source.move(task, target)
    assert await target.search() == []


async def test_a_token_from_another_backend_is_refused() -> None:
    work = await AsyncMemoryBackend(id="work").collection("default")
    home = await AsyncMemoryBackend(id="home").collection("default")
    for summary in ("w1", "w2"):
        await work.add_task(summary=summary)
    token = (await work.changes()).token
    with pytest.raises(SyncTokenError):
        await home.changes(token)
    twin = await AsyncMemoryBackend(id="work").collection("default")
    with pytest.raises(SyncTokenError):
        await twin.changes(token)


BAD_TODO = (  # DUE:2026-10-20 is not a valid DATE; some client wrote it
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//some client//EN\r\nBEGIN:VTODO\r\nUID:from-phone\r\n"
    b"DTSTAMP:20261001T000000Z\r\nSUMMARY:buy milk\r\nDUE:2026-10-20\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
)

TWO_MASTERS = (  # parses, but is no recurrence set: the searcher cannot match it
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n"
    b"BEGIN:VEVENT\r\nUID:twice\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261105T090000Z\r\n"
    b"RRULE:FREQ=DAILY;COUNT=2\r\nEND:VEVENT\r\n"
    b"BEGIN:VEVENT\r\nUID:twice\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261106T090000Z\r\n"
    b"RRULE:FREQ=DAILY;COUNT=2\r\nEND:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)


async def test_an_object_that_does_not_parse_gives_partial_results() -> None:
    collection = await AsyncMemoryBackend().collection("default")
    assert isinstance(collection, AsyncMemoryCollection)
    good = await collection.add_task(summary="good task")
    bad = collection.store.put(BAD_TODO)
    for call in (collection.search, collection.tasks):
        with pytest.raises(PartialResultError) as info:
            await call()
        assert [i.uid for i in info.value.results] == [good.uid]
        assert [(b.native_id, b.wire) for b in info.value.broken] == [(bad, BAD_TODO)]
        assert info.value.collection_id == "default"
    streamed = []

    async def drain() -> None:
        async for item in collection.iter_search():
            streamed.append(item)

    with pytest.raises(PartialResultError) as info:
        await drain()
    assert [i.uid for i in streamed] == [good.uid]
    assert info.value.results == [], "iter_search() already gave them; it keeps none"
    assert (await collection.get(good.uid)).native_id == good.native_id
    with pytest.raises(PartialResultError):
        await collection.get("not-there")
    with pytest.raises(PartialResultError) as info:
        await collection.changes()
    assert [i.uid for i in info.value.results.changed] == [good.uid]
    assert info.value.results.deleted == []
    [broken] = info.value.broken
    assert (broken.native_id, broken.etag, broken.wire) == (bad, collection.store.objects[bad].etag, BAD_TODO)
    quiet = await collection.changes(info.value.results.token)
    assert (quiet.changed, quiet.deleted) == ([], [])
    with pytest.raises(InvalidDataError):
        await collection.get_by_native_id(bad)
    await collection.add_task(summary="added after")  # the store still checks UIDs past the broken one


class ListsWithoutEtags(AsyncMemoryCollection):
    """Lists a broken object without its etag, as a backend that reads etags from the content would."""

    async def _query(self, searcher: Searcher | None) -> list[StoredObject | BrokenObject]:
        out = list(await super()._query(searcher))
        return [BrokenObject(s.native_id, None, s.wire, s.error) if isinstance(s, BrokenObject) else s for s in out]

    _list_etags = AsyncCollection._list_etags


async def test_a_broken_object_is_never_reported_deleted() -> None:
    class Backend(AsyncMemoryBackend):
        collection_class = ListsWithoutEtags

    collection = await Backend().collection("default")
    assert isinstance(collection, AsyncMemoryCollection)
    await collection.add_task(summary="good")
    bad = collection.store.put(BAD_TODO)
    with pytest.raises(PartialResultError) as info:
        await collection.changes()
    with pytest.raises(PartialResultError) as again:
        await collection.changes(known={bad: "an etag from before"})
    assert again.value.results.deleted == []
    assert bad not in (await collection.changes(info.value.results.token)).deleted


async def test_relatives_get_past_a_broken_object() -> None:
    backend = AsyncMemoryBackend()
    collection = await backend.collection("default")
    other = await backend.create_collection("other", components=[Component.TASK])
    assert isinstance(collection, AsyncMemoryCollection)
    parent = await other.add_task(summary="parent")
    child = await collection.add_task(summary="child", related_to=parent.uid)
    collection.store.put(BAD_TODO)
    with pytest.raises(PartialResultError) as info:
        await child.relatives()
    assert [r.uid for r in info.value.results] == [parent.uid]


async def test_an_object_the_searcher_cannot_match_gives_partial_results() -> None:
    work = AsyncMemoryBackend(id="work")
    home = AsyncMemoryBackend(id="home")
    collection = await work.collection("default")
    assert isinstance(collection, AsyncMemoryCollection)
    await collection.add_event(summary="unrelated", start=at(6))
    [home_default] = await home.collections()
    await home_default.delete_collection()  # so that only work's collection is "default"
    await (await home.create_collection("personal")).add_event(summary="dentist", start=at(7))
    odd = collection.store.put(TWO_MASTERS)
    with pytest.raises(PartialResultError) as info:
        await collection.events()
    assert [str(i.component["SUMMARY"]) for i in info.value.results] == ["unrelated"]
    assert [b.native_id for b in info.value.broken] == [odd]
    found = await AsyncWorkspace([work, home]).search()
    assert sorted(str(i.component["SUMMARY"]) for i in found.items) == ["dentist", "unrelated"]
    assert isinstance(found.errors["default"], PartialResultError)


class DefaultTokenScope(AsyncMemoryCollection):
    def _token_scope(self) -> str:
        return AsyncCollection._token_scope(self)


async def test_the_default_token_scope_is_kind_backend_and_collection() -> None:
    class Backend(AsyncMemoryBackend):
        collection_class = DefaultTokenScope

    work = await Backend(id="work").collection("default")
    assert work._token_scope() == "memory:work:default"
    token = (await work.changes()).token
    with pytest.raises(SyncTokenError):
        await (await Backend(id="home").collection("default")).changes(token)
    # Another store under the same label: the default cannot tell them apart.
    await (await Backend(id="work").collection("default")).changes(token)


async def test_a_backend_from_a_missing_extra_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AsyncBackend, "_kinds", {k: v for k, v in AsyncBackend._kinds.items() if k != "caldav"})
    with pytest.raises(ConfigurationError, match=r"pip install 'calendaring\[caldav\]'"):
        await AsyncBackend.connect("https://caldav.example.com/", kind="caldav")


async def test_http_urls_are_caldav_until_the_feed_probe_exists() -> None:
    pytest.importorskip("caldav")
    backend = await AsyncBackend.connect("https://caldav.example.com/dav/", username="u", password="p")
    async with backend:
        assert backend.kind == "caldav"


async def test_get_on_overrides_without_a_master_returns_the_earliest() -> None:
    backend = AsyncMemoryBackend()
    collection = await backend.collection("default")
    assert isinstance(collection, AsyncMemoryCollection)
    later = Event.new(summary="later", start=at(5, 9), end=at(5, 10), uid="invited")
    later.component.add("RECURRENCE-ID", at(5, 9))
    earlier = Event.new(summary="earlier", start=at(2, 9), end=at(2, 10), uid="invited")
    earlier.component.add("RECURRENCE-ID", at(2, 9))
    later.icalendar.add_component(earlier.component)
    collection.store.put(later.to_ical())
    found = await collection.get("invited")
    assert found.is_occurrence
    assert str(found.component["SUMMARY"]) == "earlier"
