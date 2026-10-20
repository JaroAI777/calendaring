# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""The generic layer, beyond what the conformance suite checks of every backend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from calendaring import (
    AsyncBackend,
    AsyncCollection,
    AsyncTask,
    AsyncWorkspace,
    BackendError,
    ConfigurationError,
    ConflictError,
    Event,
    Feature,
    LossyWriteWarning,
    MultiResult,
    NotFoundError,
    Support,
    SyncTokenError,
    Task,
    UnsupportedError,
    VerificationError,
)
from calendaring.loss import WARN
from calendaring.memory import TRACKER, AsyncMemoryBackend, AsyncMemoryCollection, MemoryObject


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


async def test_connect_picks_the_kind_from_the_url() -> None:
    backend = await AsyncBackend.connect("memory:", id="from-url")
    assert isinstance(backend, AsyncMemoryBackend)
    assert backend.id == "from-url"
    with pytest.raises(ConfigurationError):
        await AsyncBackend.connect("https://caldav.example.com/")
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
