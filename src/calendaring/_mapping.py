"""Mode-free write planning: what a backend cannot store (§5.3), and what it stored differently (verify).

The generic layer derives losses from the capability table, so a backend
that declares a property ``UNSUPPORTED``, or ``LOSSY`` with ``values`` (and
optionally a ``map`` from a value to the one stored instead), gets the loss
check without code of its own.  A backend with mapping rules beyond that
overrides ``_plan_losses`` on its collection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import icalendar

from .capabilities import Capabilities, Feature, Support
from .items import X_PLANNED_END, X_PLANNED_START
from .loss import Loss

__all__ = ["Change", "differences", "plan_losses", "property_lines"]

# A loss and the edit that makes the component storable.
Change = tuple[Loss, Callable[[], None]]

ANY_COMPONENT: dict[str, Feature] = {
    "CATEGORIES": Feature.CATEGORIES,
    "ATTENDEE": Feature.ATTENDEES,
    "RRULE": Feature.RECURRENCE,
    "RDATE": Feature.RECURRENCE,
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


def plan_losses(component: icalendar.Component, capabilities: Capabilities) -> list[Change]:
    """The losses writing ``component`` would cause, each with the edit that applies it."""
    changes: list[Change] = []
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
    return changes


def property_lines(component: icalendar.Component, name: str) -> list[bytes]:
    """One property's content lines, unfolded and sorted, for an order-insensitive comparison."""
    out = []
    for line in component.to_ical().replace(b"\r\n ", b"").split(b"\r\n"):
        head = line.split(b":", 1)[0].split(b";", 1)[0]
        if head.upper() == name.encode():
            out.append(line)
    return sorted(out)


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
            mine = property_lines(component, name)
            theirs = property_lines(other, name)
            if mine != theirs:
                out.append(Loss(name, b"\n".join(mine).decode(), b"\n".join(theirs).decode() or None))
    return out
