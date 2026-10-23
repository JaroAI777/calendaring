"""What a user of the library does, step by step, on every kind of backend.

These tests are meant to be read: one story each, in the sync mode, the
way a script would use the library.  The conformance suites are what check
every promise in detail; this file checks that the obvious things work,
on local files, in memory and on real CalDAV servers.

Where a backend cannot do what the story needs, the story checks the other
half of the promise instead: the call is refused before anything is
written, with ``UnsupportedError`` for an operation and ``LossyWriteError``
for a value the backend would have to drop.  The memory backend's
``TRACKER`` and ``MINIMAL`` profiles are there to walk those paths.

Add caldav's docker servers with ``--caldav-servers=Baikal,Nextcloud``
(see ``conftest.py``).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import caldav_server_names, running_caldav_server
from icalendar_searcher import Searcher

from calendaring import (
    Backend,
    Collection,
    Component,
    ConflictError,
    Event,
    Feature,
    LossyWriteError,
    Support,
    SyncItem,
    UnsupportedError,
)
from calendaring.memory import MINIMAL, TRACKER, MemoryBackend

LOCAL = [
    "memory",
    "memory, tracker profile",
    "memory, minimal profile",
    "files, vdir",
    "files, one collection directory",
    "files, one .ics file",
]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "where" in metafunc.fixturenames:
        metafunc.parametrize("where", LOCAL + caldav_server_names(metafunc.config))


def open_backend(where: str, directory: Path, request: pytest.FixtureRequest) -> Backend:
    """An empty backend of the kind named, as a user's configuration would open it."""
    match where:
        case "memory":
            return MemoryBackend()
        case "memory, tracker profile":
            return MemoryBackend(TRACKER)
        case "memory, minimal profile":
            return MemoryBackend(MINIMAL)
        case "files, vdir":
            directory.mkdir()
            return Backend.open(str(directory))
        case "files, one collection directory":
            directory.mkdir()
            (directory / "displayname").write_text("My calendar")
            return Backend.open(str(directory))
        case "files, one .ics file":
            directory.mkdir()
            return Backend.open(str(directory / "calendar.ics"))
    server = running_caldav_server(request, where)
    return Backend.open(server.url, kind="caldav", **server.options)


def at(day: int, hour: int) -> datetime:
    """A time in November 2026, when all these stories take place."""
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def summaries(items: Sequence[SyncItem]) -> list[str]:
    return sorted(str(i.component["SUMMARY"]) for i in items)


def unsupported(calendar: Collection, feature: Feature) -> bool:
    """The calendar says it cannot do this.  ``UNKNOWN`` is not that: the operation is attempted."""
    return calendar.capabilities.level(feature) is Support.UNSUPPORTED


@pytest.fixture
def backend(where: str, tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Backend]:
    with open_backend(where, tmp_path / "mine", request) as backend:
        yield backend


def fresh_calendar(
    backend: Backend,
    name: str,
    components: tuple[Component, ...] = (Component.EVENT, Component.TASK, Component.JOURNAL),
) -> Iterator[Collection]:
    """A new calendar where the backend can make one; else the one it has, as new as the backend."""
    on_a_server = backend.kind == "caldav"
    if backend.capabilities.level(Feature.CREATE_COLLECTION) is Support.UNSUPPORTED:
        if on_a_server:
            pytest.skip("the server cannot make calendars, and the stories must not write into the user's own")
        yield backend.collections()[0]
        return
    if on_a_server and backend.capabilities.level(Feature.DELETE_COLLECTION) is Support.UNSUPPORTED:
        pytest.skip("the server cannot delete calendars, so every story would leave one behind")
    calendar = backend.create_collection(name, components=components)
    # Journals are optional; the journal story checks their refusal.
    missing = set(components) - calendar.components - {Component.JOURNAL}
    if on_a_server and missing:
        calendar.delete_collection()
        pytest.skip(
            f"the server made a calendar without {sorted(c.value for c in missing)} (Bedework makes events-only ones)"
        )
    if not calendar.components:
        # A tracker asked for an events-only calendar makes one holding nothing at all.
        pytest.skip("the backend made a calendar that holds none of what was asked for")
    yield calendar
    if backend.capabilities.level(Feature.DELETE_COLLECTION) is not Support.UNSUPPORTED:
        calendar.delete_collection()


@pytest.fixture
def calendar(backend: Backend, request: pytest.FixtureRequest) -> Iterator[Collection]:
    """A calendar for the test alone, holding nothing yet."""
    yield from fresh_calendar(backend, f"scenario {request.node.originalname}")


@pytest.fixture
def events_calendar(backend: Backend, request: pytest.FixtureRequest) -> Iterator[Collection]:
    """A calendar made for events only, as many users keep one beside their task list."""
    if backend.capabilities.level(Feature.CREATE_COLLECTION) is Support.UNSUPPORTED:
        pytest.skip("the backend has one calendar, and makes no others")
    yield from fresh_calendar(backend, f"scenario {request.node.originalname}", components=(Component.EVENT,))


@pytest.fixture
def other_calendar(
    backend: Backend, where: str, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[Collection]:
    """A second calendar: on the same backend, or on another one of the same kind if it holds only one."""
    if backend.capabilities.level(Feature.CREATE_COLLECTION) is not Support.UNSUPPORTED:
        yield from fresh_calendar(backend, f"scenario {request.node.originalname} (other)")
        return
    with open_backend(where, tmp_path / "other", request) as other:
        yield other.collections()[0]


def test_a_new_calendar_is_listed_and_can_be_renamed(backend: Backend, calendar: Collection) -> None:
    assert calendar.id in [c.id for c in backend.collections()]

    if unsupported(calendar, Feature.COLLECTION_SET_NAME):
        with pytest.raises(UnsupportedError):
            calendar.set_name("Renamed")
        assert backend.collection(calendar.id).name != "Renamed"
        return
    calendar.set_name("Renamed")

    # Ask the backend again, rather than trusting the object we renamed.
    assert backend.collection(calendar.id).name == "Renamed"


def test_an_event_is_added_found_changed_and_deleted(calendar: Collection) -> None:
    if unsupported(calendar, Feature.COMPONENT_EVENT):
        # A task tracker holds no events.
        with pytest.raises(UnsupportedError):
            calendar.add_event(summary="Planning meeting", start=at(2, 9), end=at(2, 10))
        assert calendar.search() == []
        return
    meeting = calendar.add_event(summary="Planning meeting", start=at(2, 9), end=at(2, 10))

    found = calendar.events(start=at(2, 0), end=at(3, 0))
    assert [e.uid for e in found] == [meeting.uid]

    meeting.component.summary = "Planning meeting, moved to room 2"
    meeting.save()
    assert calendar.reload(meeting).component.summary == "Planning meeting, moved to room 2"

    meeting.delete()
    assert calendar.events() == []


def test_a_task_is_completed_and_uncompleted(calendar: Collection) -> None:
    """The plann use case: add a task, tick it off, and change one's mind."""
    task = calendar.add_task(summary="Buy milk")
    assert [t.uid for t in calendar.tasks(include_completed=False)] == [task.uid]

    task.complete()
    # A completed task drops out of the to-do list, but is not gone.
    assert calendar.tasks(include_completed=False) == []
    assert calendar.reload(task).component.status == "COMPLETED"

    task.uncomplete()
    assert [t.uid for t in calendar.tasks(include_completed=False)] == [task.uid]


def test_a_due_date_is_kept_or_the_task_is_refused(calendar: Collection) -> None:
    """A task the backend cannot store with its due date must not be stored without it."""
    # For a task, icalendar's ``end`` is DUE.
    if unsupported(calendar, Feature.TASK_DUE):
        with pytest.raises(LossyWriteError):
            calendar.add_task(summary="Pay the rent", end=date(2026, 11, 1))
        assert calendar.tasks() == []
        return
    task = calendar.add_task(summary="Pay the rent", end=date(2026, 11, 1))
    assert calendar.reload(task).component.end == date(2026, 11, 1)


def test_a_calendar_for_events_refuses_tasks(events_calendar: Collection) -> None:
    """The backend is never asked to store what the calendar says it does not hold."""
    if not unsupported(events_calendar, Feature.COMPONENT_TASK):
        pytest.skip("the backend made a calendar for every component anyway (SOGo does)")
    with pytest.raises(UnsupportedError):
        events_calendar.add_task(summary="Buy milk")
    assert events_calendar.search() == []


def test_a_journal_entry_is_written_where_the_backend_keeps_journals(calendar: Collection) -> None:
    if unsupported(calendar, Feature.COMPONENT_JOURNAL):
        # Several CalDAV servers (SOGo, Zimbra, CCS, OX) have no journals, and nor do trackers.
        with pytest.raises(UnsupportedError):
            calendar.add_journal(summary="Notes from the planning meeting", start=date(2026, 11, 2))
        assert calendar.search() == []
        return
    notes = calendar.add_journal(summary="Notes from the planning meeting", start=date(2026, 11, 2))
    assert [j.uid for j in calendar.journals()] == [notes.uid]


def test_a_recurring_event_is_expanded_into_occurrences(calendar: Collection) -> None:
    if unsupported(calendar, Feature.COMPONENT_EVENT):
        pytest.skip("the backend holds no events; test_an_event_is_added_... checks the refusal")
    standup = Event.new(summary="Stand-up", start=at(2, 9), end=at(2, 10))
    standup.component.add("RRULE", {"FREQ": "DAILY", "COUNT": 5})
    if unsupported(calendar, Feature.RECURRENCE):
        # A backend that cannot store the rule must not silently store a one-off event.
        with pytest.raises(LossyWriteError):
            calendar.add(standup)
        assert calendar.events() == []
        return
    calendar.add(standup)

    # Wednesday to Friday: three of the five mornings.
    mornings = calendar.events(start=at(4, 0), end=at(7, 0), expand=True)
    assert sorted(m.component.start for m in mornings) == [at(4, 9), at(5, 9), at(6, 9)]
    assert all(m.is_occurrence and m.uid == standup.uid for m in mornings)


def test_tasks_are_found_by_category_and_by_text(calendar: Collection) -> None:
    if unsupported(calendar, Feature.CATEGORIES):
        with pytest.raises(LossyWriteError):
            calendar.add_task(summary="Write the report", categories=["work"])
        # Not stored without its category, either.
        assert calendar.tasks() == []
        return
    calendar.add_task(summary="Write the report", categories=["work"])
    calendar.add_task(summary="Book the dentist", categories=["home"])

    by_category = Searcher(todo=True)
    by_category.add_property_filter("CATEGORIES", "work")
    assert summaries(calendar.search(by_category)) == ["Write the report"]

    by_text = Searcher(todo=True)
    by_text.add_property_filter("SUMMARY", "dentist", operator="contains")
    assert summaries(calendar.search(by_text)) == ["Book the dentist"]


def test_changes_tells_a_sync_client_what_happened(calendar: Collection) -> None:
    """What a sync tool does: take a token, come back later, and get only the news."""
    calendar.add_task(summary="Kept")
    edited = calendar.add_task(summary="Edited")
    deleted = calendar.add_task(summary="Deleted")
    if unsupported(calendar, Feature.CHANGES):
        with pytest.raises(UnsupportedError):
            calendar.changes()
        return
    token = calendar.changes().token

    edited.component.summary = "Edited, and then some"
    edited.save()
    deleted.delete()

    # The task left alone is not news.
    news = calendar.changes(token)
    assert [i.uid for i in news.changed] == [edited.uid]
    assert news.deleted == [deleted.native_id]


def test_a_stale_copy_cannot_overwrite_a_newer_one(calendar: Collection) -> None:
    """Two clients edit the same task: the second one to save is told, not silently overwritten."""
    if unsupported(calendar, Feature.WRITE_CONDITIONAL):
        pytest.skip("the backend overwrites by design, so there is no refusal to check")
    calendar.add_task(summary="Shared task")
    [mine] = calendar.tasks()
    [theirs] = calendar.tasks()

    theirs.component.summary = "Their edit"
    theirs.save()

    mine.component.summary = "My edit"
    with pytest.raises(ConflictError):
        mine.save()
    assert calendar.reload(mine).component.summary == "Their edit"


def test_a_task_is_moved_to_another_list(calendar: Collection, other_calendar: Collection) -> None:
    task = calendar.add_task(summary="Renew the passport")
    if unsupported(calendar, Feature.MOVE):
        with pytest.raises(UnsupportedError):
            calendar.move(task, other_calendar)
        assert [t.uid for t in calendar.tasks()] == [task.uid]
        assert other_calendar.tasks() == []
        return
    calendar.move(task, other_calendar)

    assert calendar.tasks() == []
    # By summary, not UID: a tracker gives what it moves a new identity, as moving an issue does.
    assert summaries(other_calendar.tasks()) == ["Renew the passport"]


def test_time_is_logged_against_a_task(calendar: Collection) -> None:
    """Roadmap 1.6: time entries are events in the task's own calendar."""
    task = calendar.add_task(summary="Fix the bike")
    if unsupported(calendar, Feature.TIME_LOG):
        # A tracker keeps no events to log time in.
        with pytest.raises(UnsupportedError):
            task.log_time(spent=timedelta(hours=1), day=date(2026, 11, 2))
        assert summaries(calendar.search()) == ["Fix the bike"]
        return
    task.log_time(start=at(2, 9), end=at(2, 10))
    task.log_time(spent=timedelta(minutes=30), day=date(2026, 11, 3))

    assert task.time_spent() == timedelta(hours=1, minutes=30)
    # The task itself is still the only thing the to-do list shows.
    assert [t.uid for t in calendar.tasks()] == [task.uid]
