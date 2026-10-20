"""The time-tracking model, mode-free: entries, their iCalendar form, and aggregation (API_DESIGN.md §3.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import icalendar
import pytest

from calendaring import Event, Task
from calendaring.timetracking import (
    X_TIME_ENTRY,
    X_TIMER_START,
    TimeEntry,
    TimeSummary,
    entry_from_component,
    entry_to_component,
    is_entry,
    resolve,
    running_timer,
    summarize,
)

H = timedelta(hours=1)


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_two_of_three() -> None:
    now = at(3, 17)
    assert resolve(spent=2 * H, start=at(3, 9), end=None, now=now) == (at(3, 9), at(3, 11))
    assert resolve(spent=2 * H, start=None, end=at(3, 11), now=now) == (at(3, 9), at(3, 11))
    assert resolve(spent=None, start=at(3, 9), end=at(3, 11), now=now) == (at(3, 9), at(3, 11))
    assert resolve(spent=2 * H, start=None, end=None, now=now) == (at(3, 15), now), "spent alone ends now"
    assert resolve(spent=2 * H, start=at(3, 9), end=at(3, 11), now=now) == (at(3, 9), at(3, 11))
    with pytest.raises(ValueError, match="disagree"):
        resolve(spent=3 * H, start=at(3, 9), end=at(3, 11), now=now)
    with pytest.raises(ValueError, match="two of"):
        resolve(spent=None, start=at(3, 9), end=None, now=now)
    with pytest.raises(ValueError, match="before"):
        resolve(spent=None, start=at(3, 11), end=at(3, 9), now=now)


def test_an_entry_round_trips_through_its_vevent() -> None:
    task = Task.new(summary="write the report")
    entry = TimeEntry(
        duration=2 * H,
        start=at(3, 9),
        end=at(3, 11),
        users=("mailto:alice@example.com", "mailto:bob@example.com"),
        comment="first draft\nand figures",
        logged=at(3, 11, 5),
    )
    component = entry_to_component(entry, parent=task)
    assert is_entry(component)
    assert str(component[X_TIME_ENTRY]) == "TRUE"
    assert str(component["SUMMARY"]) == "write the report"
    assert str(component["RELATED-TO"]) == task.uid
    assert component["RELATED-TO"].params["RELTYPE"] == "PARENT"
    assert "ATTENDEE" not in component, "an ATTENDEE would make a server send invitations"
    assert [p.name for p in component.subcomponents] == ["PARTICIPANT", "PARTICIPANT"]
    calendar = icalendar.Calendar()
    calendar.add_component(component)
    parent, back = entry_from_component(Event.from_ical(calendar.to_ical()).component)
    assert parent == task.uid
    assert back == TimeEntry(
        duration=2 * H,
        start=at(3, 9),
        end=at(3, 11),
        users=entry.users,
        comment=entry.comment,
        logged=at(3, 11, 5),
        id=str(component["UID"]),
    )


def test_an_ordinary_sub_event_is_no_entry() -> None:
    meeting = Event.new(summary="planning", start=at(3, 9), end=at(3, 10))
    meeting.component.add("RELATED-TO", "some-task", parameters={"RELTYPE": "PARENT"})
    assert not is_entry(meeting.component)


def test_summarize_keeps_estimate_and_spent_apart() -> None:
    a = Task.new(summary="a")
    a.component.add("ESTIMATED-DURATION", icalendar.vDuration(5 * H))
    b = Task.new(summary="b", status="COMPLETED")
    b.component.add("ESTIMATED-DURATION", icalendar.vDuration(3 * H))
    c = Task.new(summary="c")
    pairs = [
        (a, [TimeEntry(duration=2 * H, start=at(2, 9), end=at(2, 11))]),
        (b, [TimeEntry(duration=H, start=at(3, 9), end=at(3, 10))]),
        (c, [TimeEntry(duration=4 * H, logged=at(3, 18))]),  # no start, and no window: all of it counts
    ]
    customer = {a.uid: "customer-1", b.uid: "customer-1", c.uid: "customer-2"}
    by_customer = summarize(pairs, group_by=lambda item: customer[item.uid])
    assert by_customer == {
        "customer-1": TimeSummary(estimate=5 * H, spent=3 * H),  # b is done: its estimate is no longer committed
        "customer-2": TimeSummary(estimate=timedelta(0), spent=4 * H),
    }
    assert sum(by_customer.values(), TimeSummary()) == TimeSummary(estimate=5 * H, spent=7 * H)


def test_summarize_clips_to_the_window() -> None:
    task = Task.new(summary="x")
    entries = [
        TimeEntry(duration=4 * H, start=at(2, 22), end=at(3, 2)),  # straddles midnight: half inside
        TimeEntry(duration=H, start=at(3, 9), end=at(3, 10)),
        TimeEntry(duration=H, start=at(4, 9), end=at(4, 10)),  # after the window
        TimeEntry(duration=2 * H, logged=at(3, 15)),
    ]
    window = (at(3, 0), at(4, 0))
    [summary] = summarize([(task, entries)], group_by=lambda item: None, window=window).values()
    assert summary.spent == 5 * H


def test_a_day_has_no_hours_in_icalendar() -> None:
    """RFC 5545 §3.8.2.5: a DURATION after a DATE DTSTART is whole days, so a date alone is refused."""
    with pytest.raises(TypeError):
        entry_to_component(TimeEntry(duration=H, start=date(2026, 11, 3)), parent=Task.new(summary="x"))  # type: ignore[arg-type]


def test_a_timer_started_in_the_future_has_run_for_nothing() -> None:
    task = Task.new(summary="x")
    task.component.add(X_TIMER_START, icalendar.vDDDTypes(at(3, 12)))
    timer = running_timer(task, at(3, 11))
    assert timer is not None
    assert timer.duration == timedelta(0)


def test_an_entry_on_a_date_has_no_start() -> None:
    event = icalendar.Event()
    event.add("UID", "e")
    event.add("DTSTART", date(2026, 11, 3))
    event.add(X_TIME_ENTRY, "TRUE")
    assert entry_from_component(event)[1].start is None


def test_mixed_floating_and_zoned_times_are_refused() -> None:
    with pytest.raises(ValueError, match="floating"):
        resolve(spent=None, start=at(3, 9), end=datetime(2026, 11, 3, 10), now=at(3, 17))
