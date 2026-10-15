"""The mode-free storage behind the in-memory reference backend, and its capability profiles.

Both modes' ``MemoryBackend`` classes wrap this; it does no I/O and needs
no ``await``.  It stores bytes per object, as a vdir does, so it can keep
the stored form (``wire``) and take writes of given bytes.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import icalendar

from .capabilities import BACKEND_FEATURES, Feature, Support
from .errors import AlreadyExistsError, ConflictError, NotFoundError
from .items import _items_of, _parse
from .types import Component

__all__ = ["FEED", "FULL", "MINIMAL", "TRACKER", "MemoryCalendar", "MemoryObject", "MemoryStore", "Profile"]


@dataclass(frozen=True)
class Profile:
    """A capability declaration for the reference backend; its behaviour follows from it."""

    name: str
    capabilities: Mapping[Feature, Any]
    components: frozenset[Component]

    @property
    def backend_capabilities(self) -> dict[Feature, Any]:
        return {f: v for f, v in self.capabilities.items() if f in BACKEND_FEATURES}

    @property
    def collection_capabilities(self) -> dict[Feature, Any]:
        return {f: v for f, v in self.capabilities.items() if f not in BACKEND_FEATURES}

    def level(self, feature: Feature) -> Support:
        value = self.capabilities[feature]
        return value if isinstance(value, Support) else Support(value["level"])


F, L, E, U = Support.FULL, Support.LOSSY, Support.EMULATED, Support.UNSUPPORTED

_PENDING = "pending recurring-ical-events issue 292 (API_DESIGN.md Q3)"

FULL = Profile(
    "full",
    {
        **dict.fromkeys(Feature, F),
        Feature.SEARCH_SERVER_SIDE: E,
        Feature.SEARCH_TIME_RANGE: E,
        Feature.SEARCH_TEXT: E,
        Feature.CHANGES: {"level": E, "method": "etag-listing"},
        Feature.IDENTITY_FOREIGN_ID: {"level": U, "note": "the UID is native"},
        Feature.RECURRENCE_EDIT_OCCURRENCE: {"level": U, "note": _PENDING},
        Feature.RECURRENCE_EDIT_THIS_AND_FUTURE: U,
        Feature.TASK_COMPLETE_RECURRING: {"level": U, "note": _PENDING},
    },
    frozenset(Component),
)

# Shaped like an issue tracker: tasks only, two states, three priorities,
# identity assigned by the backend, no unknown properties kept.
TRACKER = Profile(
    "tracker",
    {
        **FULL.capabilities,
        Feature.COMPONENT_EVENT: U,
        Feature.COMPONENT_JOURNAL: U,
        Feature.DELETE_COLLECTION: U,
        Feature.COLLECTION_SET_NAME: U,
        Feature.CHANGES_KNOWN: E,
        Feature.WRITE_WIRE: U,
        Feature.IDENTITY_CLIENT_UID: U,
        Feature.IDENTITY_CLIENT_NATIVE_ID: U,
        Feature.PROPERTIES_PASSTHROUGH: U,
        Feature.RECURRENCE: U,
        Feature.MOVE: E,
        Feature.TASK_STATUS: {
            "level": L,
            "values": ["NEEDS-ACTION", "COMPLETED"],
            "map": {"PENDING": "NEEDS-ACTION", "IN-PROCESS": "NEEDS-ACTION", "CANCELLED": "COMPLETED"},
        },
        Feature.TASK_PRIORITY: {
            "level": L,
            "values": [1, 5, 9],
            "map": {2: 1, 3: 1, 4: 5, 6: 5, 7: 9, 8: 9},
        },
        Feature.TASK_PERCENT_COMPLETE: U,
        Feature.TASK_START: U,
        Feature.TASK_PLANNED: U,
        Feature.TASK_ESTIMATE: U,
        Feature.TASK_RELATIONS_DEPENDS_ON: U,
    },
    frozenset({Component.TASK}),
)

# Writable, but without most of what can be left out: the unsupported
# branches no other profile reaches.  It cannot make collections, so the
# suite borrows the default one.
MINIMAL = Profile(
    "minimal",
    {
        **FULL.capabilities,
        Feature.COMPONENT_JOURNAL: U,
        Feature.CREATE_COLLECTION: U,
        Feature.DELETE_COLLECTION: U,
        Feature.CHANGES: U,
        Feature.CHANGES_KNOWN: U,
        Feature.MOVE: U,
        Feature.RECURRENCE: U,
        Feature.CATEGORIES: U,
        Feature.ATTENDEES: U,
        Feature.TASK_DUE: U,
        Feature.TASK_COMPLETED: U,
        Feature.TASK_RELATIONS_PARENT: U,
        Feature.TASK_RELATIONS_DEPENDS_ON: U,
    },
    frozenset({Component.EVENT, Component.TASK}),
)

# A read-only feed: everything can be read, nothing written.
FEED = Profile(
    "feed",
    {
        **FULL.capabilities,
        Feature.WRITE: U,
        Feature.CREATE_COLLECTION: U,
        Feature.DELETE_COLLECTION: U,
        Feature.COLLECTION_SET_NAME: U,
        Feature.CHANGES_KNOWN: E,
        Feature.WRITE_WIRE: U,
        Feature.WRITE_CONDITIONAL: U,
        Feature.IDENTITY_CLIENT_UID: U,
        Feature.IDENTITY_CLIENT_NATIVE_ID: U,
        Feature.MOVE: U,
    },
    frozenset(Component),
)


@dataclass
class MemoryObject:
    """One stored object; this is the item's ``native``."""

    native_id: str
    data: bytes
    etag: str

    @property
    def calendar(self) -> icalendar.Calendar:
        return _parse(self.data)

    @property
    def uid(self) -> str:
        return str(_items_of(self.calendar)[0]["UID"])


@dataclass
class MemoryCalendar:
    """One collection's objects.  Etags come from the store's counter."""

    store: MemoryStore
    id: str
    name: str | None
    components: frozenset[Component]
    color: str | None = None
    objects: dict[str, MemoryObject] = field(default_factory=dict)

    def put(self, data: bytes, native_id: str | None = None) -> str:
        """Store bytes with no checks at all, as a seeding test or a feed's publisher would."""
        native_id = native_id or f"{self.store.next()}.ics"
        self.objects[native_id] = MemoryObject(native_id, data, self.store.etag())
        return native_id

    def get(self, native_id: str) -> MemoryObject:
        try:
            return self.objects[native_id]
        except KeyError:
            raise NotFoundError(f"no object {native_id!r}") from None

    def create(self, data: bytes, *, native_id: str | None, assign_uid: bool) -> MemoryObject:
        if native_id is not None and native_id in self.objects:
            raise AlreadyExistsError(f"{native_id!r} is taken", taken="native_id")
        if assign_uid:
            data = self._with_uid(data, f"{self.store.next()}@memory.invalid")
        uid = str(_items_of(_parse(data))[0]["UID"])
        if any(o.uid == uid for o in self.objects.values()):
            raise AlreadyExistsError(f"UID {uid!r} is taken", taken="uid")
        return self.get(self.put(data, native_id))

    def update(self, native_id: str, data: bytes, *, etag: str | None) -> MemoryObject:
        current = self.get(native_id)
        if etag is not None and etag != current.etag:
            raise ConflictError(f"{native_id!r} changed since it was read")
        if self.store.assign_uid:
            data = self._with_uid(data, current.uid)
        return self.get(self.put(data, native_id))

    def delete(self, native_id: str, *, etag: str | None) -> None:
        current = self.get(native_id)
        if etag is not None and etag != current.etag:
            raise ConflictError(f"{native_id!r} changed since it was read")
        del self.objects[native_id]

    @staticmethod
    def _with_uid(data: bytes, uid: str) -> bytes:
        calendar = _parse(data)
        for component in _items_of(calendar):
            component.pop("UID", None)
            component.add("UID", uid)
        return calendar.to_ical()  # type: ignore[no-any-return]  # unannotated in icalendar 7.3


class MemoryStore:
    """What one memory backend holds: collections by id, and a counter for etags and names."""

    def __init__(self, profile: Profile, *, silently_drop: Iterable[str] = ()) -> None:
        self.profile = profile
        self.calendars: dict[str, MemoryCalendar] = {}
        self._counter = itertools.count(1)
        # Properties to drop on every write without saying so, like a server
        # that declares UNKNOWN and loses them: for testing verify=True.
        self.silently_drop = frozenset(silently_drop)
        self.add_calendar("default", "Default", profile.components)

    @property
    def assign_uid(self) -> bool:
        return self.profile.level(Feature.IDENTITY_CLIENT_UID) is Support.UNSUPPORTED

    def next(self) -> int:
        return next(self._counter)

    def etag(self) -> str:
        return f'"{self.next()}"'

    def add_calendar(self, id: str, name: str | None, components: Iterable[Component]) -> MemoryCalendar:
        if id in self.calendars:
            raise AlreadyExistsError(f"collection {id!r} exists", taken="native_id")
        calendar = MemoryCalendar(self, id, name, frozenset(components) & self.profile.components)
        self.calendars[id] = calendar
        return calendar

    def drop(self, data: bytes) -> bytes:
        if not self.silently_drop:
            return data
        calendar = _parse(data)
        for component in _items_of(calendar):
            for name in self.silently_drop:
                component.pop(name, None)
        return calendar.to_ical()  # type: ignore[no-any-return]  # unannotated in icalendar 7.3
