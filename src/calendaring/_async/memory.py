# Hand-written source; tools/generate_sync.py derives ../_sync/memory.py from this file.
"""The in-memory reference backend (roadmap 1.2): what the conformance suite has to pass against.

It implements only the primitives of the ABCs, so everything else the suite
checks comes from the generic layer.  Its behaviour follows from a
``Profile`` (a capability declaration), so one backend exercises both
branches of the capability-aware tests.  It is also usable in an
application's own tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, ClassVar

import icalendar
from icalendar_searcher import Searcher

from .._memory_store import FULL, MemoryCalendar, MemoryObject, MemoryStore, Profile
from ..capabilities import Capabilities, Feature, Support
from ..errors import ConflictError, InvalidDataError
from ..items import Item
from ..loss import LossPolicy
from ..types import BrokenObject, Component, StoredObject
from .backend import AsyncBackend
from .collection import AsyncCollection

__all__ = ["AsyncMemoryBackend", "AsyncMemoryCollection"]


def _stored(obj: MemoryObject, *, written: bool = False) -> StoredObject:
    return StoredObject(obj.native_id, obj.etag, obj.calendar, None if written else obj.data, obj)


def _listed(obj: MemoryObject) -> StoredObject | BrokenObject:
    try:
        return _stored(obj)
    except InvalidDataError as exc:
        return BrokenObject(obj.native_id, obj.etag, obj.data, exc)


class AsyncMemoryCollection(AsyncCollection):
    _backend: AsyncMemoryBackend

    def __init__(self, backend: AsyncMemoryBackend, store: MemoryCalendar) -> None:
        self.store = store
        super().__init__(
            backend,
            id=store.id,
            name=store.name,
            color=store.color,
            components=store.components,
            capabilities=backend._collection_capabilities(store.components),
            native=store,
        )

    async def _query(self, searcher: Searcher | None) -> Sequence[StoredObject | BrokenObject]:
        return [_listed(o) for o in self.store.objects.values()]

    async def _fetch_many(self, native_ids: Iterable[str]) -> list[StoredObject | BrokenObject]:
        return [_listed(self.store.objects[n]) for n in native_ids if n in self.store.objects]

    async def _fetch(self, native_id: str) -> StoredObject:
        return _stored(self.store.get(native_id))

    async def _list_etags(self) -> dict[str, str]:
        return {o.native_id: o.etag for o in self.store.objects.values()}

    def _token_scope(self) -> str:
        return f"memory:{self._backend.store.instance}:{self.id}"

    async def _create(self, calendar: icalendar.Calendar, *, native_id: str | None, wire: bytes | None) -> StoredObject:
        data = self._backend.store.drop(wire if wire is not None else calendar.to_ical())
        obj = self.store.create(data, native_id=native_id, assign_uid=self._backend.store.assign_uid)
        return _stored(obj, written=True)

    async def _update(
        self, native_id: str, calendar: icalendar.Calendar, *, etag: str | None, wire: bytes | None
    ) -> StoredObject:
        data = self._backend.store.drop(wire if wire is not None else calendar.to_ical())
        return _stored(self.store.update(native_id, data, etag=etag), written=True)

    async def _delete(self, native_id: str, *, etag: str | None) -> None:
        self.store.delete(native_id, etag=etag)

    async def _move(self, item: Item, target: AsyncCollection) -> StoredObject | None:
        if self.capabilities.level(Feature.MOVE) is not Support.FULL or not isinstance(target, AsyncMemoryCollection):
            return None
        assert item.native_id is not None
        obj = self.store.get(item.native_id)
        if item.etag is not None and obj.etag != item.etag:
            raise ConflictError(f"{obj.native_id!r} changed since it was read")
        moved = target.store.create(obj.data, native_id=None, assign_uid=False)
        del self.store.objects[obj.native_id]
        return _stored(moved, written=True)

    async def _set_name(self, name: str) -> None:
        self.store.name = name

    async def _delete_collection(self) -> None:
        del self._backend.store.calendars[self.id]

    def _wrap_native(self, native_item: object) -> StoredObject:
        if not isinstance(native_item, MemoryObject):
            return super()._wrap_native(native_item)
        return _stored(native_item)


class AsyncMemoryBackend(AsyncBackend):
    """``AsyncMemoryBackend(profile=TRACKER)``, or ``AsyncBackend.connect("memory:")``."""

    kind = "memory"
    collection_class: ClassVar[type[AsyncMemoryCollection]] = AsyncMemoryCollection

    def __init__(
        self,
        profile: Profile = FULL,
        *,
        id: str = "memory",
        silently_drop: Iterable[str] = (),
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> None:
        self.profile = profile
        self.store = MemoryStore(profile, silently_drop=silently_drop)
        super().__init__(
            id=id, capabilities=Capabilities(profile.backend_capabilities), native=self.store, loss=loss, verify=verify
        )

    @classmethod
    async def _connect(cls, url: str, **options: Any) -> AsyncMemoryBackend:
        for unused in ("username", "password", "token", "session"):
            options.pop(unused, None)
        return cls(**options)

    def _collection_capabilities(self, components: frozenset[Component]) -> Capabilities:
        """The profile's table, narrowed to the components this collection holds."""
        table: dict[Feature, Any] = self.profile.collection_capabilities
        for kind, feature in (
            (Component.EVENT, Feature.COMPONENT_EVENT),
            (Component.TASK, Feature.COMPONENT_TASK),
            (Component.JOURNAL, Feature.COMPONENT_JOURNAL),
        ):
            if kind not in components:
                table[feature] = Support.UNSUPPORTED
        return Capabilities(table)

    async def collections(self) -> list[AsyncCollection]:
        return [self.collection_class(self, c) for c in self.store.calendars.values()]

    async def collection(self, name_or_id: str) -> AsyncCollection:
        if name_or_id in self.store.calendars:
            return self.collection_class(self, self.store.calendars[name_or_id])
        return await super().collection(name_or_id)

    async def _create_collection(self, name: str, components: frozenset[Component]) -> AsyncCollection:
        id = name
        while id in self.store.calendars:
            id = f"{name}-{self.store.next()}"
        return self.collection_class(self, self.store.add_calendar(id, name, components))

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id} ({self.profile.name})>"
