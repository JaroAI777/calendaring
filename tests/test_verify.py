"""What ``verify=True`` counts as a difference (roadmap 2.1): servers' harmless rewrites are not, a lost alarm is."""

from __future__ import annotations

import icalendar

from calendaring._mapping import differences


def calendar(*event_lines: str, extra: str = "") -> icalendar.Calendar:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//test//EN",
        "BEGIN:VEVENT",
        "UID:verify-1",
        "DTSTAMP:20261019T100000Z",
        "SUMMARY:meeting",
        *event_lines,
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return icalendar.Calendar.from_ical("\r\n".join(lines) + "\r\n" + extra)


ALARM = ("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:wake up", "TRIGGER:-PT15M", "END:VALARM")


def names(sent: icalendar.Calendar, stored: icalendar.Calendar) -> list[str]:
    return [loss.property for loss in differences(sent, stored)]


def test_identical_data_has_no_differences() -> None:
    assert names(calendar("DTSTART:20261020T100000Z", *ALARM), calendar("DTSTART:20261020T100000Z", *ALARM)) == []


def test_a_changed_alarm_is_a_difference() -> None:
    changed = ("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:wake up", "TRIGGER:-PT5M", "END:VALARM")
    assert names(calendar("DTSTART:20261020T100000Z", *ALARM), calendar("DTSTART:20261020T100000Z", *changed)) == [
        "VALARM"
    ]


def test_an_alarm_description_is_not_the_events() -> None:
    sent = calendar("DESCRIPTION:agenda", "DTSTART:20261020T100000Z", *ALARM)
    stored = calendar("DESCRIPTION:agenda", "DTSTART:20261020T100000Z", *ALARM)
    assert names(sent, stored) == []
    assert names(sent, calendar("DTSTART:20261020T100000Z", *ALARM)) == ["DESCRIPTION"]


def test_alarms_compare_in_any_order() -> None:
    other = ("BEGIN:VALARM", "ACTION:AUDIO", "TRIGGER:-PT1H", "END:VALARM")
    sent = calendar("DTSTART:20261020T100000Z", *ALARM, *other)
    stored = calendar("DTSTART:20261020T100000Z", *other, *ALARM)
    assert names(sent, stored) == []


def test_split_categories_are_the_same_categories() -> None:
    assert names(calendar("CATEGORIES:work,home"), calendar("CATEGORIES:work", "CATEGORIES:home")) == []
    assert names(calendar("CATEGORIES:work,home"), calendar("CATEGORIES:home,work")) == []
    assert names(calendar("CATEGORIES:work,home"), calendar("CATEGORIES:work")) == ["CATEGORIES"]


def test_quoted_and_unquoted_parameters_are_the_same() -> None:
    sent = calendar('ATTENDEE;CN="Alice Example":mailto:alice@example.com')
    stored = calendar("ATTENDEE;CN=Alice Example:mailto:alice@example.com")
    assert names(sent, stored) == []


def test_a_time_converted_to_utc_is_the_same_instant() -> None:
    sent = calendar("DTSTART;TZID=Europe/Oslo:20261020T100000", extra="")
    assert names(sent, calendar("DTSTART:20261020T080000Z")) == []
    assert names(sent, calendar("DTSTART:20261020T090000Z")) == ["DTSTART"]


def test_a_floating_time_converted_to_utc_is_a_difference() -> None:
    assert names(calendar("DTSTART:20261020T100000"), calendar("DTSTART:20261020T100000Z")) == ["DTSTART"]


def test_a_recurring_series_moved_to_utc_is_a_difference() -> None:
    """Every occurrence after a DST change would move by an hour."""
    sent = calendar("DTSTART;TZID=Europe/Oslo:20261020T100000", "RRULE:FREQ=WEEKLY;COUNT=30")
    stored = calendar("DTSTART:20261020T080000Z", "RRULE:FREQ=WEEKLY;COUNT=30")
    assert names(sent, stored) == ["DTSTART"]


def test_a_property_a_server_adds_to_an_alarm_is_no_difference() -> None:
    stored_alarm = (*ALARM[:-1], "ACKNOWLEDGED:20261020T090000Z", "X-MOZ-LASTACK:20261020T090000Z", "END:VALARM")
    assert (
        names(calendar("DTSTART:20261020T100000Z", *ALARM), calendar("DTSTART:20261020T100000Z", *stored_alarm)) == []
    )


def test_an_alarm_a_server_adds_is_no_difference() -> None:
    assert names(calendar("DTSTART:20261020T100000Z"), calendar("DTSTART:20261020T100000Z", *ALARM)) == []


def test_alarms_are_matched_so_that_each_finds_its_own() -> None:
    short = ("BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT15M", "END:VALARM")
    sent = calendar("DTSTART:20261020T100000Z", *short, *ALARM)
    # Stored in the other order: the short alarm also matches the full one, which the full one needs.
    stored = calendar("DTSTART:20261020T100000Z", *ALARM, *short)
    assert names(sent, stored) == []


def test_a_loss_names_only_the_alarms_that_were_not_kept() -> None:
    other = ("BEGIN:VALARM", "ACTION:AUDIO", "TRIGGER:-PT1H", "END:VALARM")
    added = ("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:server default", "TRIGGER:-PT5M", "END:VALARM")
    sent = calendar("DTSTART:20261020T100000Z", *ALARM, *other)
    [dropped] = differences(sent, calendar("DTSTART:20261020T100000Z", *other))
    assert dropped.property == "VALARM"
    assert "wake up" in str(dropped.sent)
    assert "AUDIO" not in str(dropped.sent)
    assert dropped.stored is None
    [replaced] = differences(sent, calendar("DTSTART:20261020T100000Z", *other, *added))
    assert "AUDIO" not in str(replaced.sent)
    assert "server default" in str(replaced.stored)
    assert "AUDIO" not in str(replaced.stored)
