"""The mode-free item classes (API_DESIGN.md §3)."""

import pickle
from datetime import UTC, datetime, timedelta

import icalendar
import pytest

from calendaring import Event, InvalidDataError, Item, Journal, Task, TaskStatus
from calendaring.items import split_stored

UTC_NOON = datetime(2026, 11, 1, 12, tzinfo=UTC)


def calendar(*components: str) -> bytes:
    body = "".join(components)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n".encode()


def todo(uid: str = "t1", extra: str = "") -> str:
    return f"BEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:x\r\n{extra}END:VTODO\r\n"


def test_new_makes_a_whole_calendar() -> None:
    task = Task.new(summary="Write the report", end=UTC_NOON)
    assert task.icalendar.name == "VCALENDAR"
    assert task.component.name == "VTODO"
    assert task.component.DUE == UTC_NOON
    assert task.uid
    assert task.native_id is None
    assert task.etag is None
    assert task.wire is None
    assert not task.is_occurrence


def test_item_itself_has_no_factory() -> None:
    with pytest.raises(TypeError):
        Item.new(summary="x")


def test_from_ical_picks_the_class_on_item_and_checks_it_on_a_subclass() -> None:
    assert type(Item.from_ical(calendar(todo()))) is Task
    with pytest.raises(InvalidDataError):
        Event.from_ical(calendar(todo()))
    assert type(Journal.from_ical("BEGIN:VJOURNAL\r\nUID:j\r\nDTSTAMP:20261001T000000Z\r\nEND:VJOURNAL\r\n")) is Journal


@pytest.mark.parametrize(
    "data",
    [
        calendar(),
        calendar(todo("a"), todo("b")),
        calendar(todo("a"), "BEGIN:VEVENT\r\nUID:a\r\nDTSTAMP:20261001T000000Z\r\nEND:VEVENT\r\n"),
        b"not icalendar at all",
    ],
    ids=["empty", "two-uids", "two-types", "garbage"],
)
def test_what_an_item_never_holds(data: bytes) -> None:
    with pytest.raises(InvalidDataError) as info:
        Task.from_ical(data)
    assert isinstance(info.value, ValueError)


def test_overrides_without_master_are_split() -> None:
    a = todo("r", "RECURRENCE-ID:20261101T120000Z\r\n")
    b = todo("r", "RECURRENCE-ID:20261102T120000Z\r\n")
    with pytest.raises(InvalidDataError, match="split_stored"):
        Task.from_ical(calendar(a, b))
    parts = split_stored(icalendar.Calendar.from_ical(calendar(a, b)))
    items = [Task.from_icalendar(p) for p in parts]
    assert [i.is_occurrence for i in items] == [True, True]


def test_a_recurrence_set_is_one_item_with_the_master_as_component() -> None:
    master = todo("r", "DTSTART:20261101T120000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\n")
    override = todo("r", "RECURRENCE-ID:20261102T120000Z\r\n")
    task = Task.from_ical(calendar(master, override))
    assert "RRULE" in task.component
    assert not task.is_occurrence


def test_from_icalendar_stores_a_copy() -> None:
    cal = icalendar.Calendar.from_ical(calendar(todo()))
    task = Task.from_icalendar(cal)
    cal.subcomponents[0]["SUMMARY"] = "changed"
    assert str(task.component["SUMMARY"]) == "x"


def test_replace_ical_keeps_identity_and_checks_it() -> None:
    task = Task.from_ical(calendar(todo()))
    task.native_id, task.etag = "t1.ics", '"1"'
    task.replace_ical(calendar(todo(extra="DESCRIPTION:new\r\n")))
    assert str(task.component["DESCRIPTION"]) == "new"
    assert (task.native_id, task.etag) == ("t1.ics", '"1"')
    with pytest.raises(ValueError, match="UID"):
        task.replace_ical(calendar(todo("other")))
    with pytest.raises(ValueError, match="component type"):
        task.replace_ical(calendar("BEGIN:VEVENT\r\nUID:t1\r\nDTSTAMP:20261001T000000Z\r\nEND:VEVENT\r\n"))


def test_equality_is_the_data() -> None:
    task = Task.from_ical(calendar(todo()))
    other = task.copy()
    other.etag = "different state"
    assert task == other
    other.component["SUMMARY"] = "y"
    assert task != other
    assert Task.from_ical(calendar(todo())) != Event.new(summary="x")
    with pytest.raises(TypeError):
        hash(task)


def test_copy_is_deep() -> None:
    task = Task.new(summary="a")
    clone = task.copy()
    clone.component["SUMMARY"] = "b"
    assert str(task.component["SUMMARY"]) == "a"


def test_data_items_pickle() -> None:
    task = Task.new(summary="a")
    assert pickle.loads(pickle.dumps(task)) == task


def test_set_duration_moves_the_end_not_kept() -> None:
    task = Task.new(summary="a", start=UTC_NOON, end=UTC_NOON + timedelta(hours=1))
    task.set_duration(timedelta(hours=3))
    assert task.component.DTSTART == UTC_NOON - timedelta(hours=2)
    task.set_duration(timedelta(hours=1), keep="start")
    assert task.component.DUE == UTC_NOON - timedelta(hours=1)
    with pytest.raises(ValueError, match="DUE"):
        Task.new(summary="no due").set_duration(timedelta(hours=1))


def test_planned_start_and_end_are_x_pycal_properties() -> None:
    task = Task.new(summary="a")
    task.planned_start = UTC_NOON
    task.planned_end = UTC_NOON + timedelta(days=1)
    assert b"X-PYCAL-PLANNED-START:20261101T120000Z" in task.to_ical()
    assert task.planned_end == UTC_NOON + timedelta(days=1)
    assert Task.from_ical(task.to_ical()).planned_start == UTC_NOON
    task.planned_start = None
    assert "X-PYCAL-PLANNED-START" not in task.component


def test_task_status_values_are_the_icalendar_strings() -> None:
    assert TaskStatus.NEEDS_ACTION == "NEEDS-ACTION"
    assert {s.value for s in TaskStatus} >= {"PENDING", "FAILED"}


def test_new_has_one_uid_and_its_timezones() -> None:
    from zoneinfo import ZoneInfo

    task = Task.new(summary="a", end=datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("Europe/Oslo")))
    assert "UID" not in task.icalendar, "the VCALENDAR must not carry a UID of its own"
    assert [c.name for c in task.icalendar.subcomponents] == ["VTIMEZONE", "VTODO"]
