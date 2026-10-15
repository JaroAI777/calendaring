"""Small mode-free types shared by both modes (API_DESIGN.md §2, §3.7, §7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Generic, NewType, TypeVar

if TYPE_CHECKING:
    import icalendar

    from .errors import CalendaringError

__all__ = ["ChangeSet", "Component", "MultiResult", "Scope", "StoredObject", "SyncToken"]

T = TypeVar("T")

SyncToken = NewType("SyncToken", str)


class Component(Enum):
    """What a collection can hold.  The values are the iCalendar component names."""

    EVENT = "VEVENT"
    TASK = "VTODO"
    JOURNAL = "VJOURNAL"


class Scope(Enum):
    """Which part of a recurrence set ``save(occurrence)`` changes (§3.7)."""

    THIS = "this"
    ALL = "all"
    THIS_AND_FUTURE = "this-and-future"


@dataclass(frozen=True)
class ChangeSet(Generic[T]):
    """What ``changes()`` returns: always complete, never truncated (§7)."""

    changed: list[T]  # new or modified since token (or known), bound to the collection
    deleted: list[str]  # native ids removed since token (or from known)
    token: SyncToken  # opaque; persist it and pass it back


@dataclass
class MultiResult(Generic[T]):
    """A fan-out result: what succeeded, and what failed, keyed by backend or collection id (§2.1)."""

    items: list[T] = field(default_factory=list)
    errors: Mapping[str, CalendaringError] = field(default_factory=dict)

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ExceptionGroup(
                f"{len(self.errors)} of the sources failed: {', '.join(self.errors)}", list(self.errors.values())
            )


@dataclass
class StoredObject:
    """What a backend's primitives hand to the generic layer: one stored object.

    This is the backend-author side of the ABCs (roadmap 1.2).  ``calendar``
    is the parsed object; ``wire`` the bytes as read, where the backend keeps
    iCalendar text per object, and ``None`` otherwise or after a write.
    """

    native_id: str
    etag: str
    calendar: icalendar.Calendar
    wire: bytes | None = None
    native: object | None = None
    native_status: str | None = None
    native_priority: str | None = None
