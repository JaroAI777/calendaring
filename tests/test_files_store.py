"""The files backend's storage, below both modes (roadmap 2.2): what the conformance suite cannot set up."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from calendaring._files_store import IcsFile, Vdir, file_name, layout, make_collection
from calendaring.errors import AlreadyExistsError, ConfigurationError, ConflictError, NotFoundError
from calendaring.types import Component


def vtodo(uid: str, summary: str = "x", extra: str = "") -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VTODO\r\n"
        f"UID:{uid}\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:{summary}\r\n{extra}END:VTODO\r\nEND:VCALENDAR\r\n"
    ).encode()


OSLO = (
    "BEGIN:VTIMEZONE\r\nTZID:Europe/Oslo\r\nBEGIN:STANDARD\r\nDTSTART:19701025T030000\r\n"
    "TZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
)


# === layout and names ===============================================


def test_the_layout_follows_the_path(tmp_path: Path) -> None:
    assert layout(tmp_path / "calendar.ics") == "file"  # need not exist yet
    assert layout(tmp_path / "UPPER.ICS") == "file"
    root = tmp_path / "calendars"
    root.mkdir()
    assert layout(root) == "root"  # empty: a root, where collections can be made
    (root / "work").mkdir()
    (root / "stray.ics").write_bytes(vtodo("stray"))
    assert layout(root) == "root", "subdirectories make a root, whatever lies beside them"
    (root / "work" / "a.ics").write_bytes(vtodo("a"))
    assert layout(root / "work") == "collection"
    named = tmp_path / "named"
    named.mkdir()
    (named / "displayname").write_text("Named\n")
    assert layout(named) == "collection"


def test_a_path_that_cannot_be_a_calendar_is_a_configuration_error(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not a calendar")
    for path in (tmp_path / "missing", tmp_path / "missing" / "calendar.ics", tmp_path / "notes.txt"):
        with pytest.raises(ConfigurationError):
            layout(path)


def test_a_safe_uid_is_the_file_name_and_any_other_is_not() -> None:
    assert file_name("abc-123@example.com") == "abc-123@example.com.ics"
    for unsafe in ("../../etc/passwd", "a/b", "with space", "æøå", ".hidden", "x" * 200, ""):
        name = file_name(unsafe)
        assert name.endswith(".ics")
        assert "/" not in name
        assert not name.startswith(".")
        assert len(name) < 100
    assert file_name("a/b") != file_name("a/b"), "an unsafe UID gets a fresh random name"


# === vdir ===========================================================


def test_a_native_id_is_a_plain_ics_file_name(tmp_path: Path) -> None:
    vdir = Vdir(tmp_path)
    vdir.set_name("Work")
    for name in ("bar", "displayname", "../x.ics", "sub/x.ics", ".hidden.ics", "C:x.ics", "a\\b.ics"):
        with pytest.raises(ValueError, match="file name"):
            vdir.create(vtodo(f"uid-{len(name)}"), native_id=name)
        with pytest.raises(NotFoundError):
            vdir.read(name)
        with pytest.raises(NotFoundError):
            vdir.update(name, vtodo("x"), etag=None)
        with pytest.raises(NotFoundError):
            vdir.delete(name, etag=None)
    assert vdir.name == "Work"
    assert Vdir(tmp_path).create(vtodo("upper"), native_id="UPPER.ICS").native_id == "UPPER.ICS"
    assert sorted(vdir.etags()) == ["UPPER.ICS"]


def test_concurrent_creates_of_one_uid_store_it_once(tmp_path: Path) -> None:
    outcomes: list[str] = []

    def add() -> None:
        try:
            Vdir(tmp_path).create(vtodo("same"), native_id=None)
            outcomes.append("stored")
        except AlreadyExistsError as exc:
            outcomes.append(str(exc.taken))

    threads = [threading.Thread(target=add) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["stored"] + ["uid"] * 9
    assert sorted(Vdir(tmp_path).etags()) == ["same.ics"]


def test_a_vdir_works_where_hard_links_do_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_links(*args: object) -> None:
        raise PermissionError("operation not permitted")  # FAT, exFAT, many SMB and FUSE mounts

    monkeypatch.setattr(os, "link", no_links)
    vdir = Vdir(tmp_path)
    stored = vdir.create(vtodo("a"), native_id="a.ics")
    assert vdir.read(stored.native_id).data == vtodo("a")
    with pytest.raises(AlreadyExistsError):
        vdir.create(vtodo("b"), native_id="a.ics")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_writes_keep_file_modes_and_symlinks(tmp_path: Path) -> None:
    old = os.umask(0o022)
    try:
        stored = Vdir(tmp_path).create(vtodo("a"), native_id=None)
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / stored.native_id).stat().st_mode) == 0o644, "a new file follows the umask"
    real = tmp_path / "dotfiles" / "calendar.ics"
    real.parent.mkdir()
    IcsFile(real).create(vtodo("x"), native_id=None)
    real.chmod(0o640)
    link = tmp_path / "calendar.ics"
    link.symlink_to(real)
    IcsFile(link).create(vtodo("y"), native_id=None)
    assert link.is_symlink(), "the link is kept"
    assert b"UID:y" in real.read_bytes(), "and its target written"
    assert stat.S_IMODE(real.stat().st_mode) == 0o640, "with the mode it had"
    target = tmp_path / "elsewhere.ics"
    target.write_bytes(vtodo("linked"))
    (tmp_path / "linked.ics").symlink_to(target)
    vdir = Vdir(tmp_path)
    vdir.update("linked.ics", vtodo("linked", "changed"), etag=vdir.read("linked.ics").etag)
    assert (tmp_path / "linked.ics").is_symlink()
    assert b"changed" in target.read_bytes()


def test_a_collection_holds_at_least_one_component_and_reads_past_unknown_ones(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="component"):
        make_collection(tmp_path, "nothing", frozenset())
    odd = Vdir(make_collection(tmp_path, "odd", frozenset({Component.TASK})))
    (odd.path / ".calendaring-components").write_text("VTODO\nVAVAILABILITY\n")
    assert odd.components == frozenset({Component.TASK})


def test_a_collection_keeps_its_name_and_the_components_it_was_made_for(tmp_path: Path) -> None:
    tasks = Vdir(make_collection(tmp_path, "Things to do", frozenset({Component.TASK})))
    anything = Vdir(make_collection(tmp_path, "Things to do", frozenset(Component)))
    assert (tasks.path.name, anything.path.name) == ("Things-to-do", "Things-to-do-2")
    assert (tasks.name, tasks.components) == ("Things to do", frozenset({Component.TASK}))
    assert anything.components == frozenset(Component)
    assert sorted(p.name for p in anything.path.iterdir()) == ["displayname"], "nothing beyond the vdir spec"


def test_a_vdir_ignores_what_is_not_an_object(tmp_path: Path) -> None:
    vdir = Vdir(tmp_path)
    vdir.create(vtodo("a"), native_id=None)
    (tmp_path / "displayname").write_text("Work\n")
    (tmp_path / "color").write_text("#ff0000\n")
    (tmp_path / ".hidden.ics").write_bytes(vtodo("hidden"))
    (tmp_path / "notes.txt").write_text("not a calendar")
    (tmp_path / "sub.ics").mkdir()
    assert sorted(vdir.etags()) == ["a.ics"]
    assert (vdir.name, vdir.color) == ("Work", "#ff0000")


def test_a_vdir_create_refuses_a_taken_name_or_uid(tmp_path: Path) -> None:
    vdir = Vdir(tmp_path)
    first = vdir.create(vtodo("a"), native_id="mine.ics")
    assert first.native_id == "mine.ics"
    with pytest.raises(AlreadyExistsError) as name:
        vdir.create(vtodo("b"), native_id="mine.ics")
    assert name.value.taken == "native_id"
    with pytest.raises(AlreadyExistsError) as uid:
        vdir.create(vtodo("a"), native_id=None)
    assert uid.value.taken == "uid"
    assert sorted(vdir.etags()) == ["mine.ics"]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")], "no temporary file left behind"


def test_a_vdir_write_over_a_file_edited_behind_its_back_is_refused(tmp_path: Path) -> None:
    vdir = Vdir(tmp_path)
    stored = vdir.create(vtodo("a"), native_id=None)
    path = tmp_path / stored.native_id
    path.write_bytes(vtodo("a", "edited in an editor"))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    with pytest.raises(ConflictError):
        vdir.update(stored.native_id, vtodo("a", "mine"), etag=stored.etag)
    with pytest.raises(ConflictError):
        vdir.delete(stored.native_id, etag=stored.etag)
    assert vdir.read(stored.native_id).data == vtodo("a", "edited in an editor")
    current = vdir.read(stored.native_id)
    updated = vdir.update(stored.native_id, vtodo("a", "mine"), etag=current.etag)
    assert updated.etag != current.etag
    assert vdir.read(stored.native_id).etag == updated.etag
    vdir.delete(stored.native_id, etag=updated.etag)
    with pytest.raises(NotFoundError):
        vdir.read(stored.native_id)


def test_a_vdir_keeps_the_bytes_it_was_given(tmp_path: Path) -> None:
    data = vtodo("a", "x" * 100)  # icalendar would fold this line
    stored = Vdir(tmp_path).create(data, native_id=None)
    assert (tmp_path / stored.native_id).read_bytes() == data


def test_a_broken_file_is_listed_with_its_bytes(tmp_path: Path) -> None:
    vdir = Vdir(tmp_path)
    (tmp_path / "broken.ics").write_bytes(b"not iCalendar at all")
    vdir.create(vtodo("good"), native_id=None)
    assert {e.native_id: e.data for e in vdir.entries()}["broken.ics"] == b"not iCalendar at all"
    vdir.create(vtodo("other"), native_id=None)  # the UID check gets past it


# === single file ====================================================


def test_a_single_file_holds_one_object_per_uid(tmp_path: Path) -> None:
    path = tmp_path / "calendar.ics"
    path.write_bytes(
        (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//other//EN\r\nX-WR-CALNAME:Home\r\n"
            + OSLO
            + "BEGIN:VEVENT\r\nUID:series\r\nDTSTAMP:20261001T000000Z\r\n"
            "DTSTART;TZID=Europe/Oslo:20261105T090000\r\nRRULE:FREQ=DAILY;COUNT=3\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:series\r\nDTSTAMP:20261001T000000Z\r\nRECURRENCE-ID;TZID=Europe/Oslo:20261106T090000\r\n"
            "DTSTART;TZID=Europe/Oslo:20261106T100000\r\nEND:VEVENT\r\n"
            "BEGIN:VTODO\r\nUID:task\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:t\r\nEND:VTODO\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
    )
    ics = IcsFile(path)
    assert ics.name == "Home"
    entries = {e.native_id: e for e in ics.entries()}
    assert sorted(entries) == ["series", "task"]
    assert entries["series"].data.count(b"BEGIN:VEVENT") == 2
    assert b"BEGIN:VTIMEZONE" in entries["series"].data, "an object keeps the time zones it uses"
    assert b"BEGIN:VTIMEZONE" not in entries["task"].data
    ics.update("task", vtodo("task", "changed"), etag=entries["task"].etag)
    after = path.read_bytes()
    assert b"X-WR-CALNAME:Home" in after
    assert after.count(b"BEGIN:VTIMEZONE") == 1
    assert ics.read("series").etag == entries["series"].etag, "another object's etag is untouched"


def test_a_single_file_keeps_a_long_tzid_with_its_object_and_drops_unused_zones(tmp_path: Path) -> None:
    tzid = "/softwarestudio.org/Olson_20011030_5/America/Argentina/Buenos_Aires"
    zone = OSLO.replace("Europe/Oslo", tzid)
    path = tmp_path / "calendar.ics"
    path.write_bytes(
        (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//other//EN\r\n"
            + zone
            + OSLO
            + f"BEGIN:VEVENT\r\nUID:far\r\nDTSTAMP:20261001T000000Z\r\nDTSTART;TZID={tzid}:20261105T090000\r\n"
            "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:home\r\nDTSTAMP:20261001T000000Z\r\n"
            "DTSTART;TZID=Europe/Oslo:20261105T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        ).encode()
    )
    ics = IcsFile(path)
    assert b"Buenos_Aires" in ics.read("far").data.replace(b"\r\n ", b"")
    assert b"BEGIN:VTIMEZONE" in ics.read("far").data
    ics.delete("home", etag=None)
    assert b"Europe/Oslo" not in path.read_bytes(), "a zone nothing uses any more is dropped"


def test_a_single_file_refuses_a_stale_write(tmp_path: Path) -> None:
    ics = IcsFile(tmp_path / "calendar.ics")
    stored = ics.create(vtodo("a"), native_id=None)
    assert stored.native_id == "a"
    with pytest.raises(AlreadyExistsError) as uid:
        ics.create(vtodo("a"), native_id=None)
    assert uid.value.taken == "uid"
    IcsFile(tmp_path / "calendar.ics").update("a", vtodo("a", "elsewhere"), etag=stored.etag)
    with pytest.raises(ConflictError):
        ics.update("a", vtodo("a", "mine"), etag=stored.etag)
    with pytest.raises(NotFoundError):
        ics.delete("gone", etag=None)


def test_a_single_file_that_does_not_parse_is_one_broken_entry(tmp_path: Path) -> None:
    path = tmp_path / "calendar.ics"
    path.write_bytes(b"BEGIN:VCALENDAR\r\nthis is not\r\n")
    [entry] = IcsFile(path).entries()
    assert entry.data == b"BEGIN:VCALENDAR\r\nthis is not\r\n"


def test_concurrent_writers_to_a_single_file_lose_nothing(tmp_path: Path) -> None:
    path = tmp_path / "calendar.ics"
    errors: list[BaseException] = []

    def add(n: int) -> None:
        try:
            IcsFile(path).create(vtodo(f"task-{n}"), native_id=None)
        except BaseException as exc:  # noqa: BLE001  reported below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(IcsFile(path).entries()) == 20
