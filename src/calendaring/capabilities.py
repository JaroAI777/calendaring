"""What a collection or backend can do, and how well (API_DESIGN.md §5)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any

__all__ = ["BACKEND_FEATURES", "Capabilities", "Capability", "Feature", "Support"]


class Support(Enum):
    FULL = "full"  # works, no information lost
    LOSSY = "lossy"  # works, but a value is mapped or truncated
    EMULATED = "emulated"  # done client-side; same result, more I/O or not atomic
    UNSUPPORTED = "unsupported"  # raises UnsupportedError
    UNKNOWN = "unknown"  # not probed; the operation is attempted


class Feature(StrEnum):
    """The features a declaration must cover.  Below 1.0 the list grows as backends need it (Q7)."""

    WRITE = "write"
    COMPONENT_EVENT = "component.event"
    COMPONENT_TASK = "component.task"
    COMPONENT_JOURNAL = "component.journal"
    CREATE_COLLECTION = "create-collection"
    DELETE_COLLECTION = "delete-collection"
    COLLECTION_SET_NAME = "collection.set-name"
    SEARCH_SERVER_SIDE = "search.server-side"
    SEARCH_TIME_RANGE = "search.time-range"
    SEARCH_TEXT = "search.text"
    CHANGES = "changes"
    CHANGES_KNOWN = "changes.known"
    WRITE_WIRE = "write.wire"
    WRITE_CONDITIONAL = "write.conditional"
    IDENTITY_CLIENT_UID = "identity.client-uid"
    IDENTITY_FOREIGN_ID = "identity.foreign-id"
    IDENTITY_CLIENT_NATIVE_ID = "identity.client-native-id"
    PROPERTIES_PASSTHROUGH = "properties.passthrough"
    RECURRENCE = "recurrence"
    RECURRENCE_EDIT_OCCURRENCE = "recurrence.edit-occurrence"
    RECURRENCE_EDIT_THIS_AND_FUTURE = "recurrence.edit-this-and-future"
    MOVE = "move"
    TASK_STATUS = "task.status"
    TASK_PRIORITY = "task.priority"
    TASK_PERCENT_COMPLETE = "task.percent-complete"
    TASK_START = "task.start"
    TASK_DUE = "task.due"
    TASK_COMPLETED = "task.completed"
    TASK_COMPLETE_RECURRING = "task.complete-recurring"
    TASK_PLANNED = "task.planned"
    TASK_ESTIMATE = "task.estimate"
    TASK_RELATIONS_PARENT = "task.relations.parent"
    TASK_RELATIONS_DEPENDS_ON = "task.relations.depends-on"
    CATEGORIES = "categories"
    ATTENDEES = "attendees"
    ALARMS = "alarms"
    TIMEZONES = "timezones"
    DATES = "dates"
    TIME_LOG = "time-log"


# Declared on the backend rather than on a collection (§5.2).
BACKEND_FEATURES = frozenset({Feature.CREATE_COLLECTION, Feature.DELETE_COLLECTION})


@dataclass(frozen=True)
class Capability:
    level: Support
    details: Mapping[str, Any] = field(default_factory=dict)  # how it is supported; keys per feature
    note: str | None = None  # free text for the capability matrix

    @classmethod
    def coerce(cls, value: Capability | Support | bool | str | Mapping[str, Any]) -> Capability:
        """Normalise the shapes a configuration file may use: a boolean, a level string, or a dict."""
        if isinstance(value, Capability):
            return value
        if isinstance(value, Support):
            return cls(value)
        if isinstance(value, bool):
            return cls(Support.FULL if value else Support.UNSUPPORTED)
        if isinstance(value, str):
            return cls(Support(value))
        if isinstance(value, Mapping):
            rest = dict(value)
            level = rest.pop("level")
            note = rest.pop("note", None)
            return cls(Support(level) if not isinstance(level, Support) else level, rest, note)
        raise TypeError(f"not a capability: {value!r}")


class Capabilities(Mapping[Feature, Capability]):
    """A read-only table of ``Feature`` -> ``Capability``."""

    def __init__(self, table: Mapping[Any, Any] = MappingProxyType({})) -> None:
        self._table: dict[Feature, Capability] = {Feature(k): Capability.coerce(v) for k, v in table.items()}

    def __getitem__(self, feature: Feature) -> Capability:
        return self._table[feature]

    def __iter__(self) -> Iterator[Feature]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)

    def __repr__(self) -> str:
        inner = ", ".join(f"{f.value}={c.level.value}" for f, c in self._table.items())
        return f"Capabilities({inner})"

    def supports(self, feature: Feature, *, allow_lossy: bool = False) -> bool:
        """The boolean view.  ``UNKNOWN`` counts as not supported: nothing promises it works."""
        level = self.level(feature)
        return level in (Support.FULL, Support.EMULATED) or (allow_lossy and level is Support.LOSSY)

    def level(self, feature: Feature) -> Support:
        return self._table[feature].level

    def details(self, feature: Feature) -> Mapping[str, Any]:
        return self._table[feature].details

    def updated(self, overrides: Mapping[Any, Any]) -> Capabilities:
        """A copy with some entries replaced."""
        return Capabilities({**self._table, **{Feature(k): v for k, v in overrides.items()}})
