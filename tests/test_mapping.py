"""Write planning: the losses derived from a declaration, and what verify compares."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import icalendar

from calendaring import Capabilities, Event, Feature, Support
from calendaring._mapping import differences, plan_calendar_losses, plan_losses

OSLO = ZoneInfo("Europe/Oslo")


def with_alarm(event: Event) -> Event:
    alarm = icalendar.Alarm()
    alarm.add("ACTION", "DISPLAY")
    alarm.add("DESCRIPTION", "soon")
    alarm.add("TRIGGER", timedelta(minutes=-15))
    event.component.add_component(alarm)
    return event


def planned(event: Event, table: dict[Feature, Support]) -> tuple[list[str], icalendar.Component]:
    component = copy.deepcopy(event.component)
    changes = plan_losses(component, Capabilities({**dict.fromkeys(Feature, Support.FULL), **table}))
    for _, apply in changes:
        apply()
    return [loss.property for loss, _ in changes], component


def test_a_dropped_alarm_is_a_difference() -> None:
    sent = with_alarm(Event.new(summary="x", start=datetime(2026, 11, 3, 9, tzinfo=UTC), description="d"))
    stored = copy.deepcopy(sent.icalendar)
    for component in stored.walk("VEVENT"):
        component.subcomponents[:] = []
    assert [d.property for d in differences(sent.icalendar, stored)] == ["VALARM"]
    assert differences(sent.icalendar, copy.deepcopy(sent.icalendar)) == []


def test_alarms_unsupported_drops_them() -> None:
    event = with_alarm(Event.new(summary="x", start=datetime(2026, 11, 3, 9, tzinfo=UTC)))
    losses, component = planned(event, {Feature.ALARMS: Support.UNSUPPORTED})
    assert losses == ["VALARM"]
    assert component.subcomponents == []


def test_timezones_lossy_stores_utc() -> None:
    event = Event.new(
        summary="x", start=datetime(2026, 11, 3, 9, tzinfo=OSLO), end=datetime(2026, 11, 3, 10, tzinfo=OSLO)
    )
    floating = Event.new(summary="y", start=datetime(2026, 11, 3, 9))
    losses, component = planned(event, {Feature.TIMEZONES: Support.LOSSY})
    assert sorted(losses) == ["DTEND", "DTSTART"]
    assert component.decoded("DTSTART") == datetime(2026, 11, 3, 8, tzinfo=UTC)
    losses, component = planned(floating, {Feature.TIMEZONES: Support.LOSSY})
    assert losses == ["DTSTART"]
    assert component.decoded("DTSTART").tzinfo == UTC
    assert planned(event, {Feature.TIMEZONES: Support.FULL})[0] == []


def test_dates_lossy_stores_midnight_utc() -> None:
    event = Event.new(summary="x", start=date(2026, 11, 3), end=date(2026, 11, 4))
    event.component.add("EXDATE", [date(2026, 11, 10)])
    losses, component = planned(event, {Feature.DATES: Support.LOSSY, Feature.RECURRENCE: Support.FULL})
    assert sorted(losses) == ["DTEND", "DTSTART", "EXDATE"]
    assert component.decoded("DTSTART") == datetime(2026, 11, 3, tzinfo=UTC)
    assert b"EXDATE:20261110T000000Z" in component.to_ical()


def test_recurrence_unsupported_drops_exdate() -> None:
    event = Event.new(summary="x", start=datetime(2026, 11, 3, 9, tzinfo=UTC))
    event.component.add("RRULE", {"FREQ": "WEEKLY"})
    event.component.add("EXDATE", [datetime(2026, 11, 10, 9, tzinfo=UTC)])
    assert sorted(planned(event, {Feature.RECURRENCE: Support.UNSUPPORTED})[0]) == ["EXDATE", "RRULE"]


def test_recurrence_unsupported_drops_the_overrides_too() -> None:
    series = Event.new(summary="x", start=datetime(2026, 11, 3, 9, tzinfo=UTC))
    series.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 3})
    moved = Event.new(summary="moved", start=datetime(2026, 11, 4, 15, tzinfo=UTC), uid=series.uid)
    moved.component.add("RECURRENCE-ID", datetime(2026, 11, 4, 9, tzinfo=UTC))
    series.icalendar.add_component(moved.component)
    calendar = copy.deepcopy(series.icalendar)
    table = Capabilities({**dict.fromkeys(Feature, Support.FULL), Feature.RECURRENCE: Support.UNSUPPORTED})
    changes = plan_calendar_losses(calendar, table)
    assert [loss.property for loss, _ in changes] == ["RECURRENCE-ID"]
    for _, apply in changes:
        apply()
    assert [str(c["SUMMARY"]) for c in calendar.walk("VEVENT")] == ["x"]


def test_an_alarm_the_server_annotated_is_no_difference() -> None:
    sent = with_alarm(Event.new(summary="x", start=datetime(2026, 11, 3, 9, tzinfo=UTC)))
    stored = copy.deepcopy(sent.icalendar)
    [alarm] = stored.walk("VALARM")
    alarm.add("UID", "server-alarm-1")
    alarm.add("X-WR-ALARMUID", "server-alarm-1")
    assert differences(sent.icalendar, stored) == []
    alarm["TRIGGER"] = icalendar.vDDDTypes(timedelta(minutes=-5))
    assert [d.property for d in differences(sent.icalendar, stored)] == ["VALARM"]
