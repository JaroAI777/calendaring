"""Mode-free write planning: what a backend cannot store (§5.3), and what it stored differently (verify).

The generic layer derives losses from the capability table, so a backend
that declares a property ``UNSUPPORTED``, or ``LOSSY`` with ``values`` (and
optionally a ``map`` from a value to the one stored instead), gets the loss
check without code of its own.  A backend with mapping rules beyond that
overrides ``_plan_losses`` on its collection.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

import icalendar

from .capabilities import Capabilities, Feature, Support
from .items import X_PLANNED_END, X_PLANNED_START
from .loss import Loss

__all__ = ["Change", "differences", "plan_calendar_losses", "plan_losses", "property_lines"]

# A loss and the edit that makes the component storable.
Change = tuple[Loss, Callable[[], None]]

ANY_COMPONENT: dict[str, Feature] = {
    "CATEGORIES": Feature.CATEGORIES,
    "ATTENDEE": Feature.ATTENDEES,
    "RRULE": Feature.RECURRENCE,
    "RDATE": Feature.RECURRENCE,
    "EXDATE": Feature.RECURRENCE,
}

# The properties whose values are dates or times, for timezones and dates.
TIME_PROPERTIES = ("DTSTART", "DTEND", "DUE", "RECURRENCE-ID", "RDATE", "EXDATE")

# What a backend without the feature stores instead (§5.1).
CONVERSIONS: dict[Feature, Callable[[Any], Any]] = {
    # A time with a TZID, or a floating one (read as local time, as the searcher does), becomes UTC.
    Feature.TIMEZONES: lambda t: t.astimezone(UTC) if isinstance(t, datetime) and t.tzname() != "UTC" else t,
    # A date becomes midnight UTC.
    Feature.DATES: lambda t: (
        t if isinstance(t, datetime) or not isinstance(t, date) else datetime.combine(t, time(), UTC)
    ),
}

TASK_ONLY: dict[str, Feature] = {
    "STATUS": Feature.TASK_STATUS,
    "PRIORITY": Feature.TASK_PRIORITY,
    "PERCENT-COMPLETE": Feature.TASK_PERCENT_COMPLETE,
    "DTSTART": Feature.TASK_START,
    "DUE": Feature.TASK_DUE,
    "COMPLETED": Feature.TASK_COMPLETED,
    X_PLANNED_START: Feature.TASK_PLANNED,
    X_PLANNED_END: Feature.TASK_PLANNED,
    "ESTIMATED-DURATION": Feature.TASK_ESTIMATE,
}

RELTYPE_FEATURE: dict[str, Feature] = {
    "PARENT": Feature.TASK_RELATIONS_PARENT,
    "CHILD": Feature.TASK_RELATIONS_PARENT,
    "SIBLING": Feature.TASK_RELATIONS_PARENT,
}

# Single date-time properties a server may store in UTC instead of with their TZID.
INSTANTS = frozenset({"DTSTART", "DTEND", "DUE", "COMPLETED", "RECURRENCE-ID", X_PLANNED_START, X_PLANNED_END})

# Properties servers legitimately change, and identity a backend may assign (§5.3).
NOT_VERIFIED = frozenset({"DTSTAMP", "SEQUENCE", "LAST-MODIFIED", "UID"})


def _values(component: icalendar.Component, name: str) -> list[Any]:
    value = component.get(name)
    return value if isinstance(value, list) else [value]


def _key(name: str, value: Any) -> Any:
    return int(value) if name == "PRIORITY" else str(value)


def _drop(component: icalendar.Component, name: str) -> Callable[[], None]:
    return lambda: component.pop(name, None)


def _replace(component: icalendar.Component, name: str, stored: Any) -> Callable[[], None]:
    def apply() -> None:
        component.pop(name, None)
        component.add(name, stored)

    return apply


def _keep_related(component: icalendar.Component, dropped: list[Any]) -> Callable[[], None]:
    def apply() -> None:
        kept = [v for v in _values(component, "RELATED-TO") if v not in dropped]
        component.pop("RELATED-TO", None)
        for value in kept:
            component.add("RELATED-TO", value)

    return apply


def _times(component: icalendar.Component, name: str) -> list[Any]:
    """One property's dates and times; ``RDATE`` and ``EXDATE`` may hold several, on several lines."""
    out: list[Any] = []
    for value in _values(component, name):
        if hasattr(value, "dts"):
            out.extend(d.dt for d in value.dts)
        else:
            out.append(value.dt)
    return out


def _convert(component: icalendar.Component, name: str, new: list[Any]) -> Callable[[], None]:
    def apply() -> None:
        component.pop(name, None)
        component.add(name, new if name in ("RDATE", "EXDATE") else new[0])

    return apply


def _time_losses(component: icalendar.Component, capabilities: Capabilities) -> list[Change]:
    changes: list[Change] = []
    for feature, convert in CONVERSIONS.items():
        if capabilities.level(feature) in (Support.FULL, Support.EMULATED, Support.UNKNOWN):
            continue
        for name in TIME_PROPERTIES:
            if name not in component:
                continue
            sent = _times(component, name)
            stored = [convert(t) for t in sent]
            if any(new is not old for new, old in zip(stored, sent, strict=True)):  # a conversion returns a new value
                changes.append((Loss(name, sent[0], stored[0]), _convert(component, name, stored)))
    return changes


def _alarm_losses(component: icalendar.Component, capabilities: Capabilities) -> list[Change]:
    alarms = [c for c in component.subcomponents if c.name == "VALARM"]
    if not alarms or capabilities.level(Feature.ALARMS) is not Support.UNSUPPORTED:
        return []

    def apply() -> None:
        component.subcomponents[:] = [c for c in component.subcomponents if c.name != "VALARM"]

    return [(Loss("VALARM", alarms[0].to_ical().decode(), None), apply)]


def plan_losses(component: icalendar.Component, capabilities: Capabilities) -> list[Change]:
    """The losses writing ``component`` would cause, each with the edit that applies it."""
    changes: list[Change] = _alarm_losses(component, capabilities)
    is_task = component.name == "VTODO"
    table = ANY_COMPONENT | TASK_ONLY if is_task else ANY_COMPONENT
    for name in list(component):
        feature = table.get(name)
        if feature is None:
            if (
                name.startswith("X-")
                and not name.startswith("X-PYCAL-")
                and capabilities.level(Feature.PROPERTIES_PASSTHROUGH) is Support.UNSUPPORTED
            ):
                changes.append((Loss(name, component[name], None), _drop(component, name)))
            continue
        level = capabilities.level(feature)
        if level is Support.UNSUPPORTED:
            changes.append((Loss(name, component[name], None), _drop(component, name)))
        elif level is Support.LOSSY and "values" in capabilities.details(feature):
            details = capabilities.details(feature)
            sent = _key(name, component[name])
            if sent not in details["values"]:
                mapping = details.get("map", {})
                stored = mapping.get(sent, mapping.get(str(sent)))
                apply = _drop(component, name) if stored is None else _replace(component, name, stored)
                changes.append((Loss(name, sent, stored), apply))
    if is_task and "RELATED-TO" in component:
        by_feature: dict[Feature, list[Any]] = {}
        for value in _values(component, "RELATED-TO"):
            reltype = str(value.params.get("RELTYPE", "PARENT")).upper()
            feature = RELTYPE_FEATURE.get(reltype, Feature.TASK_RELATIONS_DEPENDS_ON)
            if capabilities.level(feature) is Support.UNSUPPORTED:
                by_feature.setdefault(feature, []).append(value)
        dropped = [v for values in by_feature.values() for v in values]
        if dropped:
            sent = [str(v) for v in dropped]
            changes.append(
                (Loss("RELATED-TO", sent if len(sent) > 1 else sent[0], None), _keep_related(component, dropped))
            )
    dropped_names = {loss.property for loss, _ in changes}
    changes.extend(c for c in _time_losses(component, capabilities) if c[0].property not in dropped_names)
    return changes


def _lines(component: icalendar.Component) -> list[bytes]:
    """The component's own content lines, unfolded: not those of its subcomponents."""
    own = copy.copy(component)
    own.subcomponents = []
    return own.to_ical().replace(b"\r\n ", b"").split(b"\r\n")  # type: ignore[no-any-return]


def plan_calendar_losses(calendar: icalendar.Calendar, capabilities: Capabilities) -> list[Change]:
    """The losses across a stored object's components: overrides go with the RRULE they belong to."""
    if capabilities.level(Feature.RECURRENCE) is not Support.UNSUPPORTED:
        return []
    overrides = [c for c in calendar.subcomponents if "RECURRENCE-ID" in c]
    if not overrides or len(overrides) == len([c for c in calendar.subcomponents if c.name != "VTIMEZONE"]):
        return []  # occurrences without a master are items of their own

    def apply() -> None:
        calendar.subcomponents[:] = [c for c in calendar.subcomponents if "RECURRENCE-ID" not in c]

    sent = [c.get("RECURRENCE-ID").to_ical().decode() for c in overrides]
    return [(Loss("RECURRENCE-ID", sent if len(sent) > 1 else sent[0], None), apply)]


def property_lines(component: icalendar.Component, name: str) -> list[bytes]:
    """One property's content lines, unfolded and sorted, for an order-insensitive comparison.

    Only the component's own: a ``VALARM``'s ``DESCRIPTION`` is not its event's.
    """
    out = []
    for line in _lines(component):
        head = line.split(b":", 1)[0].split(b";", 1)[0]
        if head.upper() == name.encode():
            out.append(line)
    return sorted(out)


def _comparable(component: icalendar.Component, name: str) -> list[Any]:
    """What ``verify`` compares for a property, so that a server's harmless rewrites compare equal.

    ``CATEGORIES`` is a set however it is split over lines and commas, and
    an aware date-time on a single event is an instant, whether it is stored
    with its TZID or in UTC.  A floating time is not an instant, and in a
    recurring series a TZID is not either, so converting one is a change.
    """
    if name == "CATEGORIES":
        return sorted({str(c) for value in _values(component, name) for c in getattr(value, "cats", [value])})
    if name in INSTANTS and not isinstance(component.get(name), list) and not _recurs(component):
        value = component.decoded(name)
        if isinstance(value, datetime) and value.tzinfo is not None:
            return [value.astimezone(UTC)]
    return list(property_lines(component, name))


def _recurs(component: icalendar.Component) -> bool:
    """A series' times are local times: moved to UTC, its occurrences shift at a DST change."""
    return "RRULE" in component or "RDATE" in component


def _stored_as_sent(sent: icalendar.Component, stored: icalendar.Component) -> bool:
    """Whether ``stored`` holds everything ``sent`` did, as the top level is compared: what a server adds is fine."""
    if sent.name != stored.name:
        return False
    if any(
        _comparable(sent, name) != _comparable(stored, name) for name in dict.fromkeys(sent) if name not in NOT_VERIFIED
    ):
        return False
    return _matched(sent.subcomponents, stored.subcomponents)


def _matched(sent: list[icalendar.Component], stored: list[icalendar.Component]) -> bool:
    """Each sent subcomponent has a stored one of its own, in any order.

    Tried every way, not first come first served: a sent alarm that matches two stored
    ones must not take the one another sent alarm needs.  A component has few.
    """
    if not sent:
        return True
    first, rest = sent[0], sent[1:]
    return any(
        _stored_as_sent(first, other) and _matched(rest, stored[:i] + stored[i + 1 :]) for i, other in enumerate(stored)
    )


def _subcomponents(component: icalendar.Component, kind: str) -> list[icalendar.Component]:
    return [c for c in component.subcomponents if c.name == kind]


def _ical(components: list[icalendar.Component]) -> str:
    return b"".join(c.to_ical() for c in components).decode()


def _by_occurrence(calendar: icalendar.Calendar) -> dict[str | None, icalendar.Component]:
    out = {}
    for component in calendar.subcomponents:
        if component.name == "VTIMEZONE":
            continue
        rid = component.get("RECURRENCE-ID")
        out[None if rid is None else rid.to_ical().decode()] = component
    return out


def differences(sent: icalendar.Calendar, stored: icalendar.Calendar) -> list[Loss]:
    """Every property that was sent and is stored differently, or not at all."""
    out: list[Loss] = []
    stored_parts = _by_occurrence(stored)
    for rid, component in _by_occurrence(sent).items():
        other = stored_parts.get(rid)
        if other is None:
            out.append(Loss(f"RECURRENCE-ID={rid}" if rid else str(component.name), component.name, None))
            continue
        for name in dict.fromkeys(component):
            if name in NOT_VERIFIED:
                continue
            if _comparable(component, name) != _comparable(other, name):
                mine, theirs = property_lines(component, name), property_lines(other, name)
                out.append(Loss(name, b"\n".join(mine).decode(), b"\n".join(theirs).decode() or None))
        for kind in dict.fromkeys(str(c.name) for c in [*component.subcomponents, *other.subcomponents]):
            mine_subs = _subcomponents(component, kind)
            theirs_subs = _subcomponents(other, kind)
            if not _matched(mine_subs, theirs_subs):
                # Name what was not kept: the sent ones nothing stored matches, and the stored ones that match
                # nothing sent.  Where each matches but they cannot be paired off, that is all of them.
                lost = [m for m in mine_subs if not any(_stored_as_sent(m, t) for t in theirs_subs)]
                instead = [t for t in theirs_subs if not any(_stored_as_sent(m, t) for m in mine_subs)]
                if not lost:
                    lost, instead = mine_subs, theirs_subs
                out.append(Loss(kind, _ical(lost), _ical(instead) or None))
    return out
