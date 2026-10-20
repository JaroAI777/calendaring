"""The time log, mode-free: entries, their iCalendar form, and aggregation (API_DESIGN.md §3.4, roadmap 1.6).

Time spent is an append-only log of entries; the total is derived.  In
iCalendar an entry is a ``VEVENT`` marked ``X-PYCAL-TIME-ENTRY``, pointing
at the task or event the time was spent on with ``RELATED-TO``, and
naming who did the work with RFC 9073 ``PARTICIPANT`` components, never
with ``ATTENDEE`` (which would make a server send invitations).  The I/O
methods are on the collections and bound items; this module does no I/O.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar

from .items import Item, Task, _decoded_datetime

__all__ = [
    "X_TIMER_START",
    "X_TIME_ENTRY",
    "TimeEntry",
    "TimeSummary",
    "entry_from_component",
    "apply_entry",
    "comparable",
    "entry_to_component",
    "is_entry",
    "resolve",
    "running_timer",
    "summarize",
]

X_TIME_ENTRY = "X-PYCAL-TIME-ENTRY"
X_TIMER_START = "X-PYCAL-TIMER-START"

_ZERO = timedelta(0)


@dataclass(frozen=True)
class TimeEntry:
    """Time spent on one task or event, by the ``users`` named (each spent ``duration``)."""

    duration: timedelta
    start: datetime | None = None
    end: datetime | None = None  # None, with start set: the running timer
    users: tuple[str, ...] = ()  # calendar addresses
    comment: str | None = None
    logged: datetime | None = None  # when it was recorded (CREATED)
    id: str | None = None  # the entry's UID, or the backend's id for it

    @property
    def running(self) -> bool:
        return self.start is not None and self.end is None

    @property
    def person_time(self) -> timedelta:
        """The duration once per person named (at least once)."""
        return self.duration * max(1, len(self.users))


def running_timer(item: Item, now: datetime) -> TimeEntry | None:
    """The running timer on a task, as an entry without an end; ``None`` when none runs."""
    if not isinstance(item, Task) or X_TIMER_START not in item.component:
        return None
    start = _decoded_datetime(item.component, X_TIMER_START)
    return None if start is None else TimeEntry(duration=max(now - start, _ZERO), start=start)


def resolve(
    *, spent: timedelta | None, start: datetime | None, end: datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    """``(start, end)`` from two of ``spent``, ``start`` and ``end``; ``spent`` alone ends now."""
    if start is not None and end is not None:
        if (start.tzinfo is None) != (end.tzinfo is None):
            raise ValueError("one time is floating and the other has a time zone; give both the same kind")
        if end < start:
            raise ValueError(f"the entry ends ({end}) before it starts ({start})")
        if spent is not None and spent != end - start:
            raise ValueError(f"spent ({spent}), start and end disagree ({end - start})")
        return start, end
    if spent is None:
        raise ValueError("give two of spent, start and end (or spent alone)")
    if start is not None:
        return start, start + spent
    end = end if end is not None else now
    return end - spent, end


def is_entry(component: icalendar.Component) -> bool:
    return component.name == "VEVENT" and str(component.get(X_TIME_ENTRY, "")).upper() == "TRUE"


def _storable(moment: datetime) -> datetime:
    """A time as it can be written: a zone with no IANA name (CEST from ``astimezone()``) becomes UTC."""
    if moment.tzinfo is None or isinstance(moment.tzinfo, ZoneInfo):
        return moment
    return moment.astimezone(UTC)


def entry_to_component(entry: TimeEntry, *, parent: Item, uid: str | None = None) -> icalendar.Event:
    """The ``VEVENT`` that stores ``entry``, logged against ``parent``."""
    event = icalendar.Event()
    event.add("UID", uid or entry.id or str(uuid.uuid4()))
    event.add("DTSTAMP", datetime.now(UTC))
    event.add("CREATED", entry.logged or datetime.now(UTC))
    if "SUMMARY" in parent.component:
        event.add("SUMMARY", str(parent.component["SUMMARY"]))
    event.add("RELATED-TO", parent.uid, parameters={"RELTYPE": "PARENT"})
    event.add(X_TIME_ENTRY, "TRUE")
    apply_entry(event, entry)
    return event


def apply_entry(event: icalendar.Component, entry: TimeEntry) -> None:
    """Write ``entry``'s times, comment and people into a stored entry, leaving its other properties."""
    if not isinstance(entry.start, datetime) or not isinstance(entry.end, datetime):
        raise TypeError("an entry is stored with a start and an end time (RFC 5545 has no hours on a date)")
    for name in ("DTSTART", "DTEND", "DURATION", "DESCRIPTION"):
        event.pop(name, None)
    event.add("DTSTART", _storable(entry.start))
    event.add("DTEND", _storable(entry.end))
    if entry.comment is not None:
        event.add("DESCRIPTION", entry.comment)
    event.subcomponents[:] = [c for c in event.subcomponents if c.name != "PARTICIPANT"]
    for user in entry.users:
        participant = icalendar.cal.Component()
        participant.name = "PARTICIPANT"  # type: ignore[misc]  # RFC 9073; icalendar 7.3 has no class for it
        participant.add("UID", str(uuid.uuid4()))
        participant.add("PARTICIPANT-TYPE", "ACTIVE")
        participant.add("CALENDAR-ADDRESS", icalendar.vCalAddress(user))
        event.add_component(participant)


def entry_from_component(component: icalendar.Component) -> tuple[str | None, TimeEntry]:
    """``(the UID it was logged against, the entry)`` from a stored entry ``VEVENT``."""
    start = component.decoded("DTSTART", None)
    end = component.decoded("DTEND", None)
    duration = component.decoded("DURATION", None)
    if not isinstance(start, datetime):  # an entry on a date has no time to start at
        start = end = None
    if end is None and start is not None and duration is not None:
        end = start + duration
    related = component.get("RELATED-TO")
    related = related if isinstance(related, list) else [] if related is None else [related]
    parents = [str(r) for r in related if str(r.params.get("RELTYPE", "PARENT")).upper() == "PARENT"]
    users = tuple(
        str(p["CALENDAR-ADDRESS"])
        for p in component.subcomponents
        if p.name == "PARTICIPANT" and "CALENDAR-ADDRESS" in p
    )
    entry = TimeEntry(
        duration=end - start if start is not None and end is not None else duration or _ZERO,
        start=start,
        end=end,
        users=users,
        comment=str(component["DESCRIPTION"]) if "DESCRIPTION" in component else None,
        logged=component.decoded("CREATED", None),
        id=str(component["UID"]) if "UID" in component else None,
    )
    return (parents[0] if parents else None), entry


@dataclass(frozen=True)
class TimeSummary:
    """Committed time (the estimates of open tasks) and spent time, kept apart."""

    estimate: timedelta = field(default=_ZERO)
    spent: timedelta = field(default=_ZERO)

    def __add__(self, other: TimeSummary) -> TimeSummary:
        return TimeSummary(self.estimate + other.estimate, self.spent + other.spent)


def _open(item: Item) -> bool:
    return isinstance(item, Task) and str(item.component.get("STATUS", "NEEDS-ACTION")) not in (
        "COMPLETED",
        "CANCELLED",
    )


def _estimate(item: Item) -> timedelta:
    value = item.component.get("ESTIMATED-DURATION")  # a property icalendar 7.3 does not type
    if value is None:
        return _ZERO
    parsed = value.dt if hasattr(value, "dt") else icalendar.vDuration.from_ical(str(value))  # type: ignore[no-untyped-call]
    return parsed if isinstance(parsed, timedelta) else _ZERO


def comparable(moment: datetime) -> datetime:
    """A time that compares with any other: a floating one read as local time (as the searcher does, §4)."""
    return moment.astimezone() if moment.tzinfo is None else moment


def _spent(entry: TimeEntry, window: tuple[datetime, datetime] | None) -> timedelta:
    """The person-time of an entry, the part inside ``window`` when one is given."""
    if window is None:
        return entry.person_time
    low, high = comparable(window[0]), comparable(window[1])
    if entry.start is None or entry.end is None:
        inside = entry.logged is not None and low <= comparable(entry.logged) < high
        return entry.person_time if inside else _ZERO
    start, end = comparable(entry.start), comparable(entry.end)
    if end <= start:
        return entry.person_time if low <= start < high else _ZERO
    overlap = min(end, high) - max(start, low)
    if overlap <= _ZERO:
        return _ZERO
    return entry.person_time * (overlap / (end - start))


def summarize(
    pairs: Iterable[tuple[Item, Sequence[TimeEntry]]],
    *,
    group_by: Callable[[Item], Hashable],
    window: tuple[datetime, datetime] | None = None,
) -> dict[Hashable, TimeSummary]:
    """Per key of ``group_by``: the estimates of the open tasks, and the time logged (inside ``window``).

    An entry that names several people counts once per person.  One that
    straddles an edge of the window is prorated; one with no start counts
    by when it was logged.
    """
    out: dict[Hashable, TimeSummary] = {}
    for item, entries in pairs:
        estimate = _estimate(item) if _open(item) else _ZERO
        spent = sum((_spent(e, window) for e in entries), _ZERO)
        key = group_by(item)
        out[key] = out.get(key, TimeSummary()) + TimeSummary(estimate, spent)
    return out
