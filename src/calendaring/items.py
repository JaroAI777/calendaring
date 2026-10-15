"""The mode-free item classes: plain data over an ``icalendar.Calendar`` (API_DESIGN.md §3).

They do no I/O.  What a collection hands out is the same data bound to it
(``AsyncTask``, ``SyncTask``, ...), defined in ``_async/items.py``.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self

import icalendar
from icalendar import Calendar as _Calendar
from icalendar import Component as _Component

from .errors import InvalidDataError

__all__ = ["PRODID", "Event", "Item", "Journal", "Task", "TaskStatus", "split_stored"]

PRODID = "-//pycal.org//calendaring//EN"

# Written by this library where iCalendar has no property (§3.6).
X_PLANNED_START = "X-PYCAL-PLANNED-START"
X_PLANNED_END = "X-PYCAL-PLANNED-END"


class TaskStatus(StrEnum):
    """The vocabulary backends map ``STATUS`` to and from; values are the iCalendar strings."""

    PENDING = "PENDING"  # tasks draft
    NEEDS_ACTION = "NEEDS-ACTION"
    IN_PROCESS = "IN-PROCESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"  # tasks draft


def _items_of(calendar: icalendar.Calendar) -> list[icalendar.Component]:
    return [c for c in calendar.subcomponents if c.name != "VTIMEZONE"]


def _parse(data: str | bytes) -> icalendar.Calendar:
    try:
        parsed = icalendar.Calendar.from_ical(data)
    except ValueError as exc:
        raise InvalidDataError(f"does not parse as iCalendar: {exc}") from exc
    if isinstance(parsed, list):
        raise InvalidDataError("several calendars in one item")
    if parsed.name != "VCALENDAR":
        return _wrap_component(parsed)
    return parsed


def _wrap_component(component: icalendar.Component) -> icalendar.Calendar:
    calendar = icalendar.Calendar.new(prodid=PRODID, subcomponents=[component])
    calendar.pop("UID", None)  # Calendar.new() gives the VCALENDAR a UID of its own; the item has one already
    calendar.add_missing_timezones()
    return calendar


def split_stored(calendar: icalendar.Calendar) -> list[icalendar.Calendar]:
    """Split a stored object into what items may hold (§3.1, What an item contains).

    A stored object with a master, or with one component, is one item.  One
    with several overrides and no master (RFC 4791 §4.1 allows it) is one
    occurrence item per override.
    """
    components = _items_of(calendar)
    if len(components) <= 1 or any("RECURRENCE-ID" not in c for c in components):
        return [calendar]
    timezones = [c for c in calendar.subcomponents if c.name == "VTIMEZONE"]
    out = []
    for component in components:
        part = icalendar.Calendar()
        for key, value in calendar.items():
            part[key] = value
        for tz in timezones:
            part.add_component(copy.deepcopy(tz))
        part.add_component(copy.deepcopy(component))
        out.append(part)
    return out


class Item:
    """One event, task or journal: one ``VCALENDAR`` with the components of one ``UID``.

    Library users get items from the factories (``Task.new()``,
    ``Task.from_ical()``, ``Task.from_icalendar()``) or from a collection,
    not from this constructor.
    """

    _component_name: ClassVar[str | None] = None
    _component_class: ClassVar[type[_Component] | None] = None

    icalendar: _Calendar
    component: _Component
    native_id: str | None
    etag: str | None
    collection_id: str | None
    native: object | None
    is_occurrence: bool

    def __init__(
        self,
        calendar: _Calendar,
        *,
        native_id: str | None = None,
        etag: str | None = None,
        wire: bytes | None = None,
        collection_id: str | None = None,
        native: object | None = None,
        is_occurrence: bool | None = None,
    ) -> None:
        cls = type(self)._data_class()
        found = Item._class_for(calendar)
        if cls is not Item and found is not cls:
            raise InvalidDataError(f"{found.__name__} data given to {cls.__name__}")
        if cls is Item:
            raise TypeError("Item is abstract; use Task, Event or Journal")
        self.icalendar = calendar
        self.component, occurrence = self._pick(calendar)
        self.is_occurrence = occurrence if is_occurrence is None else is_occurrence
        self.native_id = native_id
        self.etag = etag
        self._wire = None if self.is_occurrence else wire
        self.collection_id = collection_id
        self.native = native

    # --- construction -------------------------------------------------

    @classmethod
    def _data_class(cls) -> type[Item]:
        """The mode-free class: ``Task`` for ``Task``, ``AsyncTask`` and ``SyncTask``."""
        for klass in cls.__mro__:
            if klass in (Task, Event, Journal, Item):
                return klass
        raise TypeError(cls)  # pragma: no cover

    @staticmethod
    def _class_for(calendar: _Calendar) -> type[Item]:
        components = _items_of(calendar)
        if not components:
            raise InvalidDataError("an item holds at least one component")
        names = {str(c.name) for c in components}
        if len(names) > 1:
            raise InvalidDataError(f"an item holds one component type, not {sorted(names)}")
        uids = {str(c.get("UID", "")) for c in components}
        if len(uids) > 1 or "" in uids:
            raise InvalidDataError("an item holds the components of exactly one UID")
        by_name = {"VTODO": Task, "VEVENT": Event, "VJOURNAL": Journal}
        try:
            klass = by_name[names.pop()]
        except KeyError as exc:
            raise InvalidDataError(f"not an item component: {exc}") from None
        # component is typed as the icalendar subclass; a hand-built plain
        # Component with the right name would not have its attributes
        expected = klass._component_class
        assert expected is not None
        if not all(isinstance(c, expected) for c in components):
            raise InvalidDataError(f"components must be icalendar.{expected.__name__}, not a plain Component")
        return klass

    @staticmethod
    def _pick(calendar: _Calendar) -> tuple[_Component, bool]:
        components = _items_of(calendar)
        masters = [c for c in components if "RECURRENCE-ID" not in c]
        if len(masters) == 1:
            return masters[0], False
        if len(masters) > 1:
            raise InvalidDataError("several masters for one UID")
        if len(components) > 1:
            raise InvalidDataError("several occurrences without their master; use split_stored()")
        return components[0], True

    @classmethod
    def new(cls, **properties: Any) -> Self:
        """A new data item; the keyword arguments are those of ``icalendar.Todo.new`` and friends."""
        if cls._component_class is None:
            raise TypeError(f"{cls.__name__}.new(): use Task, Event or Journal")
        component = cls._component_class.new(**properties)
        return cls(_wrap_component(component))

    @classmethod
    def from_ical(cls, data: str | bytes) -> Self:
        """A new data item from iCalendar text: a ``VCALENDAR``, or a bare component."""
        calendar = _parse(data)
        if cls is Item:
            return Item._class_for(calendar)(calendar)  # type: ignore[return-value]
        return cls(calendar)

    @classmethod
    def from_icalendar(cls, calendar: _Calendar) -> Self:
        """A new data item from a parsed calendar.  The item stores a copy."""
        calendar = copy.deepcopy(calendar)
        if cls is Item:
            return Item._class_for(calendar)(calendar)  # type: ignore[return-value]
        return cls(calendar)

    # --- identity and state -------------------------------------------

    @property
    def uid(self) -> str:
        return str(self.component["UID"])

    @property
    def wire(self) -> bytes | None:
        """The stored object exactly as the backend returned it when read; see §3.1, The stored form."""
        return self._wire

    def __copy__(self) -> Self:
        clone = type(self).__new__(type(self))
        clone.__dict__.update(self.__dict__)
        return clone

    def copy(self) -> Self:
        clone = copy.copy(self)
        clone.icalendar = copy.deepcopy(self.icalendar)
        clone.component = self._find_same(clone.icalendar)
        return clone

    def _find_same(self, calendar: _Calendar) -> _Component:
        index = next(i for i, c in enumerate(self.icalendar.subcomponents) if c is self.component)
        return calendar.subcomponents[index]

    def as_data(self) -> Item:
        """A mode-free copy with no collection inside.  ``native`` is dropped, being a live object."""
        cls = type(self)._data_class()
        data = cls.__new__(cls)
        data.__dict__.update({k: v for k, v in self.__dict__.items() if k != "collections"})
        data.icalendar = copy.deepcopy(self.icalendar)
        data.component = self._find_same(data.icalendar)
        data.native = None
        return data

    # --- raw data -----------------------------------------------------

    def to_ical(self) -> bytes:
        """The item as it is now, serialised by ``icalendar``.  Not ``wire``."""
        return self.icalendar.to_ical()  # type: ignore[no-any-return]  # unannotated in icalendar 7.3

    def replace_ical(self, data: str | bytes) -> None:
        """New content, same identity and backend state; the data must describe the same item."""
        calendar = _parse(data)
        if Item._class_for(calendar) is not type(self)._data_class():
            raise ValueError("replace_ical(): a different component type")
        component, occurrence = self._pick(calendar)
        if str(component.get("UID")) != self.uid:
            raise ValueError("replace_ical(): a different UID")
        if occurrence != self.is_occurrence:
            raise ValueError("replace_ical(): a different kind of item")
        if occurrence and component.get("RECURRENCE-ID") != self.component.get("RECURRENCE-ID"):
            raise ValueError("replace_ical(): a different RECURRENCE-ID")
        self.icalendar = calendar
        self.component = component

    # --- comparison ---------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Equality compares the data, never the collections or the stored form."""
        if not isinstance(other, Item):
            return NotImplemented
        return type(self)._data_class() is type(other)._data_class() and self.icalendar == other.icalendar

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.uid!r} {self.component.get('SUMMARY', '')!s}>"


class Event(Item):
    """``component`` is an ``icalendar.Event``."""

    _component_name = "VEVENT"
    _component_class = icalendar.Event
    component: icalendar.Event


class Journal(Item):
    """``component`` is an ``icalendar.Journal``."""

    _component_name = "VJOURNAL"
    _component_class = icalendar.Journal
    component: icalendar.Journal


def _decoded_datetime(component: icalendar.Component, name: str) -> datetime | None:
    if name not in component:
        return None
    prop = component[name]
    # An X- property parses as text; one this process set is already typed.
    if hasattr(prop, "dt"):
        value = prop.dt
    else:
        value = icalendar.vDDDTypes.from_ical(str(prop), prop.params.get("TZID"))  # type: ignore[no-untyped-call]
    return value if isinstance(value, datetime) else None


def _set_datetime(component: icalendar.Component, name: str, value: date | datetime | None) -> None:
    component.pop(name, None)
    if value is not None:
        component.add(name, icalendar.vDDDTypes(value))


class Task(Item):
    """``component`` is an ``icalendar.Todo``.

    ``native_status`` and ``native_priority`` are the backend's own values,
    set by the backend that read the item and not serialised (§3.5).
    """

    _component_name = "VTODO"
    _component_class = icalendar.Todo
    component: icalendar.Todo

    native_status: str | None = None
    native_priority: str | None = None

    @property
    def planned_start(self) -> datetime | None:
        return _decoded_datetime(self.component, X_PLANNED_START)

    @planned_start.setter
    def planned_start(self, value: datetime | None) -> None:
        _set_datetime(self.component, X_PLANNED_START, value)

    @property
    def planned_end(self) -> datetime | None:
        return _decoded_datetime(self.component, X_PLANNED_END)

    @planned_end.setter
    def planned_end(self, value: datetime | None) -> None:
        _set_datetime(self.component, X_PLANNED_END, value)

    def set_duration(self, duration: timedelta, keep: Literal["start", "due"] = "due") -> None:
        """Set ``DUE - DTSTART`` by moving the end not kept, as caldav's ``set_duration(movable_attr=...)``."""
        component = self.component
        start = component.get("DTSTART")
        due = component.get("DUE")
        if keep == "due":
            if due is None:
                raise ValueError("set_duration(keep='due') needs a DUE")
            _set_datetime(component, "DTSTART", component.decoded("DUE") - duration)
        elif keep == "start":
            if start is None:
                raise ValueError("set_duration(keep='start') needs a DTSTART")
            _set_datetime(component, "DUE", component.decoded("DTSTART") + duration)
        else:
            raise ValueError(f"keep must be 'start' or 'due', not {keep!r}")
        component.pop("DURATION", None)
